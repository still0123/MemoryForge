from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.interface.cli as cli_module
import memoryforge.portal.desktop as desktop
from memoryforge.interface.cli import app
from memoryforge.core.errors import WorkspaceError


def test_recent_workspace_round_trip_and_ignores_stale_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_path = tmp_path / "state" / "desktop.json"

    desktop.save_recent_workspace(workspace, state_path)

    assert desktop.load_recent_workspace(state_path) == workspace.resolve()
    workspace.rmdir()
    assert desktop.load_recent_workspace(state_path) is None


def test_choose_workspace_returns_none_when_dialog_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        desktop.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout=""),
    )

    assert desktop.choose_workspace() is None


def test_run_desktop_embeds_loopback_portal_and_closes_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[str] = []
    captured: dict[str, object] = {}

    class FakeServer:
        server_address = ("127.0.0.1", 45837)

        def __init__(self, root: Path, *, port: int) -> None:
            assert root == workspace.resolve()
            assert port == 0

        def serve_forever(self, *, poll_interval: float) -> None:
            assert poll_interval == 0.5
            events.append("serve")

        def shutdown(self) -> None:
            events.append("shutdown")

        def server_close(self) -> None:
            events.append("close")

    class FakeWebView:
        def create_window(self, title: str, url: str, **kwargs: object) -> object:
            captured.update(title=title, url=url, **kwargs)
            return object()

        def start(self) -> None:
            events.append("window")

    monkeypatch.setattr(desktop, "LocalPortalServer", FakeServer)
    monkeypatch.setattr(desktop, "_load_webview", lambda: FakeWebView())

    desktop.run_desktop(workspace, state_path=tmp_path / "desktop.json")

    assert captured == {
        "title": "MemoryForge — workspace",
        "url": "http://127.0.0.1:45837",
        "width": 1320,
        "height": 900,
        "min_size": (960, 640),
    }
    assert events[-2:] == ["shutdown", "close"]


def test_run_desktop_rejects_workspace_and_picker_together(tmp_path: Path) -> None:
    with pytest.raises(desktop.DesktopAppError, match="either"):
        desktop.run_desktop(tmp_path, choose=True)


def test_run_desktop_explains_when_selected_folder_is_not_a_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidServer:
        def __init__(self, root: Path, *, port: int) -> None:
            del root, port
            raise WorkspaceError("MemoryForge workspace is not initialized")

    monkeypatch.setattr(desktop, "LocalPortalServer", InvalidServer)
    monkeypatch.setattr(desktop, "_load_webview", lambda: object())

    with pytest.raises(desktop.DesktopAppError, match="不是已初始化"):
        desktop.run_desktop(tmp_path)


def test_main_displays_startup_error_and_ignores_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[str] = []

    def fail() -> None:
        raise desktop.DesktopAppError("所选目录不是已初始化的 MemoryForge Workspace。")

    monkeypatch.setattr(desktop, "run_desktop", fail)
    monkeypatch.setattr(desktop, "_show_startup_error", errors.append)
    desktop.main()
    assert errors == ["所选目录不是已初始化的 MemoryForge Workspace。"]

    monkeypatch.setattr(
        desktop,
        "run_desktop",
        lambda: (_ for _ in ()).throw(desktop.DesktopSelectionCancelled("cancelled")),
    )
    desktop.main()
    assert errors == ["所选目录不是已初始化的 MemoryForge Workspace。"]


def test_desktop_cli_forwards_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_desktop(workspace: Path | None, *, choose: bool) -> None:
        captured.update(workspace=workspace, choose=choose)

    monkeypatch.setattr(cli_module, "run_desktop", fake_run_desktop)

    result = CliRunner().invoke(
        app,
        ["desktop", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert captured == {"workspace": tmp_path, "choose": False}
