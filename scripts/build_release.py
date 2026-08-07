#!/usr/bin/env python3
"""Build reproducible local release artifacts and public provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src"
TARGET_VERSION = "0.3.0"
CONSTRAINTS = REPO_ROOT / "constraints/dev.txt"
FORBIDDEN_SDIST_PARTS = ("/demo/results/artifacts/",)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    output = args.output.resolve()
    if output.is_relative_to(REPO_ROOT):
        raise SystemExit("--output must remain outside the repository")
    if output.exists():
        raise SystemExit(f"--output already exists: {output}")
    if platform.python_implementation() != "CPython" or sys.version_info[:2] != (3, 11):
        raise SystemExit("release build requires CPython 3.11")
    commit = _git("rev-parse", "HEAD")
    if _git("status", "--porcelain"):
        raise SystemExit("MemoryForge worktree must be clean")
    _require_version()

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}-",
        dir=output.parent,
    ) as staging_parent:
        staging = Path(staging_parent) / "release"
        staging.mkdir()
        with tempfile.TemporaryDirectory(prefix="memoryforge-release-build-") as temporary:
            workdir = Path(temporary)
            builds = [_isolated_build(workdir / name, name=name) for name in ("first", "second")]
            if builds[0]["wheel"]["sha256"] != builds[1]["wheel"]["sha256"]:
                raise SystemExit("Wheel builds are not byte-identical")
            if builds[0]["sdist"]["sha256"] != builds[1]["sdist"]["sha256"]:
                raise SystemExit("sdist builds are not byte-identical")

            first_root = workdir / "first/dist"
            wheel_name = str(builds[0]["wheel"]["path"])
            sdist_name = str(builds[0]["sdist"]["path"])
            wheel = staging / wheel_name
            sdist = staging / sdist_name
            shutil.copy2(first_root / wheel_name, wheel)
            shutil.copy2(first_root / sdist_name, sdist)
            for build in builds:
                for kind in ("wheel", "sdist"):
                    name = str(build[kind]["path"])
                    retained_artifact = staging / f"reproducibility-{build['name']}-{name}"
                    shutil.copy2(workdir / str(build["name"]) / "dist" / name, retained_artifact)
                    build[kind]["retained_path"] = retained_artifact.name

            wheel_check = workdir / "wheel-check.json"
            _run(
                [
                    sys.executable,
                    str(REPO_ROOT / "demo/run_release_check.py"),
                    "--wheel",
                    str(wheel),
                    "--workdir",
                    str(workdir / "wheel-check"),
                    "--output",
                    str(wheel_check),
                ],
                environment={**os.environ, "PIP_CONSTRAINT": str(CONSTRAINTS)},
            )
            wheel_check_payload = _read_json(wheel_check)
            sdist_check = _check_sdist_clean_room(sdist, workdir / "sdist-check")

            _run(
                [
                    sys.executable,
                    str(REPO_ROOT / "demo/build_benchmark_summary.py"),
                    "--output",
                    str(staging / "benchmark-summary.json"),
                ]
            )
            _run(
                [
                    sys.executable,
                    str(REPO_ROOT / "demo/run_release_workspace_drill.py"),
                    "--workdir",
                    str(workdir / "workspace-drill"),
                    "--output",
                    str(staging / "workspace-drill.json"),
                ]
            )

            provenance = {
                "schema_version": 1,
                "memoryforge_commit": commit,
                "memoryforge_worktree_dirty": False,
                "package": {
                    "version": TARGET_VERSION,
                    "wheel": wheel.name,
                    "wheel_sha256": _sha256(wheel),
                    "sdist": sdist.name,
                    "sdist_sha256": _sha256(sdist),
                },
                "builds": builds,
                "reproducible_artifacts": True,
                "runtime": {
                    "implementation": platform.python_implementation(),
                    "python": platform.python_version(),
                    "system": platform.system(),
                    "machine": platform.machine(),
                },
                "checks": {
                    "wheel_clean_room": wheel_check_payload["checks"],
                    "sdist_clean_room": sdist_check,
                    "workspace_drill": "passed",
                    "benchmark_summary": "passed",
                    "sdist_members": "passed",
                },
                "dependencies": wheel_check_payload["package"]["dependencies"],
                "confirmation": {"status": "not_run"},
                "holdout": {"status": "not_run"},
            }
            (staging / "release-provenance.json").write_text(
                json.dumps(provenance, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _write_sha256sums(staging)

        if _git("rev-parse", "HEAD") != commit or _git("status", "--porcelain"):
            raise SystemExit("release build changed the source Commit or worktree")
        staging.replace(output)
    print(f"Release artifacts built at {output}")


def _isolated_build(root: Path, *, name: str) -> dict[str, Any]:
    environment = root / "environment"
    destination = root / "dist"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    if uv:
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "-c",
                str(CONSTRAINTS),
                "build",
                "hatchling",
            ]
        )
    else:
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "-c",
                str(CONSTRAINTS),
                "build",
                "hatchling",
            ]
        )
    _run(
        [
            str(python),
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--no-isolation",
            "--outdir",
            str(destination),
        ]
    )
    wheel = _single(destination, "memoryforge-*.whl")
    sdist = _single(destination, "memoryforge-*.tar.gz")
    _validate_sdist(sdist)
    return {
        "name": name,
        "wheel": {
            "path": wheel.name,
            "sha256": _sha256(wheel),
            "size": wheel.stat().st_size,
        },
        "sdist": {
            "path": sdist.name,
            "sha256": _sha256(sdist),
            "size": sdist.stat().st_size,
        },
    }


def _validate_sdist(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
    except (OSError, tarfile.TarError) as exc:
        raise SystemExit("sdist is not a valid gzip tar archive") from exc
    forbidden = [
        name
        for name in names
        if any(part in f"/{name}" for part in FORBIDDEN_SDIST_PARTS)
        or name.endswith((".whl", ".tar.gz"))
    ]
    if forbidden:
        raise SystemExit(f"sdist contains retained or nested artifacts: {forbidden[:3]}")


def _check_sdist_clean_room(sdist: Path, root: Path) -> dict[str, str]:
    environment = (root / "environment").resolve()
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    clean_environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    clean_environment["PIP_CONSTRAINT"] = str(CONSTRAINTS)
    clean_environment["PYTHONNOUSERSITE"] = "1"
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "hatchling",
        ],
        environment=clean_environment,
    )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-build-isolation",
            str(sdist),
        ],
        environment=clean_environment,
    )
    _run(
        [str(python), "-I", "-m", "pip", "check"],
        environment=clean_environment,
    )
    probe = json.loads(
        _run(
            [
                str(python),
                "-I",
                "-c",
                (
                    "import importlib.metadata,json,memoryforge;"
                    "print(json.dumps({'version':importlib.metadata.version('memoryforge'),"
                    "'import_path':memoryforge.__file__}))"
                ),
            ],
            environment=clean_environment,
        )
    )
    import_path = Path(str(probe.get("import_path", ""))).resolve()
    if probe.get("version") != TARGET_VERSION or not import_path.is_relative_to(environment):
        raise SystemExit(
            "sdist clean-room import escaped or reported the wrong version: "
            f"version={probe.get('version')!r}, import_path={import_path}"
        )
    if (
        _run(
            [str(python), "-I", "-m", "memoryforge", "--version"],
            environment=clean_environment,
        ).strip()
        != TARGET_VERSION
    ):
        raise SystemExit("sdist clean-room CLI version mismatch")
    return {
        "install": "passed",
        "pip_check": "passed",
        "import": "passed",
        "cli_version": "passed",
    }


def _require_version() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    versions = {
        str(project["project"]["version"]),
        _source_cli_version(),
    }
    if versions != {TARGET_VERSION}:
        raise SystemExit(f"release version must be {TARGET_VERSION}: {sorted(versions)}")


def _source_cli_version() -> str:
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "memoryforge", "--version"],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _single(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"expected one {pattern} artifact, found {len(matches)}")
    return matches[0]


def _write_sha256sums(root: Path) -> None:
    paths = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    lines = [f"{_sha256(path)}  {path.relative_to(root).as_posix()}" for path in paths]
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
    for line in lines:
        digest, name = line.split("  ", 1)
        if _sha256(root / name) != digest:
            raise SystemExit(f"SHA256 verification failed: {name}")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object: {path.name}")
    return payload


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"release command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout.strip()}\n{completed.stderr.strip()}"
        )
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
