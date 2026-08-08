from __future__ import annotations

import importlib.util
import io
import json
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
    assert release_builder._source_module_version() == "0.3.0"


def test_release_package_inputs_and_build_environment_are_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["readme"] == "PACKAGE_README.md"
    assert project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"] == [
        "/PACKAGE_README.md",
        "/pyproject.toml",
        "/src",
    ]
    assert (root / ".gitattributes").read_text(encoding="utf-8") == "* text=auto eol=lf\n"
    assert release_builder.WORKTREE_CHECKOUT_OPTIONS == (
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.eol=lf",
    )
    monkeypatch.setenv("PYTHONPATH", "/private/tmp/untrusted")
    monkeypatch.setenv("PYTHONHOME", "/private/tmp/untrusted")
    monkeypatch.setenv("COV_CORE_DATAFILE", ".coverage")
    monkeypatch.setenv("COVERAGE_FILE", ".coverage")
    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("UV_INDEX_URL", "https://example.invalid/simple")
    environment = release_builder._clean_build_environment()
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert "COV_CORE_DATAFILE" not in environment
    assert "COVERAGE_FILE" not in environment
    assert environment["PIP_INDEX_URL"] == release_builder.PACKAGE_INDEX_URL
    assert environment["UV_DEFAULT_INDEX"] == release_builder.PACKAGE_INDEX_URL
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["SOURCE_DATE_EPOCH"] == "315532800"


def test_benchmark_summary_reports_macro_per_suite_and_negatives() -> None:
    commit = summary_builder._git("rev-parse", "HEAD")
    summary = summary_builder.build_summary(memoryforge_commit=commit)

    assert summary["schema_version"] == 3
    assert summary["memoryforge_commit"] == commit
    assert summary["registry"]["qa_case_count"] == 121
    assert len(summary["suites"]) == 12
    release = next(
        experiment
        for experiment in summary["experiments"]
        if experiment["suite_id"] == "release-candidate-delivery"
    )
    assert release["accepted_evidence"] == []
    assert release["development"]["sha256"] == (
        "8d9fe33359b71ac0b86b6fa42b0bc5ee34080126af3ae0b9bf2fa2c0121cf2a6"
    )
    assert release["repositories"][0]["commit"] == ("4d834679ec61355e285fb36a0cceef8f489a9083")
    assert release["evidence"][-1]["status"] == "rejected"
    assert all(
        set(accepted) == {"status", "development", "acceptance"}
        and len(accepted["development"]["memoryforge_commit"]) == 40
        and len(accepted["acceptance"]["memoryforge_commit"]) == 40
        for experiment in summary["experiments"]
        for accepted in experiment["accepted_evidence"]
    )
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
    assert any(
        result["status"] == "review_rejected"
        and result["path"]
        == "demo/results/release_candidate_candidate_7_static_review_rejected.json"
        and result["memoryforge_commit"] == "a044337347b9c6884ea660c7568c4e3911c84521"
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

    assert evidence["schema_version"] == 2
    assert evidence["private_detail_leaks"] == 0
    assert evidence["passed"] is True
    assert (
        evidence["queries"]["answered"]["original"] == evidence["queries"]["answered"]["restored"]
    )
    assert evidence["queries"]["unknown"]["original"] == evidence["queries"]["unknown"]["restored"]
    assert evidence["evaluation"]["metrics"] == {
        "answer_accuracy": 100.0,
        "citation_grounding_accuracy": 100.0,
        "abstention_accuracy": 100.0,
    }
    assert evidence["workspace"]["original_commit"] == evidence["workspace"]["restored_commit"]
    assert evidence["workspace"]["restored_lint"]["status"] == "clean"
    assert summary_builder.validator._release_drill_contract(
        evidence,
        commit,
        evidence_revision=11,
    )
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
    showcase_evidence = json.loads((workdir / "showcase-evidence.json").read_text(encoding="utf-8"))
    assert showcase_evidence["evaluation"]["case_count"] == 2
    assert all(
        case["error_classification"] == "none" and case["memoryforge"]["answer_correct"] is True
        for case in showcase_evidence["evaluation"]["cases"]
    )
    assert showcase_evidence["evaluation"]["memoryforge"] == {
        "answer_accuracy": 100.0,
        "citation_grounding_accuracy": 100.0,
        "abstention_accuracy": 100.0,
    }


def test_workspace_release_drill_binds_cli_to_current_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        return workspace_drill.subprocess.CompletedProcess(command, 0, "0.3.0\n", "")

    monkeypatch.setattr(workspace_drill.subprocess, "run", run)
    monkeypatch.setenv("PYTHONHOME", "/private/tmp/untrusted")
    monkeypatch.setenv("COV_CORE_SOURCE", "memoryforge")
    monkeypatch.setenv("COVERAGE_FILE", "/private/tmp/untrusted-coverage")

    assert workspace_drill._cli("--version") == "0.3.0\n"
    assert captured["env"]["PYTHONPATH"] == str(workspace_drill.SOURCE_ROOT)
    assert captured["env"]["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONHOME" not in captured["env"]
    assert "COV_CORE_SOURCE" not in captured["env"]
    assert "COVERAGE_FILE" not in captured["env"]


def test_workspace_release_drill_rejects_wrong_or_non_replayed_queries() -> None:
    citation = {
        "source_id": "1" * 64,
        "source_version": 2,
        "locator": "chars:17-61",
        "quote": workspace_drill.EXPECTED_SIGNATURE,
    }
    answered = {
        "status": "answered",
        "answer": workspace_drill.EXPECTED_SIGNATURE,
        "citations": [citation],
        **citation,
        "support": {
            "sufficient": True,
            "enforced": True,
            "failed_hard_gates": [],
        },
        "trace": [{"level": "L0"}],
    }
    unknown = {
        "status": "unknown",
        "answer": "不知道",
        "citations": [],
        "source_id": None,
        "source_version": None,
        "locator": None,
        "quote": None,
        "support": {
            "sufficient": False,
            "enforced": True,
            "failed_hard_gates": [
                "exact_identifier_not_covered",
                "score_below_threshold",
            ],
        },
    }

    assert workspace_drill._answered_query_valid(answered)
    assert workspace_drill._unknown_query_valid(unknown)
    assert workspace_drill._replay_payload(answered) == workspace_drill._replay_payload(
        {key: value for key, value in answered.items() if key != "trace"}
    )
    assert workspace_drill._evaluation_metrics(False, True) == {
        "answer_accuracy": 0.0,
        "citation_grounding_accuracy": 0.0,
        "abstention_accuracy": 100.0,
    }
    answered["answer"] = "wrong"
    assert not workspace_drill._answered_query_valid(answered)


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
