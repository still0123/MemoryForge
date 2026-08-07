from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "demo" / "run_cross_platform_delivery_benchmark.py"
)
_SPEC = importlib.util.spec_from_file_location("run_cross_platform_delivery_benchmark", _SCRIPT)
assert _SPEC and _SPEC.loader
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def test_isolated_python_cannot_import_candidate_pytest_shadow(tmp_path: Path) -> None:
    shadow = tmp_path / "pytest.py"
    shadow.write_text("raise RuntimeError('candidate shadow loaded')\n", encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(tmp_path))

    return_code, output = benchmark._run_isolated_pytest(
        [
            sys.executable,
            "-I",
            "-c",
            "import pytest; print(pytest.__file__)",
        ],
        cwd=tmp_path,
        environment=environment,
    )

    assert return_code == 0
    assert str(shadow) not in output


def test_isolated_pytest_timeout_terminates_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "CASE_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()

    return_code, output = benchmark._run_isolated_pytest(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        environment=dict(os.environ),
    )

    assert return_code == -1
    assert output == ""
    assert time.monotonic() - started < 2


def test_direct_platform_import_count_handles_import_from(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "src/memoryforge"
    package.mkdir(parents=True)
    (package / "platform_lock.py").write_text("import fcntl\n", encoding="utf-8")
    (package / "workspace.py").write_text("from fcntl import flock\n", encoding="utf-8")
    (package / "changesets.py").write_text("import msvcrt\n", encoding="utf-8")
    monkeypatch.setattr(benchmark, "REPO_ROOT", tmp_path)

    assert benchmark._direct_platform_import_count() == 2


def test_confirmation_isolation_requires_no_result_or_hosted_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "REPO_ROOT", tmp_path)
    confirmation = {"status": "not_run"}

    assert benchmark._confirmation_is_isolated(confirmation) is True
    result = tmp_path / "demo/results/cross_platform_delivery_confirmation.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}\n", encoding="utf-8")
    assert benchmark._confirmation_is_isolated(confirmation) is False
    result.unlink()
    workflow = tmp_path / ".github/workflows/windows.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: forbidden\n", encoding="utf-8")
    assert benchmark._confirmation_is_isolated(confirmation) is False
