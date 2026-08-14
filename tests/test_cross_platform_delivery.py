from __future__ import annotations

import ast
import importlib.util
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import memoryforge.core.platform_lock as platform_lock
from memoryforge.core.platform_lock import (
    UnsafeLockFileError,
    exclusive_file_lock,
    lock_descriptor,
    try_lock_descriptor,
    unlock_descriptor,
)
from memoryforge.storage.workspace import Workspace


def test_descriptor_lock_round_trip_and_contention(tmp_path: Path) -> None:
    path = tmp_path / "workspace.lock"
    holder = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    contender = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert try_lock_descriptor(holder) is True
        assert try_lock_descriptor(contender) is False
        unlock_descriptor(holder)
        assert try_lock_descriptor(contender) is True
        unlock_descriptor(contender)
    finally:
        os.close(holder)
        os.close(contender)


def test_descriptor_lock_blocking_waits_for_release(tmp_path: Path) -> None:
    path = tmp_path / "workspace.lock"
    holder = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    contender = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    assert try_lock_descriptor(holder) is True
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(lock_descriptor, contender, poll_interval=0.01)
            time.sleep(0.05)
            assert not waiting.done()
            unlock_descriptor(holder)
            waiting.result(timeout=2)
        unlock_descriptor(contender)
    finally:
        os.close(holder)
        os.close(contender)


def test_windows_backend_locks_byte_zero_and_restores_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMsvcrt:
        LK_NBLCK = 2
        LK_UNLCK = 3

        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int]] = []

        def locking(self, descriptor: int, mode: int, count: int) -> None:
            self.calls.append((os.lseek(descriptor, 0, os.SEEK_CUR), mode, count))

    fake = FakeMsvcrt()
    monkeypatch.setattr(platform_lock, "_msvcrt", fake)
    path = tmp_path / "workspace.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, b"unchanged")
        os.lseek(descriptor, 4, os.SEEK_SET)
        assert platform_lock._try_lock_windows(descriptor) is True
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 4
        platform_lock._unlock_windows(descriptor)
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 4
        assert fake.calls == [(0, fake.LK_NBLCK, 1), (0, fake.LK_UNLCK, 1)]

        def contend(_descriptor: int, _mode: int, _count: int) -> None:
            raise OSError(13, "permission denied")

        monkeypatch.setattr(fake, "locking", contend)
        assert platform_lock._try_lock_windows(descriptor) is False
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 4
    finally:
        os.close(descriptor)


def test_exclusive_file_lock_preserves_content_and_rejects_symlink(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace.lock"
    path.write_bytes(b"retained")

    with exclusive_file_lock(path):
        assert path.read_bytes() == b"retained"

    assert path.read_bytes() == b"retained"
    target = tmp_path / "target.lock"
    target.write_bytes(b"outside")
    linked = tmp_path / "linked.lock"
    linked.symlink_to(target)
    with pytest.raises(UnsafeLockFileError), exclusive_file_lock(linked):
        pass
    assert target.read_bytes() == b"outside"


def test_workspace_and_changesets_have_no_direct_platform_lock_imports(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parent.parent
    for relative in (
        "src/memoryforge/storage/workspace.py",
        "src/memoryforge/storage/changesets.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert imported.isdisjoint({"fcntl", "msvcrt"})

    workspace = Workspace.initialize(tmp_path / "workspace")
    with workspace.exclusive_lock():
        contender = os.open(workspace.internal_dir / "workspace.lock", os.O_RDWR)
        try:
            assert try_lock_descriptor(contender) is False
        finally:
            os.close(contender)


def test_powershell_local_gate_matches_release_contract() -> None:
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts/check_local.ps1").read_text(encoding="utf-8")

    for required in (
        "ruff check --no-cache .",
        "ruff format --check .",
        "mypy",
        "demo/validate_benchmark_registry.py",
        "demo/run_cross_platform_smoke.py",
        "pytest",
        "--wheel --sdist --no-isolation",
        "demo/run_release_check.py",
        "--no-build-isolation",
        "pip check",
        "Get-FileHash",
        "SHA256SUMS",
    ):
        assert required in script
    assert "/bin/" not in script
    assert "scripts/check_local.sh" not in script


def test_cross_platform_smoke_runs_cli_workspace_and_unknown_demo(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parent.parent
    script = root / "demo/run_cross_platform_smoke.py"
    spec = importlib.util.spec_from_file_location("run_cross_platform_smoke", script)
    assert spec and spec.loader
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    output = tmp_path / "smoke.json"

    smoke.main(
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "--output",
            str(output),
        ]
    )

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert evidence["status"] == "passed"
    assert evidence["checks"] == {
        "package_import": "passed",
        "cli_version": "passed",
        "cli_help": "passed",
        "workspace_init": "passed",
        "workspace_open": "passed",
        "workspace_lock": "passed",
        "empty_query_unknown": "passed",
    }
    assert str(tmp_path) not in json.dumps(evidence)
