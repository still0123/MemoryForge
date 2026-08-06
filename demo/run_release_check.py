#!/usr/bin/env python3
"""Verify a MemoryForge wheel in a fresh venv and write release provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import venv
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PINNED_PUBLIC_COMMIT = "93f5dc05229da250b041850ad8deeeec886ef304"
PINNED_PUBLIC_REPOSITORY_ID = "db29dfd06f0a2853c9764f1393b680d6a592b1a5771a012b6e1387fc17aac597"
CODE_METRICS = (
    "expected_source_coverage",
    "symbol_recall",
    "core_relation_recall",
    "known_gap_relation_recall",
    "overall_relation_recall",
    "module_assignment_accuracy",
    "citation_grounding_accuracy",
    "architecture_edge_grounding",
    "mermaid_edge_coverage",
    "architecture_citation_coverage",
    "architecture_mermaid_determinism",
    "deterministic_replay",
)
PUBLIC_METRICS = {
    "answer_accuracy": 96.7,
    "source_recall_at_3": 96.2,
    "citation_grounding_accuracy": 100.0,
    "multi_source_coverage": 100.0,
    "abstention_accuracy": 100.0,
}


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    wheel = args.wheel.resolve()
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    public_source = args.public_source_repo.resolve() if args.public_source_repo else None
    _validate_inputs(wheel, workdir, public_source)

    env_dir = workdir / ".venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONNOUSERSITE"] = "1"

    _run(
        python,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        str(wheel),
        cwd=workdir,
        env=env,
    )
    _run(python, "-m", "pip", "check", cwd=workdir, env=env)
    probe = json.loads(
        _run(
            python,
            "-c",
            _probe_script(),
            cwd=workdir,
            env=env,
        )
    )
    import_path = Path(probe.pop("import_path")).resolve()
    if not import_path.is_relative_to(env_dir) or import_path.is_relative_to(REPO_ROOT / "src"):
        raise SystemExit(f"clean-room import escaped the fresh venv: {import_path}")
    probe["import_path"] = import_path.relative_to(workdir).as_posix()
    _run(python, "-m", "memoryforge", "--help", cwd=workdir, env=env)

    code_output = workdir / "code_wiki_evidence.json"
    _run(
        python,
        str(REPO_ROOT / "demo/run_code_wiki_benchmark.py"),
        "--workdir",
        str(workdir / "code-wiki"),
        "--output",
        str(code_output),
        cwd=workdir,
        env=env,
    )
    code_evidence = json.loads(code_output.read_text(encoding="utf-8"))
    _validate_code_evidence(code_evidence)

    public_evidence = None
    if public_source is not None:
        public_output = workdir / "public_evidence.json"
        _run(
            python,
            str(REPO_ROOT / "demo/run_public_demo.py"),
            "--source-repo",
            str(public_source),
            "--workspace",
            str(workdir / "public-workspace"),
            "--output",
            str(public_output),
            cwd=workdir,
            env=env,
        )
        public_evidence = json.loads(public_output.read_text(encoding="utf-8"))
        _validate_public_evidence(public_evidence)

    commands = [
        "python -m pip install <wheel>",
        "python -m pip check",
        "python -m memoryforge --help",
        "python demo/run_code_wiki_benchmark.py",
    ]
    if public_evidence is not None:
        commands.append("python demo/run_public_demo.py")
    provenance = {
        "schema_version": 1,
        "memoryforge_commit": _git_output(REPO_ROOT, "rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git_output(REPO_ROOT, "status", "--porcelain")),
        "package": {
            "version": probe["packages"]["memoryforge"],
            "wheel": wheel.name,
            "wheel_sha256": _sha256(wheel),
            "import_path": probe["import_path"],
            "import_from_fresh_venv": True,
            "dependencies": {
                name: version
                for name, version in probe["packages"].items()
                if name != "memoryforge"
            },
        },
        "runtime": {
            "implementation": probe["implementation"],
            "python": probe["python"],
            "platform": platform.platform(),
        },
        "commands": commands,
        "checks": {
            "pip_check": "passed",
            "cli_help": "passed",
            "code_wiki_benchmark": "passed",
            "public_demo": "passed" if public_evidence is not None else "not_run",
        },
        "code_wiki": {
            "evidence_sha256": _sha256(code_output),
            "metrics": code_evidence["evaluation"]["metrics"],
            "gates": code_evidence["evaluation"]["gates"],
            "incremental": code_evidence["incremental"],
        },
        "public_demo": (
            {
                "evidence_sha256": _sha256(workdir / "public_evidence.json"),
                "source_repositories": public_evidence["source_repositories"],
                "workflow": public_evidence["workflow"],
                "metrics": public_evidence["evaluation"]["memoryforge"],
                "raw_fts_baseline": public_evidence["evaluation"]["raw_fts_baseline"],
            }
            if public_evidence is not None
            else {
                "status": "not_run",
                "required_commit": PINNED_PUBLIC_COMMIT,
            }
        ),
    }
    if args.code_evidence_output:
        _publish(code_output, args.code_evidence_output.resolve())
    if args.public_evidence_output:
        if public_evidence is None:
            raise SystemExit("--public-evidence-output requires --public-source-repo")
        _publish(workdir / "public_evidence.json", args.public_evidence_output.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote release provenance to {output}")


def _validate_inputs(
    wheel: Path,
    workdir: Path,
    public_source: Path | None,
) -> None:
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise SystemExit(f"--wheel must be an existing .whl: {wheel}")
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    workdir.mkdir(parents=True, exist_ok=True)
    if public_source is None:
        return
    if not (public_source / ".git").exists():
        raise SystemExit(f"--public-source-repo must be a Git checkout: {public_source}")
    commit = _git_output(public_source, "rev-parse", "HEAD")
    if commit != PINNED_PUBLIC_COMMIT:
        raise SystemExit(
            f"public source must be AgentSkill-Eval@{PINNED_PUBLIC_COMMIT}, got {commit}"
        )


def _validate_code_evidence(evidence: dict[str, Any]) -> None:
    metrics = evidence["evaluation"]["metrics"]
    failed = [name for name in CODE_METRICS if metrics.get(name) != 100.0]
    if failed:
        raise SystemExit(f"code Wiki release metrics failed: {', '.join(failed)}")
    if not all(evidence["evaluation"]["gates"].values()):
        raise SystemExit("code Wiki release gate failed")
    incremental = evidence["incremental"]
    if not incremental["passed"] or incremental["changed_page_ratio"] > 0.25:
        raise SystemExit("code Wiki incremental release gate failed")


def _validate_public_evidence(evidence: dict[str, Any]) -> None:
    repositories = evidence["source_repositories"]
    if (
        len(repositories) != 1
        or repositories[0]["commit"] != PINNED_PUBLIC_COMMIT
        or repositories[0]["repository_id"] != PINNED_PUBLIC_REPOSITORY_ID
    ):
        raise SystemExit("public Demo used an unexpected source identity")
    metrics = evidence["evaluation"]["memoryforge"]
    failed = [name for name, expected in PUBLIC_METRICS.items() if metrics.get(name) != expected]
    if failed:
        raise SystemExit(f"public Demo release metrics failed: {', '.join(failed)}")
    if evidence["workflow"]["lint"]["status"] != "clean":
        raise SystemExit("public Demo lint gate failed")


def _probe_script() -> str:
    packages = (
        "memoryforge",
        "pydantic",
        "tree-sitter",
        "tree-sitter-go",
        "tree-sitter-python",
        "tree-sitter-typescript",
        "typer",
    )
    return (
        "import importlib.metadata,json,memoryforge,platform;"
        f"names={packages!r};"
        "print(json.dumps({'implementation':platform.python_implementation(),"
        "'python':platform.python_version(),'import_path':memoryforge.__file__,"
        "'packages':{name:importlib.metadata.version(name) for name in names}}))"
    )


def _run(
    *command: str | Path,
    cwd: Path,
    env: dict[str, str],
) -> str:
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        rendered = " ".join(str(part) for part in command)
        raise SystemExit(
            f"release command failed ({completed.returncode}): {rendered}\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _publish(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-source-repo", type=Path)
    parser.add_argument("--code-evidence-output", type=Path)
    parser.add_argument("--public-evidence-output", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
