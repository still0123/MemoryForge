"""Native-window launcher for the local MemoryForge portal."""

from __future__ import annotations

import json
import subprocess
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from threading import Thread
from types import ModuleType
from typing import Protocol, cast

from memoryforge.core.errors import MemoryForgeError, WorkspaceError
from memoryforge.portal.local_portal import LocalPortalServer

_HOST = "127.0.0.1"
_STATE_FILE = "desktop.json"


class DesktopAppError(MemoryForgeError):
    """Raised when the desktop shell cannot start safely."""


class DesktopSelectionCancelled(DesktopAppError):
    """Raised when the initial workspace picker is cancelled."""


class _WebView(Protocol):
    def create_window(
        self,
        title: str,
        url: str,
        *,
        width: int,
        height: int,
        min_size: tuple[int, int],
    ) -> object: ...

    def start(self) -> None: ...


def default_state_path() -> Path:
    """Return the per-user location for the most recently opened workspace."""
    return Path.home() / "Library" / "Application Support" / "MemoryForge" / _STATE_FILE


def load_recent_workspace(state_path: Path | None = None) -> Path | None:
    """Return a still-existing recent workspace, without trusting malformed state."""
    path = state_path or default_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("workspace") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        return None
    workspace = Path(value).expanduser()
    return workspace.resolve() if workspace.is_dir() else None


def save_recent_workspace(workspace: Path, state_path: Path | None = None) -> None:
    """Persist the selected workspace; failure does not prevent opening it."""
    path = state_path or default_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"workspace": str(workspace.resolve())}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def choose_workspace() -> Path | None:
    """Ask macOS for a workspace folder, returning None when the user cancels."""
    try:
        result = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                "POSIX path of (choose folder with prompt \"选择 MemoryForge Workspace\")",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise DesktopAppError("Desktop folder picker requires macOS.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).expanduser().resolve()


def run_desktop(
    workspace: Path | None = None,
    *,
    choose: bool = False,
    state_path: Path | None = None,
) -> None:
    """Open one workspace in a native macOS window and stop on close."""
    if workspace is not None and choose:
        raise DesktopAppError("Use either --workspace or --choose-workspace, not both.")

    selected = workspace.expanduser().resolve() if workspace is not None else None
    if selected is None and not choose:
        selected = load_recent_workspace(state_path)
    if selected is None:
        selected = choose_workspace()
    if selected is None:
        raise DesktopSelectionCancelled("No workspace selected.")
    if not selected.is_dir():
        raise DesktopAppError("Selected path is not a folder.")

    webview = _load_webview()
    try:
        server = LocalPortalServer(selected, port=0)
    except (FileNotFoundError, WorkspaceError) as exc:
        raise DesktopAppError(
            "所选目录不是已初始化的 MemoryForge Workspace。"
            "请先运行 memoryforge init <目录>，再重新打开应用。"
        ) from exc
    port = int(server.server_address[1])
    server_thread = Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.5},
        name="memoryforge-desktop-server",
        daemon=True,
    )
    server_thread.start()
    try:
        webview.create_window(
            f"MemoryForge — {selected.name}",
            f"http://{_HOST}:{port}",
            width=1320,
            height=900,
            min_size=(960, 640),
        )
        save_recent_workspace(selected, state_path)
        webview.start()
    finally:
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()


def _load_webview() -> _WebView:
    try:
        module: ModuleType = import_module("webview")
    except ModuleNotFoundError as exc:
        raise DesktopAppError(
            "Desktop support is not installed. Run 'pip install memoryforge-wiki[desktop]'."
        ) from exc
    return cast(_WebView, module)


def main() -> None:
    try:
        run_desktop()
    except DesktopSelectionCancelled:
        return
    except (MemoryForgeError, OSError, ValueError) as exc:
        _show_startup_error(str(exc) or "MemoryForge 无法启动。")


def _show_startup_error(message: str) -> None:
    script = f'display alert "MemoryForge" message {json.dumps(message)} as critical'
    with suppress(OSError):
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False)
