from __future__ import annotations

import importlib.util
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memoryforge.workspace import Workspace

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

    return_code, output, timed_out = benchmark._run_isolated_pytest(
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
    assert timed_out is False
    assert str(shadow) not in output


def test_isolated_pytest_timeout_terminates_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "CASE_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()

    return_code, output, timed_out = benchmark._run_isolated_pytest(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        environment=dict(os.environ),
    )

    assert return_code == -1
    assert output == ""
    assert timed_out is True
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


def test_pytest_failure_classification_preserves_collection_cause() -> None:
    assert (
        benchmark._classify_pytest_result(
            4,
            "ModuleNotFoundError: No module named 'memoryforge.platform_lock'",
            "failed",
            timed_out=False,
        )
        == "pytest_collection_module_not_found"
    )
    assert (
        benchmark._classify_pytest_result(
            4,
            "ERROR: usage: pytest",
            "failed",
            timed_out=False,
        )
        == "pytest_usage_error"
    )
    assert (
        benchmark._classify_pytest_result(
            1,
            "AssertionError: expected lock",
            "failed",
            timed_out=False,
        )
        == "pytest_assertion_failure"
    )


def test_run_case_removes_explicit_pytest_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def run(
        _command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
    ) -> tuple[int, str, bool]:
        del cwd
        captured.update(environment)
        return 0, "1 passed\n", False

    monkeypatch.setenv("PYTEST_PLUGINS", "pytest_cov.plugin")
    monkeypatch.setattr(benchmark, "_run_isolated_pytest", run)

    result = benchmark._run_case(
        {
            "id": "isolated",
            "test": "test_descriptor_lock_round_trip_and_contention",
        }
    )

    assert result["status"] == "passed"
    assert "PYTEST_PLUGINS" not in captured


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace lock")
def test_workspace_lock_is_not_split_by_lock_file_replacement(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace")
    lock_path = workspace.internal_dir / "workspace.lock"

    def acquire_second() -> None:
        with workspace.exclusive_lock():
            pass

    with ThreadPoolExecutor(max_workers=1) as executor:
        with workspace.exclusive_lock():
            lock_path.write_bytes(b"first")
            lock_path.rename(workspace.internal_dir / "displaced.lock")
            lock_path.write_bytes(b"replacement")
            waiting = executor.submit(acquire_second)
            time.sleep(0.05)
            assert not waiting.done()
        waiting.result(timeout=2)
