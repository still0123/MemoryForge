from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote

import pytest

from memoryforge.adapters.importer import import_local_file
from memoryforge.core.models import ChangeOperation, ChangeOperationType, ChangeSet, ChangeSetStatus
from memoryforge.portal.local_portal import LocalPortalApp, LocalPortalServer, make_handler
from memoryforge.portal.portal_catalog import _page_subtype
from memoryforge.storage.changesets import ChangeSetStore
from memoryforge.storage.workspace import Workspace


def test_portal_classifies_new_code_wiki_modules_before_source_files() -> None:
    assert _page_subtype("code", "code_wiki", "src/store.py", "module-1") == "code_module"
    assert _page_subtype("code", "code_wiki", "src/store.py", None) == "code_file"


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
    assert page["summary"] == "模块 fixture/page-00 包含 3 个已验证代码符号。"
    assert "summary:" not in page["html"]
    assert "<p>Alpha project 0 cache policy</p>" in page["html"]
    assert page["freshness"]["state"] == "fresh"
    assert page["freshness"]["based_on_commit"]
    assert page["freshness"]["current_commit"]
    assert "基于" in page["freshness"]["label"]

    assert page["has_more"] is False
    assert page["next_offset"] == page["total"]
    assert _tree_sha256(workspace) == before


def test_local_portal_paginates_large_wiki_pages(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    page_path = "wiki/pages/page-00.md"
    page = workspace / page_path
    original = page.read_text(encoding="utf-8")
    large_body = "\n\n".join(f"Paragraph {index:04}: " + "x" * 180 for index in range(500))
    page.write_text(original + "\n\n" + large_body + "\n", encoding="utf-8")
    Workspace.open(workspace).version_store.commit_paths((page_path,), "test: large Wiki page")
    portal = LocalPortalApp(workspace)

    status, _, body = portal.dispatch(
        "/api/page?path=wiki%2Fpages%2Fpage-00.md&offset=0&limit=4000"
    )
    first = json.loads(body)

    assert status == 200
    assert first["has_more"] is True
    assert 4_000 <= first["next_offset"] < first["total"]
    assert len(first["content"]) == first["next_offset"]
    assert "Paragraph 0499" not in first["content"]

    status, _, body = portal.dispatch(
        f"/api/page?path=wiki%2Fpages%2Fpage-00.md&offset={first['next_offset']}&limit=4000"
    )
    second = json.loads(body)

    assert status == 200
    assert second["next_offset"] > first["next_offset"]
    assert second["content"].startswith(first["content"])
    assert len(second["content"]) == second["next_offset"]


def test_local_portal_renders_existing_wiki_links_as_page_routes(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=2)
    page_path = "wiki/pages/page-00.md"
    page = workspace / page_path
    page.write_text(
        page.read_text(encoding="utf-8")
        + "\n- [Page 01](page-01.md)\n"
        + "- [Missing](missing.md)\n",
        encoding="utf-8",
    )
    Workspace.open(workspace).version_store.commit_paths((page_path,), "test: Wiki page links")

    payload = json.loads(
        LocalPortalApp(workspace).dispatch("/api/page?path=wiki%2Fpages%2Fpage-00.md")[2]
    )

    assert (
        '<a class="md-link" href="#page=wiki%2Fpages%2Fpage-01.md">Page 01</a>' in payload["html"]
    )
    assert '<span class="md-link">Missing</span>' in payload["html"]


def test_local_portal_lists_navigation_pages_without_source_ownership(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    page_path = "wiki/pages/" + "a" * 64 + ".md"
    (workspace / page_path).write_text("# 友好导航名称\n", encoding="utf-8")
    Workspace.open(workspace).version_store.commit_paths((page_path,), "test: navigation page")

    listing = LocalPortalApp(workspace).list_pages()

    assert listing["total"] == 2
    assert {item["title"] for item in listing["items"]} == {"Page 00", "友好导航名称"}
    assert any(item["path"] == page_path for item in listing["items"])


def test_local_portal_ask_forwards_explicit_local_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    provider = object()
    portal = LocalPortalApp(workspace, provider=provider, allow_local_llm=True)  # type: ignore[arg-type]
    captured = {}

    def fake_answer(root: Path, question: str, **kwargs: object) -> dict[str, object]:
        captured.update(root=root, question=question, **kwargs)
        return {
            "status": "answered",
            "evidence_status": "grounded",
            "answer": "EFS 是弹性文件存储。",
            "supported_claims": ["EFS 是弹性文件存储。"],
            "unsupported_aspects": [],
            "wiki_pages": ["wiki/pages/page-00.md"],
            "support": {"sufficient": True},
            "citations": [
                {
                    "source_id": "a" * 64,
                    "source_version": 1,
                    "locator": "chars:0-10",
                    "quote": "EFS 是弹性文件存储。",
                }
            ],
        }

    monkeypatch.setattr("memoryforge.local_portal.answer_question", fake_answer)

    status, _, body = portal.dispatch_post("/api/ask", {"question": "EFS是什么"})

    assert status == 200
    payload = json.loads(body)
    assert payload["answer"] == "EFS 是弹性文件存储。"
    assert payload["evidence_status"] == "grounded"
    assert payload["supported_claims"] == ["EFS 是弹性文件存储。"]
    assert payload["unsupported_aspects"] == []
    assert payload["wiki_pages"] == ["wiki/pages/page-00.md"]
    assert payload["support"] == {"sufficient": True}
    assert payload["citations"] == [
        {
            "source_id": "a" * 64,
            "quote": "EFS 是弹性文件存储。",
            "section": "",
            "source_version": 1,
            "locator": "chars:0-10",
        }
    ]
    assert captured == {
        "root": workspace,
        "question": "EFS是什么",
        "max_pages": 3,
        "max_citations": 6,
        "provider": provider,
        "allow_local": True,
        "repository_id": None,
    }


def test_local_portal_ask_scopes_an_explicit_repository_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    portal = LocalPortalApp(workspace, allow_local_llm=True)
    scope = type("Scope", (), {"repository_id": "repo-id"})()
    captured = {}
    monkeypatch.setattr(
        "memoryforge.portal.local_portal._named_repository_scope",
        lambda *_args: scope,
    )

    def fake_answer(root: Path, question: str, **kwargs: object) -> dict[str, object]:
        captured.update(root=root, question=question, **kwargs)
        return {
            "status": "unknown",
            "evidence_status": "no_local_evidence",
            "answer": "不知道",
            "supported_claims": [],
            "unsupported_aspects": ["no_local_evidence"],
            "wiki_pages": [],
            "citations": [],
        }

    monkeypatch.setattr("memoryforge.portal.local_portal.answer_question", fake_answer)

    status, _, _body = portal.dispatch_post(
        "/api/ask",
        {"question": "efs-mgr 有什么配置？"},
    )

    assert status == 200
    assert captured["repository_id"] == "repo-id"


def test_local_portal_index_shell_is_small_and_self_contained(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=2)
    portal = LocalPortalApp(workspace)

    status, content_type, body = portal.dispatch("/")

    assert status == 200
    assert content_type.startswith("text/html")
    html = body.decode("utf-8")
    assert len(html) < 24_000
    assert '<html lang="zh-CN">' in html
    assert '<link rel="stylesheet" href="/app.css">' in html
    assert '<script src="/vendor/mermaid.min.js" defer></script>' in html
    assert '<script src="/app.js" defer></script>' in html
    assert "<script>" not in html
    assert "<style>" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert 'id="primary-nav"' in html
    assert 'href="#knowledge"' in html
    assert 'href="#add-source"' in html
    assert 'href="#updates"' in html
    assert 'href="#jobs"' in html

    status, content_type, body = portal.dispatch("/app.js")
    assert status == 200
    assert content_type.startswith("text/javascript")
    script = body.decode()
    assert "/api/pages" in script
    assert "/api/page?path=" in script
    assert "location.hash" in script
    assert "AbortController" in script
    assert "setTimeout(()=>renderSearch(value),200)" in script
    assert "memoryforge-recent-pages" in script
    assert "body.innerHTML=data.html" in script
    assert 'setAttribute("aria-current","page")' in script
    assert 'class:"hero"' in script
    assert "查看 Wiki 全文" in script
    assert "打开完整正文" not in script
    assert "查看来源原文" in script
    assert "继续加载" in script
    assert "/api/source-text" in script
    assert "window.mermaid.run" in script

    status, content_type, body = portal.dispatch("/vendor/mermaid.min.js")
    assert status == 200
    assert content_type.startswith("text/javascript")
    assert len(body) > 1_000_000
    assert b"mermaid" in body[:10_000]
    assert "view=published" in script
    assert 'view:"evidence"' in script
    assert "搜索源码证据" in script
    assert "moduleTree" in script
    assert 'heading("来源管理"' in script
    assert 'section("可阅读知识"' in script
    assert 'section("继续探索"' in script
    assert "Git 仓库链接（HTTPS）" in script


def test_local_portal_source_text_reads_only_applied_version(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    source = tmp_path / "source/fixture.md"
    applied_text = source.read_text(encoding="utf-8")
    source.write_text("# Fixture\n\nPending replacement must stay hidden.\n", encoding="utf-8")
    updated = import_local_file(workspace, source, source_root=source.parent)
    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        current_version = int(
            connection.execute(
                """
                SELECT versions.id
                FROM source_versions AS versions
                JOIN sources ON sources.id = versions.source_id
                WHERE sources.source_id = ? AND versions.is_current = 1
                """,
                (updated.source_id,),
            ).fetchone()[0]
        )

    portal = LocalPortalApp(workspace)
    page = json.loads(portal.dispatch("/api/page?path=wiki%2Fpages%2Fpage-00.md")[2])
    applied = page["sources"][0]

    assert page["freshness"]["state"] == "stale"
    assert applied["source_id"] == updated.source_id
    assert applied["source_version"] != current_version
    status, _, body = portal.dispatch(
        f"/api/source-text?source_id={updated.source_id}&source_version={current_version}"
    )
    assert status == 404
    assert "Pending replacement" not in body.decode()

    status, _, body = portal.dispatch(
        f"/api/source-text?source_id={applied['source_id']}"
        f"&source_version={applied['source_version']}"
    )
    assert status == 200
    assert json.loads(body)["text"] == applied_text
    assert portal.dispatch("/api/source-text?path=%2Ftmp%2Ffixture.md")[0] == 400


def test_local_portal_source_text_paginates_complete_immutable_text(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    portal = LocalPortalApp(workspace)
    source = json.loads(portal.dispatch("/api/page?path=wiki%2Fpages%2Fpage-00.md")[2])["sources"][
        0
    ]
    text = (tmp_path / "source/fixture.md").read_text(encoding="utf-8")
    endpoint = (
        f"/api/source-text?source_id={source['source_id']}"
        f"&source_version={source['source_version']}"
    )

    first = json.loads(portal.dispatch(endpoint + "&offset=0&limit=10")[2])
    rest = json.loads(portal.dispatch(endpoint + "&offset=10&limit=100")[2])
    capped = json.loads(portal.dispatch(endpoint + "&limit=999999")[2])

    assert first == {
        "workspace_commit": first["workspace_commit"],
        "source_id": source["source_id"],
        "source_version": source["source_version"],
        "text": text[:10],
        "offset": 0,
        "limit": 10,
        "total": len(text),
        "has_more": True,
    }
    assert rest["text"] == text[10:]
    assert rest["offset"] == 10
    assert rest["total"] == len(text)
    assert rest["has_more"] is False
    assert capped["limit"] == 20_000


def test_local_portal_classifies_projects_sources_templates_and_relations(
    tmp_path: Path,
) -> None:
    workspace, paths, repositories = _workspace_with_typed_pages(tmp_path)
    portal = LocalPortalApp(workspace)

    summary = json.loads(portal.dispatch("/api/summary")[2])
    assert summary == {
        "workspace_commit": Workspace.open(workspace).current_commit(),
        "current_source_count": 6,
        "applied_page_count": 8,
        "source_identity_count": 6,
        "projects": 2,
        "code_pages": 3,
        "conversation_pages": 1,
        "feishu_pages": 1,
        "note_pages": 1,
        "pending_sources": 1,
        "page_count": 8,
        "source_count": 6,
        "applied_source_count": 5,
        "pending_updates": 0,
        "stale_updates": 0,
        "active_jobs": 0,
        "failed_jobs": 0,
    }

    projects = json.loads(portal.dispatch("/api/projects")[2])
    assert {item["name"] for item in projects["items"]} == {"alpha", "beta"}
    assert all(
        "checkout_path" not in item and "remote_url" not in item for item in projects["items"]
    )
    assert "token@" not in json.dumps(projects)
    alpha = next(item for item in projects["items"] if item["name"] == "alpha")
    assert alpha["commit"] == "a" * 12
    assert alpha["languages"] == ["Python"]
    assert alpha["top_modules"] == ["src"]

    conversations = json.loads(
        portal.dispatch("/api/sources?kind=conversation&offset=0&limit=50")[2]
    )
    assert conversations["total"] == 2
    assert {item["status"] for item in conversations["items"]} == {"已应用", "待应用"}
    assert all("source_id" not in item for item in conversations["items"])

    filtered = json.loads(
        portal.dispatch(f"/api/pages?kind=code&project={repositories['alpha']}&offset=0&limit=50")[
            2
        ]
    )
    assert filtered["total"] == 2
    assert {item["kind"] for item in filtered["items"]} == {"code"}
    code_item = next(item for item in filtered["items"] if item["path"] == paths["code"])
    assert code_item["template"] == "code_file"
    assert code_item["relative_path"] == "src/store.py"

    alpha_project = json.loads(portal.dispatch(f"/api/project?id={repositories['alpha']}")[2])
    assert alpha_project["overview"] == []
    assert [item["path"] for item in alpha_project["modules"]] == [paths["module"]]
    assert [item["path"] for item in alpha_project["module_tree"]] == [paths["module"]]
    assert alpha_project["file_count"] == 1
    assert len(alpha_project["files"]) == 1

    default_published = json.loads(
        portal.dispatch("/api/pages?view=published&offset=0&limit=50")[2]
    )
    assert all(item["kind"] != "code" for item in default_published["items"])
    assert {item["kind"] for item in default_published["items"]} >= {
        "project",
        "conversation",
        "feishu",
        "note",
    }

    published = json.loads(
        portal.dispatch(f"/api/pages?view=published&kind=code&project={repositories['alpha']}")[2]
    )
    assert published["total"] == 1
    assert published["items"][0]["template"] == "code_module"

    evidence = json.loads(
        portal.dispatch(f"/api/pages?view=evidence&kind=code&project={repositories['alpha']}")[2]
    )
    assert evidence["total"] == 1
    assert evidence["items"][0]["template"] == "code_file"

    expected_templates = {
        paths["project"]: "project",
        paths["module"]: "code_module",
        paths["code"]: "code_file",
        paths["conversation"]: "conversation",
        paths["feishu"]: "feishu",
        paths["note"]: "note",
    }
    for path, template in expected_templates.items():
        status, _, body = portal.dispatch("/api/page?path=" + quote(path, safe=""))
        assert status == 200
        page = json.loads(body)
        assert page["template"] == template
        assert page["workspace_commit"] == summary["workspace_commit"]
        assert "content" in page and "html" in page

    code_page = json.loads(portal.dispatch("/api/page?path=" + quote(paths["code"], safe=""))[2])
    assert "职责" in code_page["html"]
    assert "主要类型与方法" in code_page["html"]
    assert "代码依据" in code_page["html"]
    assert "checkout" not in json.dumps(code_page["sources"])

    conversation = json.loads(
        portal.dispatch("/api/page?path=" + quote(paths["conversation"], safe=""))[2]
    )
    assert conversation["status"] == "未验证会话记忆"
    mentioned_paths = {item["path"] for item in conversation["related"]["exact_mentions"]}
    assert paths["code"] in mentioned_paths
    assert all(
        "pkg.Store" not in item["detail"] for item in conversation["related"]["exact_mentions"]
    )

    reverse = {
        item["path"]
        for item in code_page["related"]["exact_mentions"]
        if item["relationship"] == "精确提及"
    }
    assert paths["conversation"] in reverse

    feishu = json.loads(portal.dispatch("/api/page?path=" + quote(paths["feishu"], safe=""))[2])
    assert paths["conversation"] in {item["path"] for item in feishu["related"]["direct"]}


def test_local_portal_summary_recovers_stale_changesets(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    opened = Workspace.open(workspace)
    ChangeSetStore(opened).create(
        ChangeSet(
            changeset_id="chg_portal_stale",
            base_commit=opened.current_commit(),
            status=ChangeSetStatus.PROPOSED,
            operations=(
                ChangeOperation(
                    type=ChangeOperationType.CREATE_PAGE,
                    path="wiki/pages/proposed.md",
                ),
            ),
        ),
        {"wiki/pages/proposed.md": "# Proposed\n"},
    )
    (workspace / "wiki/INDEX.md").write_text("# Advanced\n", encoding="utf-8")
    opened.version_store.commit_paths(("wiki/INDEX.md",), "test: stale portal proposal")
    portal = LocalPortalApp(workspace)

    status, _, body = portal.dispatch("/api/summary")
    updates_status, _, updates_body = portal.dispatch("/api/updates")
    detail_status, _, detail_body = portal.dispatch("/api/updates/chg_portal_stale")
    apply_status, _, apply_body = portal.dispatch_post(
        "/api/updates/chg_portal_stale/approve-and-apply",
        {},
    )
    portal.close()

    summary = json.loads(body)
    assert status == 200
    assert summary["pending_updates"] == 0
    assert summary["stale_updates"] == 1
    assert updates_status == 200
    assert json.loads(updates_body)["items"][0]["status"] == "已过期"
    assert detail_status == 200
    assert json.loads(detail_body)["stale"] is True
    assert apply_status == 500
    assert json.loads(apply_body)["error_code"] == "workflow_error"


def test_local_portal_search_rebuilds_catalog_after_workspace_commit(tmp_path: Path) -> None:
    workspace, paths, repositories = _workspace_with_typed_pages(tmp_path)
    portal = LocalPortalApp(workspace)

    searched = json.loads(
        portal.dispatch(
            f"/api/pages?q={quote('cache policy')}&kind=code&project={repositories['alpha']}"
        )[2]
    )
    assert paths["code"] in {item["path"] for item in searched["items"]}

    added = "wiki/pages/later.md"
    (workspace / added).write_text("# Later\n\nNew page\n", encoding="utf-8")
    Workspace.open(workspace).version_store.commit_paths((added,), "test: add later page")

    summary = json.loads(portal.dispatch("/api/summary")[2])
    assert summary["applied_page_count"] == 9


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


def test_local_portal_http_host_methods_head_and_security_headers(tmp_path: Path) -> None:
    workspace = _workspace_with_pages(tmp_path, count=1)
    server = LocalPortalServer(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

        connection.request("GET", "/", headers={"Host": "portal.example"})
        forbidden = connection.getresponse()
        assert forbidden.status == 403
        forbidden.read()

        connection.request("POST", "/", headers={"Host": f"localhost:{port}"})
        cross_site = connection.getresponse()
        assert cross_site.status == 403
        cross_site.read()

        connection.request("PUT", "/", headers={"Host": f"localhost:{port}"})
        method = connection.getresponse()
        assert method.status == 405
        assert method.getheader("Allow") == "GET, HEAD, POST"
        method.read()

        connection.request("HEAD", "/app.js", headers={"Host": f"127.0.0.1:{port}"})
        head = connection.getresponse()
        assert head.status == 200
        assert int(head.getheader("Content-Length") or "0") > 0
        assert head.read() == b""
        assert head.getheader("Cache-Control") == "no-store"
        assert head.getheader("X-Content-Type-Options") == "nosniff"
        assert head.getheader("Cross-Origin-Resource-Policy") == "same-origin"
        assert head.getheader("Referrer-Policy") == "no-referrer"
        assert head.getheader("X-Frame-Options") == "DENY"
        assert head.getheader("Content-Security-Policy") == (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'"
        )
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_portal_serve_cli_help_does_not_start_server() -> None:
    from typer.testing import CliRunner

    from memoryforge.interface.cli import app

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
            "---\n"
            f'summary: "3 verified code symbols in module fixture/page-{index:02}."\n'
            "---\n\n"
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
            ((f"wiki/pages/page-{index:02}.md", imported.source_id) for index in range(count)),
        )
    return workspace_root


def _workspace_with_typed_pages(
    tmp_path: Path,
) -> tuple[Path, dict[str, str], dict[str, str]]:
    workspace = tmp_path / "typed-workspace"
    Workspace.initialize(workspace)
    source_root = tmp_path / "typed-sources"
    source_root.mkdir()
    specifications = {
        "alpha_code": (
            "alpha.md",
            "# Store\n\nThe cache policy lives in src/store.py.\n",
            ("code", "python"),
        ),
        "beta_code": (
            "beta.md",
            "# Service\n\nThe beta service starts here.\n",
            ("code", "python"),
        ),
        "conversation": (
            "conversation.md",
            "# Fix cache\n\nUse src/store.py. Store is only an unqualified name.\n",
            ("conversation", "platform:codex", "unverified"),
        ),
        "feishu": (
            "feishu.md",
            "# Storage handbook\n\nThe storage handbook references the cache decision.\n",
            ("feishu",),
        ),
        "note": ("note.md", "# Personal note\n\nRemember the release checklist.\n", ("personal",)),
        "pending": (
            "pending.md",
            "# Pending AI chat\n\nThis conversation has not been applied.\n",
            ("conversation", "platform:codex", "unverified"),
        ),
    }
    imported = {}
    for key, (name, content, tags) in specifications.items():
        source = source_root / name
        source.write_text(content, encoding="utf-8")
        imported[key] = import_local_file(
            workspace,
            source,
            source_root=source_root,
            tags=tags,
        )

    repositories = {"alpha": "1" * 64, "beta": "2" * 64}
    paths = {
        "project": f"wiki/pages/repository-{repositories['alpha'][:12]}.md",
        "module": f"wiki/pages/code/{repositories['alpha'][:12]}/src.md",
        "code": f"wiki/pages/code/{repositories['alpha'][:12]}/src/store.md",
        "beta_project": f"wiki/pages/repository-{repositories['beta'][:12]}.md",
        "beta_code": f"wiki/pages/code/{repositories['beta'][:12]}/service.md",
        "conversation": "wiki/pages/conversation.md",
        "feishu": "wiki/pages/feishu.md",
        "note": "wiki/pages/note.md",
    }
    pages = {
        paths["project"]: (
            "---\n"
            'title: "alpha 项目总览"\n'
            'summary: "alpha 项目的确定性入口。"\n'
            'tags: ["repository", "generated"]\n'
            "generated: repository_overview\n"
            f"repository_id: {repositories['alpha']}\n"
            "---\n\n"
            "# alpha 项目总览\n\n"
            "## 模块\n\n"
            "[src](code/111111111111/src.md)\n"
        ),
        paths["module"]: (
            "---\n"
            'title: "src"\n'
            'summary: "src 模块导航。"\n'
            "generated: code_module_overview\n"
            f"repository_id: {repositories['alpha']}\n"
            'tags: ["code", "module"]\n'
            "---\n\n"
            "# src\n\n"
            "## Responsibilities\n\n"
            "存储模块导航。\n\n"
            "## Representative files\n\n"
            "- `src/store.py`\n"
        ),
        paths["code"]: (
            "---\n"
            'title: "Store"\n'
            'summary: "Python code: Store owns the cache policy."\n'
            "generated: code_wiki\n"
            f"repository_id: {repositories['alpha']}\n"
            'tags: ["code", "module", "python"]\n'
            "---\n\n"
            "# Store\n\n"
            "## Responsibilities\n\n"
            "Own the cache policy.\n\n"
            "## Entry points and handlers\n\n"
            "- `src.store.Store.get`\n\n"
            "## Module dependencies\n\n"
            "- `src.cache`\n\n"
            "## Representative files\n\n"
            "- `src/store.py`\n\n"
            "## Verified symbols\n\n"
            "- `pkg.Store`\n\n"
            "## Sources\n\n"
            "Fixed Commit evidence.\n"
        ),
        paths["beta_project"]: (
            "---\n"
            'title: "beta 项目总览"\n'
            'summary: "beta 项目的确定性入口。"\n'
            'tags: ["repository", "generated"]\n'
            "generated: repository_overview\n"
            f"repository_id: {repositories['beta']}\n"
            "---\n\n"
            "# beta 项目总览\n"
        ),
        paths["beta_code"]: (
            "---\n"
            'title: "Beta service"\n'
            'summary: "Python code: beta service."\n'
            "generated: code_wiki\n"
            f"repository_id: {repositories['beta']}\n"
            'tags: ["code", "python"]\n'
            "---\n\n"
            "# Beta service\n\n"
            "## Verified symbols\n\n"
            "- `beta.Service`\n"
        ),
        paths["conversation"]: (
            "---\n"
            'title: "修复缓存会话"\n'
            'summary: "会话建议检查 src/store.py。"\n'
            'tags: ["conversation", "platform:codex", "unverified"]\n'
            "---\n\n"
            "# 修复缓存会话\n\n"
            "## Conversation notes (unverified)\n\n"
            "结论：检查 `src/store.py`。Store 只是模糊同名，不是完整的 `pkg Store`。\n\n"
            "## User prompts (search only)\n\n"
            "为什么缓存失效？\n"
        ),
        paths["feishu"]: (
            "---\n"
            'title: "存储手册"\n'
            'summary: "飞书同步的存储手册。"\n'
            'tags: ["feishu"]\n'
            "---\n\n"
            "# 存储手册\n\n"
            "## 文档目录\n\n"
            "- [缓存会话](conversation.md)\n"
        ),
        paths["note"]: (
            "---\n"
            'title: "发布清单"\n'
            'summary: "普通本地笔记。"\n'
            'tags: ["personal"]\n'
            "updated: 2026-08-12\n"
            "---\n\n"
            "# 发布清单\n\n"
            "## 正文\n\n"
            "发布前执行测试。\n"
        ),
    }
    for path, content in pages.items():
        destination = workspace / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    Workspace.open(workspace).version_store.commit_paths(("wiki/pages",), "test: typed pages")

    database = workspace / ".memoryforge/index.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO git_repositories(
                repository_id, name, checkout_path, remote_name, remote_url,
                sensitivity, registered_at, last_synced_commit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    repositories["alpha"],
                    "alpha",
                    "/private/checkouts/alpha",
                    "origin",
                    "https://token@example.invalid/alpha.git",
                    "local_only",
                    "2026-08-12T00:00:00+00:00",
                    "a" * 40,
                ),
                (
                    repositories["beta"],
                    "beta",
                    "/private/checkouts/beta",
                    "origin",
                    "https://token@example.invalid/beta.git",
                    "local_only",
                    "2026-08-12T00:00:00+00:00",
                    "b" * 40,
                ),
            ),
        )
        versions = {
            key: int(
                connection.execute(
                    """
                    SELECT versions.id
                    FROM source_versions AS versions
                    JOIN sources ON sources.id = versions.source_id
                    WHERE sources.source_id = ? AND versions.is_current = 1
                    """,
                    (result.source_id,),
                ).fetchone()[0]
            )
            for key, result in imported.items()
        }
        connection.executemany(
            "INSERT INTO applied_source_versions(source_id, source_version_id) VALUES (?, ?)",
            (
                (imported[key].source_id, versions[key])
                for key in ("alpha_code", "beta_code", "conversation", "feishu", "note")
            ),
        )
        connection.executemany(
            """
            INSERT INTO git_source_revisions(
                source_version_id, repository_id, relative_path, commit_sha
            ) VALUES (?, ?, ?, ?)
            """,
            (
                (versions["alpha_code"], repositories["alpha"], "src/store.py", "a" * 40),
                (versions["beta_code"], repositories["beta"], "service.py", "b" * 40),
            ),
        )
        connection.executemany(
            "INSERT INTO git_code_modules(repository_id, relative_path) VALUES (?, ?)",
            ((repositories["alpha"], "src"), (repositories["beta"], ".")),
        )
        connection.executemany(
            "INSERT INTO page_sources(page_path, source_id) VALUES (?, ?)",
            (
                (paths["code"], imported["alpha_code"].source_id),
                (paths["beta_code"], imported["beta_code"].source_id),
                (paths["conversation"], imported["conversation"].source_id),
                (paths["feishu"], imported["feishu"].source_id),
                (paths["note"], imported["note"].source_id),
            ),
        )
        connection.execute(
            """
            INSERT INTO wiki_facts(
                fact_id, page_path, repository_id, source_id, source_version,
                locator, section_path, quote, routing_text, symbol, relation_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fact-store",
                paths["code"],
                repositories["alpha"],
                imported["alpha_code"].source_id,
                versions["alpha_code"],
                "chars:0-5",
                "Store",
                "Store",
                "cache store",
                "pkg.Store",
                "contains",
            ),
        )
    return workspace, paths, repositories


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
