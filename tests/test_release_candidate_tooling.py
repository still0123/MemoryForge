from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "demo/run_release_candidate_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("run_release_candidate_benchmark", _SCRIPT)
assert _SPEC and _SPEC.loader
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def test_release_candidate_runner_binds_frozen_inputs() -> None:
    development, confirmation, holdout = benchmark._load_frozen_inputs()

    assert development["split"] == "development"
    assert confirmation["status"] == "not_run"
    assert holdout["status"] == "not_run"
    assert [case["id"] for case in development["cases"]] == [
        "package-version-consistency",
        "registry-and-benchmark-summary",
        "local-reproducible-artifacts",
        "workspace-release-drill",
        "release-document-consistency",
        "frozen-splits-remain-closed",
    ]


def test_release_candidate_runner_refuses_existing_output_before_work(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    output = tmp_path / "evidence.json"
    output.write_text("keep\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="--output already exists"):
        benchmark.main(
            [
                "--release-dir",
                str(release_dir),
                "--output",
                str(output),
            ]
        )

    assert output.read_text(encoding="utf-8") == "keep\n"
    assert not release_dir.exists()


def test_release_candidate_version_probes_use_clean_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHOME", "/private/tmp/untrusted")
    monkeypatch.setenv("PYTHONPATH", "/private/tmp/untrusted")
    monkeypatch.setenv("COV_CORE_DATAFILE", ".coverage")
    monkeypatch.setenv("COVERAGE_FILE", ".coverage")

    environment = benchmark._clean_python_environment()

    assert environment["PYTHONPATH"] == str(benchmark.SOURCE_ROOT)
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONHOME" not in environment
    assert "COV_CORE_DATAFILE" not in environment
    assert "COVERAGE_FILE" not in environment


def test_release_candidate_reproducibility_requires_two_matching_builds(
    tmp_path: Path,
) -> None:
    provenance = _release_artifacts(tmp_path)
    _write_release_artifacts(tmp_path, provenance)

    assert benchmark._check_reproducible_artifacts(tmp_path)["passed"] is True
    provenance["builds"][1]["sdist"]["sha256"] = "3" * 64
    _write_release_artifacts(tmp_path, provenance)
    assert benchmark._check_reproducible_artifacts(tmp_path) == {
        "passed": False,
        "error_classification": "artifact_reproducibility_failure",
    }
    (tmp_path / provenance["package"]["wheel"]).unlink()
    assert benchmark._check_reproducible_artifacts(tmp_path) == {
        "passed": False,
        "error_classification": "artifact_missing",
    }


def test_release_candidate_versions_read_both_artifact_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nversion = "0.3.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmark, "REPO_ROOT", project)
    monkeypatch.setattr(benchmark, "_source_module_version", lambda: "0.3.0")
    monkeypatch.setattr(benchmark, "_cli_version", lambda: "0.3.0")
    monkeypatch.setattr(benchmark, "_git", lambda *_args: "1" * 40)
    provenance = _release_artifacts(tmp_path)
    path = tmp_path / "release-provenance.json"
    _write_release_artifacts(tmp_path, provenance)

    assert benchmark._check_versions(tmp_path)["passed"] is True
    provenance["package"] = []
    path.write_text(json.dumps(provenance), encoding="utf-8")
    assert benchmark._check_versions(tmp_path) == {
        "passed": False,
        "error_classification": "version_mismatch",
    }


def test_release_candidate_consumes_workspace_drill_schema_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "bced2a660ef38dfc4c0c6a0f994897d6af895574"
    source = (
        benchmark.REPO_ROOT
        / "demo/results/artifacts/release_candidate_development"
        / commit
        / "workspace-drill.json"
    )
    drill = json.loads(source.read_text(encoding="utf-8"))
    (tmp_path / "workspace-drill.json").write_text(
        json.dumps(drill),
        encoding="utf-8",
    )
    monkeypatch.setattr(benchmark, "_git", lambda *_args: commit)

    assert benchmark._check_workspace_drill(tmp_path)["passed"] is False
    drill["fixture"] = benchmark.registry_validator.RELEASE_DRILL_FIXTURE
    drill["workspace"]["original_commit"] = (
        benchmark.registry_validator.RELEASE_DRILL_WORKSPACE_COMMIT
    )
    drill["workspace"]["restored_commit"] = (
        benchmark.registry_validator.RELEASE_DRILL_WORKSPACE_COMMIT
    )
    (tmp_path / "workspace-drill.json").write_text(
        json.dumps(drill),
        encoding="utf-8",
    )
    assert benchmark._check_workspace_drill(tmp_path)["passed"] is True
    drill["evaluation"]["metrics"]["answer_accuracy"] = 0.0
    (tmp_path / "workspace-drill.json").write_text(
        json.dumps(drill),
        encoding="utf-8",
    )
    assert benchmark._check_workspace_drill(tmp_path)["passed"] is False


def test_release_candidate_rejects_incomplete_release_contract(tmp_path: Path) -> None:
    provenance = _release_artifacts(tmp_path)
    _write_release_artifacts(tmp_path, provenance)
    (tmp_path / "SHA256SUMS").write_text("0" * 64 + "  ignored\n", encoding="ascii")

    assert benchmark._check_reproducible_artifacts(tmp_path) == {
        "passed": False,
        "error_classification": "artifact_contract_invalid",
    }
    assert benchmark._release_artifact_evidence(tmp_path, "1" * 40)["sha256sums_sha256"]
    (tmp_path / "SHA256SUMS").unlink()
    assert benchmark._release_artifact_evidence(tmp_path, "1" * 40)["sha256sums_sha256"] == ""


def test_release_candidate_rejects_unlisted_files_and_nested_output(tmp_path: Path) -> None:
    provenance = _release_artifacts(tmp_path)
    _write_release_artifacts(tmp_path, provenance)
    (tmp_path / "unlisted-private.txt").write_text("private", encoding="utf-8")

    assert benchmark._check_reproducible_artifacts(tmp_path)["passed"] is False
    with pytest.raises(SystemExit, match="outside --release-dir"):
        benchmark.main(
            [
                "--release-dir",
                str(tmp_path),
                "--output",
                str(tmp_path / "evidence.json"),
            ]
        )


def test_release_candidate_rejects_directories_and_symlinks(tmp_path: Path) -> None:
    provenance = _release_artifacts(tmp_path)
    _write_release_artifacts(tmp_path, provenance)
    nested = tmp_path / "unlisted"
    nested.mkdir()
    assert benchmark._check_reproducible_artifacts(tmp_path)["passed"] is False
    nested.rmdir()

    link = tmp_path / "artifact-link"
    try:
        link.symlink_to(tmp_path / provenance["package"]["wheel"])
    except OSError:
        pytest.skip("symlink creation is unavailable")
    assert benchmark._check_reproducible_artifacts(tmp_path)["passed"] is False


def test_release_candidate_rejects_retained_path_alias(tmp_path: Path) -> None:
    provenance = _release_artifacts(tmp_path)
    provenance["builds"][1]["wheel"]["retained_path"] = (
        "reproducibility/second/../first/memoryforge-0.3.0-py3-none-any.whl"
    )
    _write_release_artifacts(tmp_path, provenance)

    assert benchmark._check_reproducible_artifacts(tmp_path)["passed"] is False


def test_release_candidate_rejects_wrong_package_name(tmp_path: Path) -> None:
    provenance = _release_artifacts(tmp_path)
    _write_release_artifacts(tmp_path, provenance)
    wheel = tmp_path / provenance["package"]["wheel"]
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "memoryforge-0.3.0.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: other\nVersion: 0.3.0\n",
        )

    assert benchmark._check_versions(tmp_path)["passed"] is False


def test_release_candidate_rejects_incomplete_provenance_schema(tmp_path: Path) -> None:
    provenance = _release_artifacts(tmp_path)
    provenance.pop("runtime")
    _write_release_artifacts(tmp_path, provenance)

    assert benchmark._check_versions(tmp_path)["passed"] is False


def test_release_candidate_summary_requires_all_registered_content(tmp_path: Path) -> None:
    summary = benchmark.summary_builder.build_summary(
        memoryforge_commit=benchmark._git("rev-parse", "HEAD"),
    )
    (tmp_path / "benchmark-summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )
    assert benchmark._check_benchmark_summary(tmp_path)["passed"] is True
    summary["negative_results"] = []
    (tmp_path / "benchmark-summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )
    assert benchmark._check_benchmark_summary(tmp_path)["passed"] is False


def test_release_candidate_privacy_scan_rejects_paths_and_secrets() -> None:
    assert benchmark._private_detail_leaks([{"path": "dist/memoryforge.whl"}]) == 0
    assert (
        benchmark._private_detail_leaks(
            [
                {"path": "prefix:/Users/private/workspace"},
                {"detail": "api_key=not-public"},
            ]
        )
        == 2
    )
    assert benchmark._private_detail_leaks([{"api_key": "sk-live-private-value"}]) == 2
    assert benchmark._private_detail_leaks([{"path": "/private/tmp/private-workspace"}]) == 1


def test_release_document_claims_are_explicit() -> None:
    for path, claims in benchmark.DOCUMENT_CLAIMS.items():
        text = path.read_text(encoding="utf-8")
        assert all(claim in text for claim in claims)
        assert text.count(benchmark.RELEASE_CLAIM_MARKER) == 1
        assert not any(claim in text for claim in benchmark.FORBIDDEN_RELEASE_CLAIMS)
    texts = {path: path.read_text(encoding="utf-8") for path in benchmark.DOCUMENTS}
    texts[benchmark.DOCUMENTS[2]] += (
        "\nHistorical gate: macOS 559 passed, Linux 556 passed / 3 skipped.\n"
    )
    assert benchmark._document_claims_consistent(texts)
    texts[benchmark.DOCUMENTS[0]] += "\nv0.3.0 confirmation succeeded.\n"
    assert not benchmark._document_claims_consistent(texts)
    texts[benchmark.DOCUMENTS[0]] = benchmark.DOCUMENTS[0].read_text(encoding="utf-8")
    texts[benchmark.DOCUMENTS[0]] += "\nConfirmation status: `passed`\n"
    assert not benchmark._document_claims_consistent(texts)


def test_release_clean_room_checks_cannot_be_not_run() -> None:
    assert benchmark._clean_room_passed(
        benchmark.WHEEL_CLEAN_ROOM_CHECKS,
        benchmark.WHEEL_CLEAN_ROOM_CHECKS,
    )
    assert not benchmark._clean_room_passed(
        {key: "not_run" for key in benchmark.SDIST_CLEAN_ROOM_CHECKS},
        benchmark.SDIST_CLEAN_ROOM_CHECKS,
    )


def _release_artifacts(root: Path) -> dict:
    wheel = root / "memoryforge-0.3.0-py3-none-any.whl"
    sdist = root / "memoryforge-0.3.0.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "memoryforge-0.3.0.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: memoryforge\nVersion: 0.3.0\n",
        )
    payload = b"Metadata-Version: 2.3\nName: memoryforge\nVersion: 0.3.0\n"
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("memoryforge-0.3.0/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    artifacts = {
        "wheel": {
            "path": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "size": wheel.stat().st_size,
        },
        "sdist": {
            "path": sdist.name,
            "sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
            "size": sdist.stat().st_size,
        },
    }
    builds = []
    for name in ("first", "second"):
        retained_wheel = root / f"reproducibility-{name}-{wheel.name}"
        retained_sdist = root / f"reproducibility-{name}-{sdist.name}"
        retained_wheel.write_bytes(wheel.read_bytes())
        retained_sdist.write_bytes(sdist.read_bytes())
        builds.append(
            {
                "name": name,
                "wheel": {
                    **artifacts["wheel"],
                    "retained_path": retained_wheel.relative_to(root).as_posix(),
                },
                "sdist": {
                    **artifacts["sdist"],
                    "retained_path": retained_sdist.relative_to(root).as_posix(),
                },
            }
        )
    return {
        "schema_version": 1,
        "memoryforge_commit": benchmark._git("rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": False,
        "package": {
            "version": "0.3.0",
            "wheel": wheel.name,
            "wheel_sha256": artifacts["wheel"]["sha256"],
            "sdist": sdist.name,
            "sdist_sha256": artifacts["sdist"]["sha256"],
        },
        "builds": builds,
        "reproducible_artifacts": True,
        "runtime": {
            "implementation": "CPython",
            "python": "3.11.15",
            "system": "Darwin",
            "machine": "arm64",
        },
        "checks": {
            "wheel_clean_room": dict(benchmark.WHEEL_CLEAN_ROOM_CHECKS),
            "sdist_clean_room": dict(benchmark.SDIST_CLEAN_ROOM_CHECKS),
            "workspace_drill": "passed",
            "benchmark_summary": "passed",
            "sdist_members": "passed",
        },
        "dependencies": {"pydantic": "2.13.4"},
        "confirmation": {"status": "not_run"},
        "holdout": {"status": "not_run"},
    }


def _write_release_artifacts(root: Path, provenance: dict) -> None:
    (root / "release-provenance.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    (root / "benchmark-summary.json").write_text("{}\n", encoding="utf-8")
    (root / "workspace-drill.json").write_text("{}\n", encoding="utf-8")
    paths = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
            f"{path.relative_to(root).as_posix()}\n"
            for path in paths
        ),
        encoding="ascii",
    )
