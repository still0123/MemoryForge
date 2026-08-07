from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from memoryforge.errors import MemoryForgeError
from memoryforge.showcase import (
    ShowcaseBuildError,
    _publish,
    _validate_output,
    build_showcase,
)

_SHOWCASE_TEST = Path(__file__).with_name("test_showcase.py")
_SHOWCASE_SPEC = importlib.util.spec_from_file_location("showcase_fixture", _SHOWCASE_TEST)
assert _SHOWCASE_SPEC and _SHOWCASE_SPEC.loader
showcase_fixture = importlib.util.module_from_spec(_SHOWCASE_SPEC)
_SHOWCASE_SPEC.loader.exec_module(showcase_fixture)

_BENCHMARK = Path(__file__).resolve().parent.parent / "demo" / "run_static_showcase_benchmark.py"
_BENCHMARK_SPEC = importlib.util.spec_from_file_location("showcase_benchmark", _BENCHMARK)
assert _BENCHMARK_SPEC and _BENCHMARK_SPEC.loader
showcase_benchmark = importlib.util.module_from_spec(_BENCHMARK_SPEC)
_BENCHMARK_SPEC.loader.exec_module(showcase_benchmark)


def test_showcase_reads_fixed_commit_and_rejects_forged_page_paths(tmp_path: Path) -> None:
    workspace, evidence = showcase_fixture._public_workspace(tmp_path)
    output = tmp_path / "showcase"
    build_showcase(workspace, output, evidence=evidence)
    first = json.loads((output / "showcase.json").read_text(encoding="utf-8"))
    page_path = workspace / first["wiki"]["pages"][0]["path"]
    page_path.write_text("# UNCOMMITTED PRIVATE TITLE\n", encoding="utf-8")

    build_showcase(workspace, output, evidence=evidence)

    rebuilt = json.loads((output / "showcase.json").read_text(encoding="utf-8"))
    assert "UNCOMMITTED PRIVATE TITLE" not in json.dumps(rebuilt)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside secret\n", encoding="utf-8")
    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        connection.execute(
            """
            UPDATE wiki_facts
            SET page_path = '../outside.md'
            WHERE id = (SELECT MIN(id) FROM wiki_facts)
            """
        )
    with pytest.raises(MemoryForgeError, match="invalid historical Wiki file identity"):
        build_showcase(workspace, tmp_path / "forged", evidence=evidence)


def test_showcase_requires_real_apply_commit_and_complete_approval_chain(
    tmp_path: Path,
) -> None:
    workspace, evidence = showcase_fixture._public_workspace(tmp_path)
    applied = next((workspace / ".memoryforge/staging/applied").iterdir())
    receipt_path = applied / "receipt.json"
    original_receipt = receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(original_receipt)
    receipt["commit"] = "0" * 40
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(MemoryForgeError, match="Commit does not exist"):
        build_showcase(workspace, tmp_path / "forged-commit", evidence=evidence)

    receipt_path.write_text(original_receipt, encoding="utf-8")
    for name in ("review.json", "review.sha256", "approval.json", "approval.sha256"):
        (applied / name).unlink()
    with pytest.raises(ShowcaseBuildError, match="lacks review or approval"):
        build_showcase(workspace, tmp_path / "missing-approval", evidence=evidence)


def test_showcase_rebuild_keeps_directory_identity_and_foreign_race_data(
    tmp_path: Path,
) -> None:
    workspace, evidence = showcase_fixture._public_workspace(tmp_path)
    output = tmp_path / "showcase"
    build_showcase(workspace, output, evidence=evidence)
    identity = output.stat().st_ino

    build_showcase(workspace, output, evidence=evidence)

    assert output.stat().st_ino == identity
    race = tmp_path / "race"
    _validate_output(workspace, race)
    race.mkdir()
    foreign = race / "foreign.txt"
    foreign.write_text("keep\n", encoding="utf-8")
    with pytest.raises(ShowcaseBuildError, match="not owned"):
        _publish(race, payload=b"{}\n", page=b"<!doctype html>\n")
    assert foreign.read_text(encoding="utf-8") == "keep\n"


def test_showcase_benchmark_rejects_collect_only_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "1 test collected\n", "")

    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    monkeypatch.setattr(showcase_benchmark.subprocess, "run", fake_run)

    result = showcase_benchmark._run_case(
        {
            "id": "complete-public-readonly-snapshot",
            "test": "test_showcase_builds_complete_public_snapshot_without_mutating_workspace",
        }
    )

    assert result["status"] == "failed"
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert "PYTEST_ADDOPTS" not in environment
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
