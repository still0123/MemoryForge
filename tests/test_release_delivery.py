from __future__ import annotations

import importlib.util
import io
import json
import os
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
release_check = _module("run_release_check", "demo/run_release_check.py")


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
    assert (
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.eol=lf",
        "-c",
        f"core.hooksPath={os.devnull}",
    ) == release_builder.WORKTREE_CHECKOUT_OPTIONS
    monkeypatch.setenv("PYTHONPATH", "/private/tmp/untrusted")
    monkeypatch.setenv("PYTHONHOME", "/private/tmp/untrusted")
    monkeypatch.setenv("COV_CORE_DATAFILE", ".coverage")
    monkeypatch.setenv("COVERAGE_FILE", ".coverage")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/private/tmp/untrusted-hook")
    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("PIP_FIND_LINKS", "/private/tmp/untrusted")
    monkeypatch.setenv("UV_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("UV_EXTRA_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setenv("UV_CONFIG_FILE", "/private/tmp/untrusted.toml")
    environment = release_builder._clean_build_environment()
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert "COV_CORE_DATAFILE" not in environment
    assert "COVERAGE_FILE" not in environment
    assert not any(key.startswith("GIT_") for key in environment)
    assert "PIP_FIND_LINKS" not in environment
    assert "UV_EXTRA_INDEX_URL" not in environment
    assert "UV_CONFIG_FILE" not in environment
    assert environment["PIP_INDEX_URL"] == release_builder.PACKAGE_INDEX_URL
    assert environment["UV_DEFAULT_INDEX"] == release_builder.PACKAGE_INDEX_URL
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["SOURCE_DATE_EPOCH"] == "315532800"


def test_release_builder_git_ignores_caller_git_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_run(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs["env"])  # type: ignore[arg-type]
        return type("Completed", (), {"stdout": "head\n"})()

    monkeypatch.setenv("GIT_DIR", "/private/tmp/untrusted")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setattr(release_builder.subprocess, "run", fake_run)

    assert release_builder._git("rev-parse", "HEAD") == "head"
    assert not any(key.startswith("GIT_") for key in captured)


def test_public_producers_ignore_caller_git_and_package_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, str]] = []

    def fake_run(*_args: object, **kwargs: object) -> object:
        captured.append(kwargs["env"])  # type: ignore[arg-type]
        return type("Completed", (), {"stdout": "head\n"})()

    monkeypatch.setenv("GIT_DIR", "/private/tmp/untrusted")
    monkeypatch.setenv("PIP_FIND_LINKS", "/private/tmp/untrusted")
    monkeypatch.setenv("UV_EXTRA_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setattr(summary_builder.subprocess, "run", fake_run)
    assert summary_builder._git("rev-parse", "HEAD") == "head"
    monkeypatch.setattr(release_check.subprocess, "run", fake_run)
    assert release_check._git_output(release_check.REPO_ROOT, "rev-parse", "HEAD") == "head"
    assert all("GIT_DIR" not in env for env in captured)
    assert all(env["GIT_CONFIG_NOSYSTEM"] == "1" for env in captured)
    assert all(env["GIT_CONFIG_GLOBAL"] == os.devnull for env in captured)

    source = (release_check.REPO_ROOT / "demo/run_release_check.py").read_text(encoding="utf-8")
    assert 'not key.startswith(("GIT_", "PIP_", "UV_"))' in source
    assert '"--isolated"' in source
    assert '"--constraint"' in source


def test_sdist_clean_room_passes_constraints_to_isolated_pip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[list[str], dict[str, str]]] = []
    environment = (tmp_path / "clean" / "environment").resolve()
    import_path = environment / "lib/python3.11/site-packages/memoryforge/__init__.py"

    class FakeEnvBuilder:
        def __init__(self, *, with_pip: bool) -> None:
            assert with_pip is True

        def create(self, _path: Path) -> None:
            return None

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
    ) -> str:
        commands.append((command, environment))
        if "importlib.metadata,json,memoryforge" in " ".join(command):
            return json.dumps({"version": "0.3.0", "import_path": str(import_path)})
        if command[-2:] == ["memoryforge", "--version"]:
            return "0.3.0\n"
        return ""

    constraints = tmp_path / "constraints.txt"
    monkeypatch.setattr(release_builder.venv, "EnvBuilder", FakeEnvBuilder)
    monkeypatch.setattr(release_builder, "_run", fake_run)

    release_builder._check_sdist_clean_room(
        tmp_path / "memoryforge-0.3.0.tar.gz",
        tmp_path / "clean",
        constraints=constraints,
        cwd=tmp_path,
    )

    installs = [command for command, _environment in commands if "install" in command]
    assert len(installs) == 2
    assert all(["-c", str(constraints)] == command[-3:-1] for command in installs)
    assert all("PIP_CONSTRAINT" not in clean for _command, clean in commands)


@pytest.mark.parametrize(
    "source",
    (
        '__version__ = "0.3.0"\n__version__ = "9.9.9"\n',
        '__version__: str = "0.3.0"\n',
        '__version__ = "0.3.0"\ndel __version__\n',
    ),
)
def test_release_builder_rejects_ambiguous_source_versions(
    tmp_path: Path,
    source: str,
) -> None:
    package = tmp_path / "memoryforge"
    package.mkdir()
    (package / "__init__.py").write_text(source, encoding="utf-8")

    with pytest.raises(SystemExit, match="one string literal assignment"):
        release_builder._source_module_version(tmp_path)


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
    assert release["evidence"][-1]["status"] == "development_passed_gate_pending"
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


def test_benchmark_summary_binds_registry_to_declared_commit() -> None:
    registry = json.loads(summary_builder.REGISTRY.read_text(encoding="utf-8"))
    registry_summary = summary_builder.validator.validate_registry_payload(registry)
    registry["qa_case_count"] -= 1

    with pytest.raises(ValueError, match="Registry does not match its Commit"):
        summary_builder.validator.build_benchmark_summary(
            registry,
            registry_summary,
            package_version="0.3.0",
            memoryforge_commit=summary_builder._git("rev-parse", "HEAD"),
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
        evidence_revision=14,
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
    retained = json.loads(
        (
            workspace_drill.REPO_ROOT / "demo/results/artifacts/release_candidate_development/"
            "f174d1de4e459c4b324f0ba5f58e8df62263fa00/workspace-drill.json"
        ).read_text(encoding="utf-8")
    )
    answered = retained["queries"]["answered"]["original"]
    unknown = retained["queries"]["unknown"]["original"]

    assert workspace_drill._answered_query_valid(answered)
    assert workspace_drill._unknown_query_valid(unknown)
    traced = {**answered, "trace": [{"level": "L0"}]}
    assert workspace_drill._replay_payload(answered) == workspace_drill._replay_payload(traced)
    assert workspace_drill._evaluation_metrics(False, True) == {
        "answer_accuracy": 0.0,
        "citation_grounding_accuracy": 0.0,
        "abstention_accuracy": 100.0,
    }
    answered["answer"] = "wrong"
    assert not workspace_drill._answered_query_valid(answered)
    answered["answer"] = workspace_drill.EXPECTED_SIGNATURE
    answered.pop("evidence")
    assert not workspace_drill._answered_query_valid(answered)
    unknown["support"].pop("score")
    assert not workspace_drill._unknown_query_valid(unknown)


def test_release_check_reuses_raw_code_wiki_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = json.loads(
        (
            release_check.REPO_ROOT
            / "demo/results/artifacts/release_candidate_delivery_candidate_11/"
            "macos/code-wiki-evidence.json"
        ).read_text(encoding="utf-8")
    )
    monkeypatch.setattr(
        release_check,
        "_git_output",
        lambda *_args: evidence["memoryforge_commit"],
    )
    release_check._validate_code_evidence(evidence)
    evidence["fixture"]["initial_commit"] = "0" * 40

    with pytest.raises(SystemExit, match="Evidence contract failed"):
        release_check._validate_code_evidence(evidence)


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
