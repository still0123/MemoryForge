from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_external_code_wiki_benchmark.py"
_spec = importlib.util.spec_from_file_location("run_external_code_wiki_benchmark", _SCRIPT)
assert _spec and _spec.loader
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)


def test_external_suite_contract_is_frozen() -> None:
    manifest = json.loads(benchmark.MANIFEST.read_text(encoding="utf-8"))

    assert {repository["name"] for repository in manifest["repositories"]} == {
        "click",
        "cobra",
        "zod",
    }
    for repository in manifest["repositories"]:
        suite = benchmark.CodeEvaluationSuite.model_validate_json(
            benchmark.SUITES[repository["name"]].read_text(encoding="utf-8")
        )
        assert len(suite.expected_source_paths) == repository["expected_source_count"]
        assert len(suite.symbols) == 20
        assert len(suite.relations) == 15
        assert len(suite.modules) == 5
        assert suite.incremental is None


def test_external_benchmark_rejects_wrong_pinned_commit(tmp_path: Path) -> None:
    checkout = tmp_path / "click"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "benchmark@example.com")
    _git(checkout, "config", "user.name", "benchmark")
    (checkout / "LICENSE.txt").write_text("not the pinned source\n", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", "wrong")
    _git(checkout, "remote", "add", "origin", "https://github.com/pallets/click.git")
    repository = json.loads(benchmark.MANIFEST.read_text(encoding="utf-8"))["repositories"][0]

    with pytest.raises(ValueError, match="pinned commit"):
        benchmark._verify_source(checkout, repository)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
