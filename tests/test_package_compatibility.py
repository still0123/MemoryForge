from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from memoryforge._compat import _LEGACY_MODULES


@pytest.mark.parametrize(
    ("legacy_name", "target_name"),
    sorted(_LEGACY_MODULES.items()),
)
def test_legacy_module_imports_resolve_to_new_implementation(
    legacy_name: str,
    target_name: str,
) -> None:
    legacy_module = importlib.import_module(legacy_name)
    target_module = importlib.import_module(target_name)

    assert legacy_module is target_module


def test_legacy_package_exports_remain_available() -> None:
    from memoryforge.compiler import _render_index, compile_pending_sources
    from memoryforge.evaluation import EvaluationCase, EvaluationSuite, run_evaluation
    from memoryforge.query import _top_matches, answer_question

    assert _render_index.__module__ == "memoryforge.compiler.compiler"
    assert compile_pending_sources.__module__ == "memoryforge.compiler.compiler"
    assert EvaluationCase.__module__ == "memoryforge.evaluation.evaluation"
    assert EvaluationSuite.__module__ == "memoryforge.evaluation.evaluation"
    assert run_evaluation.__module__ == "memoryforge.evaluation.evaluation"
    assert _top_matches.__module__ == "memoryforge.query.query"
    assert answer_question.__module__ == "memoryforge.query.query"


def test_module_entrypoint_still_reports_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "memoryforge", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "0.4.0"
