from __future__ import annotations

import hashlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote

import pytest

from memoryforge.importer import import_local_file
from memoryforge.local_portal import LocalPortalApp, LocalPortalServer, make_handler
from memoryforge.workspace import Workspace


def test_local_portal_summary_pagination_search_and_page_read(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=55)
    portal = LocalPortalApp(workspace)
    before = _tree_sha256(workspace)

    status, _, body = portal.dispatch("/api/summary")
    assert status == 200
    summary = json.loads(body)
    assert summary["page_count"] == 55
    assert summary["source_count"] == 1
    assert summary["applied_source_count"] == 1
    assert len(summary["workspace_commit"]) >= 40

    status, _, body = portal.dispatch("/api/pages")
    assert status == 200
    first_page = json.loads(body)
    assert first_page["total"] == 55
    assert first_page["offset"] == 0
    assert first_page["limit"] == 50
    assert len(first_page["items"]) == 50
    assert first_page["items"][0]["title"] == "Page 00"

    status, _, body = portal.dispatch("/api/pages?limit=101")
    hard_capped = json.loads(body)
    assert hard_capped["limit"] == 100
    assert len(hard_capped["items"]) == 55

    status, _, body = portal.dispatch("/api/pages?limit=50&offset=50")
    tail = json.loads(body)
    assert len(tail["items"]) == 5
    assert tail["items"][0]["path"].endswith("page-50.md")

    status, _, body = portal.dispatch("/api/pages?q=" + quote("page-05"))
    searched = json.loads(body)
    assert searched["total"] >= 1
    assert any(item["path"].endswith("page-05.md") for item in searched["items"])

    page_path = first_page["items"][0]["path"]
    status, _, body = portal.dispatch("/api/page?path=" + quote(page_path, safe=""))
    assert status == 200
    page = json.loads(body)
    assert page["path"] == page_path
    assert "Alpha project 0 cache policy" in page["content"]
    assert "<p>Alpha project 0 cache policy</p>" in page["html"]

    assert _tree_sha256(workspace) == before


def test_local_portal_lists_navigation_pages_without_source_ownership(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    page_path = "wiki/pages/" + "a" * 64 + ".md"
    (workspace / page_path).write_text("# 友好导航名称\n", encoding="utf-8")
    Workspace.open(workspace).version_store.commit_paths((page_path,), "test: navigation page")

    listing = LocalPortalApp(workspace).list_pages()

    assert listing["total"] == 2
    assert {item["title"] for item in listing["items"]} == {"Page 00", "友好导航名称"}
    assert any(item["path"] == page_path for item in listing["items"])


def test_local_portal_index_shell_is_small_and_self_contained(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=2)
    portal = LocalPortalApp(workspace)

    status, content_type, body = portal.dispatch("/")

    assert status == 200
    assert content_type.startswith("text/html")
    html = body.decode("utf-8")
    assert len(html) < 24_000
    assert '<html lang="zh-CN">' in html
    assert "/api/pages" in html
    assert "/api/page?path=" in html
    assert "http://" not in html
    assert "https://" not in html


def test_local_portal_rejects_unsafe_page_paths(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=2)
    portal = LocalPortalApp(workspace)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside secret\n", encoding="utf-8")
    (workspace / "wiki/pages/link.md").symlink_to(outside)

    for path in (
        "../outside.md",
        "wiki/pages/../outside.md",
        "/tmp/outside.md",
        "wiki/INDEX.md",
        "pages/page-00.md",
        "wiki/pages/link.md",
    ):
        status, _, body = portal.dispatch("/api/page?path=" + quote(path, safe=""))
        assert status == 400, (path, body.decode())


def test_local_portal_handler_and_loopback_server_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace_with_pages(tmp_path, count=2)
    portal = LocalPortalApp(workspace)

    handler = make_handler(portal)
    assert issubclass(handler, BaseHTTPRequestHandler)
    assert handler.portal is portal

    monkeypatch.setattr(LocalPortalServer, "server_bind", lambda self: None)
    monkeypatch.setattr(LocalPortalServer, "server_activate", lambda self: None)
    server = LocalPortalServer(workspace, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_local_portal_serve_cli_help_does_not_start_server() -> None:
    from typer.testing import CliRunner

    from memoryforge.cli import app

    result = CliRunner().invoke(app, ["showcase", "serve", "--help"])
    assert result.exit_code == 0, result.output
    assert "--port" in result.output
    assert "--workspace" in result.output


def _workspace_with_pages(tmp_path: Path, count: int) -> Path:
    workspace_root = tmp_path / "workspace"
    Workspace.initialize(workspace_root)
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "fixture.md"
    source.write_text("# Fixture\n\nAlpha project cache policy\n", encoding="utf-8")
    imported = import_local_file(workspace_root, source, source_root=source_root)
    pages = workspace_root / "wiki/pages"
    pages.mkdir(exist_ok=True)
    for index in range(count):
        (pages / f"page-{index:02}.md").write_text(
            f"# Page {index:02}\n\nAlpha project {index} cache policy\n",
            encoding="utf-8",
        )
    Workspace.open(workspace_root).version_store.commit_paths(
        ("wiki/pages",),
        "test: portal pages",
    )
    with sqlite3.connect(workspace_root / ".memoryforge/index.sqlite") as connection:
        version_id = connection.execute(
            """
            SELECT v.id
            FROM source_versions AS v
            JOIN sources AS s ON s.id = v.source_id
            WHERE s.source_id = ? AND v.is_current = 1
            """,
            (imported.source_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO applied_source_versions(source_id, source_version_id)
            VALUES (?, ?)
            """,
            (imported.source_id, version_id),
        )
        connection.executemany(
            """
            INSERT INTO page_sources(page_path, source_id)
            VALUES (?, ?)
            """,
            (
                (f"wiki/pages/page-{index:02}.md", imported.source_id)
                for index in range(count)
            ),
        )
    return workspace_root


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts
    ):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
