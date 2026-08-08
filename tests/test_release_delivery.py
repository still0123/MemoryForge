from __future__ import annotations

import importlib.util
import io
import tarfile
import tomllib
from pathlib import Path

import pytest


def _module(name: str, relative_path: str):
    path = Path(__file__).resolve().parent.parent / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


summary_builder = _module("build_benchmark_summary", "demo/build_benchmark_summary.py")
workspace_drill = _module("run_release_workspace_drill", "demo/run_release_workspace_drill.py")
release_builder = _module("build_release", "scripts/build_release.py")


def test_release_builder_reads_version_from_current_source() -> None:
    assert release_builder._source_cli_version() == "0.3.0"


def test_release_package_inputs_and_build_environment_are_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["readme"] == "PACKAGE_README.md"
    assert project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"] == [
        "/PACKAGE_README.md",
        "/pyproject.toml",
        "/src",
    ]
    monkeypatch.setenv("PYTHONPATH", "/private/tmp/untrusted")
    monkeypatch.setenv("PYTHONHOME", "/private/tmp/untrusted")
    environment = release_builder._clean_build_environment()
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["SOURCE_DATE_EPOCH"] == "315532800"


def test_benchmark_summary_reports_macro_per_suite_and_negatives() -> None:
    summary = summary_builder.build_summary(memoryforge_commit="1" * 40)

    assert summary["memoryforge_commit"] == "1" * 40
    assert summary["registry"]["qa_case_count"] == 121
    assert len(summary["suites"]) == 12
    release = next(
        experiment
        for experiment in summary["experiments"]
        if experiment["suite_id"] == "release-candidate-delivery"
    )
    assert release["accepted_evidence"] == []
    assert summary["macro"]["citation_grounding_accuracy"] > 90
    assert any(
        result["suite_id"] == "doc-wiki-qa.click" and result["status"] == "retained_metric_gap"
        for result in summary["negative_results"]
    )
    assert any(
        result["suite_id"] == "release-candidate-delivery"
        and result["status"] == "regression_rejected"
        and result["path"]
        == "demo/results/release_candidate_candidate_7_local_gate_contract_rejected.json"
        for result in summary["negative_results"]
    )


def test_workspace_release_drill_runs_real_public_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "drill"
    workdir.mkdir()
    commit = workspace_drill._git_output(workspace_drill.REPO_ROOT, "rev-parse", "HEAD")
    monkeypatch.setattr(workspace_drill, "_source_identity", lambda: (commit, False))

    evidence = workspace_drill.run_drill(workdir)

    assert evidence["private_detail_leaks"] == 0
    assert evidence["passed"] is True
    assert set(evidence["checks"]) == {
        "refresh",
        "review",
        "approve",
        "apply",
        "lint",
        "no_pending_ingest",
        "backup",
        "restore",
        "query",
        "showcase",
    }


def test_workspace_release_drill_binds_cli_to_current_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        return workspace_drill.subprocess.CompletedProcess(command, 0, "0.3.0\n", "")

    monkeypatch.setattr(workspace_drill.subprocess, "run", run)

    assert workspace_drill._cli("--version") == "0.3.0\n"
    assert captured["env"]["PYTHONPATH"] == str(workspace_drill.SOURCE_ROOT)


def test_workspace_release_drill_rejects_dirty_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_drill, "_source_identity", lambda: ("1" * 40, True))

    with pytest.raises(RuntimeError, match="clean source worktree"):
        workspace_drill.run_drill(tmp_path)


def test_workspace_release_drill_measures_showcase_privacy(tmp_path: Path) -> None:
    showcase = tmp_path / "showcase"
    showcase.mkdir()
    (showcase / "index.html").write_text(
        "<p>/Users/private/workspace</p><p>token=private</p>",
        encoding="utf-8",
    )

    assert workspace_drill._showcase_private_detail_leaks(showcase) == 2
    (showcase / "showcase.json").write_text(
        '{"path": "C:\\\\Users\\\\private\\\\workspace"}',
        encoding="utf-8",
    )
    assert workspace_drill._showcase_private_detail_leaks(showcase) == 3


def test_release_builder_rejects_nested_artifacts(tmp_path: Path) -> None:
    clean = tmp_path / "clean.tar.gz"
    dirty = tmp_path / "dirty.tar.gz"
    _tar(clean, "memoryforge-0.3.0/pyproject.toml")
    _tar(
        dirty,
        "memoryforge-0.3.0/demo/results/artifacts/candidate/memoryforge.whl",
    )

    release_builder._validate_sdist(clean)
    with pytest.raises(SystemExit, match="retained or nested artifacts"):
        release_builder._validate_sdist(dirty)


def _tar(path: Path, name: str) -> None:
    payload = b"release fixture"
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))
