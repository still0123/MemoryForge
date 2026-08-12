"""Read-only local HTTP portal for a large MemoryForge Workspace."""

from __future__ import annotations

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

from memoryforge.errors import MemoryForgeError
from memoryforge.showcase import _markdown_html
from memoryforge.workspace import (
    Workspace,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    _connect_readonly,
    find_applied_page_paths,
)

_HOST = "127.0.0.1"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
_JSON = "application/json"
_HTML = "text/html; charset=utf-8"
_PAGE_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MemoryForge 本地 Wiki</title>
<style>
:root{color-scheme:light dark;--bg:#f7f8fa;--fg:#1f2328;--muted:#59636e;
  --line:#d8dee4;--accent:#0969da;--code:#f6f8fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);background:#fff}
h1{font-size:22px;margin:0}
main{max-width:1080px;margin:0 auto;padding:20px}
#summary{color:var(--muted);margin-top:6px}
#controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:16px 0}
input[type=search]{flex:1;min-width:220px;padding:8px 10px;
  border:1px solid var(--line);border-radius:6px}
button{padding:8px 12px;border:1px solid var(--line);border-radius:6px;
  background:#fff;cursor:pointer}
button:disabled{opacity:.45;cursor:default}
ul{list-style:none;margin:0;padding:0;border:1px solid var(--line);
  border-radius:8px;background:#fff}
li{border-bottom:1px solid var(--line);padding:10px 14px}
li:last-child{border-bottom:0}
a{color:var(--accent);text-decoration:none;cursor:pointer}
#page-body{background:#fff;border:1px solid var(--line);border-radius:8px;padding:18px 22px}
pre{background:var(--code);padding:12px;border-radius:6px;overflow:auto}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
blockquote{border-left:3px solid var(--line);margin-left:0;padding-left:12px;color:var(--muted)}
</style>
</head>
<body>
<header><h1>本地 Wiki</h1><div id="summary"></div></header>
<main>
<div id="controls">
  <input id="search" type="search" placeholder="搜索页面路径" aria-label="搜索页面路径">
  <button id="prev" type="button" aria-label="上一页">上一页</button>
  <button id="next" type="button" aria-label="下一页">下一页</button>
</div>
<ul id="pages"></ul>
<article id="viewer" hidden>
  <button id="back" type="button">返回列表</button>
  <div id="page-body"></div>
</article>
</main>
<script>
const state={q:"",offset:0,limit:50};
async function fetchJson(url){
  const r=await fetch(url);if(!r.ok)throw new Error("request failed");return r.json()
}
async function loadSummary(){
  const s=await fetchJson("/api/summary");
  const summary=document.getElementById("summary");
  summary.textContent=s.page_count+" 页 · "+s.source_count+" 来源 · "+
    s.workspace_commit.slice(0,8);
}
async function loadPages(){
  const q=encodeURIComponent(state.q);
  const url=`/api/pages?limit=${state.limit}&offset=${state.offset}${q?`&q=${q}`:""}`;
  const data=await fetchJson(url);const list=document.getElementById("pages");
  list.textContent="";
  for(const item of data.items){
    const li=document.createElement("li");const a=document.createElement("a");
    a.textContent=item.title;a.addEventListener("click",()=>openPage(item.path));
    li.append(a);list.append(li)
  }
  document.getElementById("prev").disabled=state.offset===0;
  document.getElementById("next").disabled=state.offset+data.items.length>=data.total
}
async function openPage(path){
  const data=await fetchJson(`/api/page?path=${encodeURIComponent(path)}`);
  document.getElementById("page-body").innerHTML=data.html;
  document.getElementById("pages").hidden=true;document.getElementById("viewer").hidden=false
}
function backToList(){
  document.getElementById("viewer").hidden=true;document.getElementById("pages").hidden=false
}
document.getElementById("search").addEventListener("input",e=>{state.q=e.target.value;state.offset=0;loadPages()});
document.getElementById("prev").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-state.limit);loadPages()});
document.getElementById("next").addEventListener("click",()=>{state.offset+=state.limit;loadPages()});
document.getElementById("back").addEventListener("click",backToList);
loadSummary();loadPages();
</script>
</body>
</html>
"""


class LocalPortalApp:
    """Testable portal logic: no process, no persistent page cache."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Workspace.open_readonly(workspace)
        self.root = self.workspace.root

    def summary(self) -> dict[str, Any]:
        commit = self.workspace.current_commit()
        with _connect_readonly(self.workspace.index_path) as connection:
            current_sources = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT source_id) FROM source_versions WHERE is_current = 1"
                ).fetchone()[0]
            )
            applied_sources = int(
                connection.execute("SELECT COUNT(*) FROM applied_source_versions").fetchone()[0]
            )
        return {
            "workspace_commit": commit,
            "page_count": len(self._applied_page_paths()),
            "source_count": current_sources,
            "applied_source_count": applied_sources,
        }

    def list_pages(
        self,
        query: str = "",
        *,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset must not be negative")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        limit = min(limit, _MAX_LIMIT)
        all_paths = self._applied_page_paths()
        matches: list[str]
        if query.strip():
            needle = query.casefold()
            path_matches = [path for path in all_paths if needle in path.casefold()]
            try:
                fts_matches = list(find_applied_page_paths(self.root, query, limit=_MAX_LIMIT))
            except (ValueError, sqlite3.Error, WorkspaceIntegrityError, WorkspaceSecurityError):
                fts_matches = []
            applied = set(all_paths)
            fts_matches = [path for path in fts_matches if path in applied]
            matches = list(dict.fromkeys([*path_matches, *fts_matches]))
        else:
            matches = all_paths
        total = len(matches)
        selected = matches[offset : offset + limit]
        contents = self.workspace.version_store.read_wiki_texts_at(
            self.workspace.current_commit(),
            paths=tuple(selected),
        )
        items = [
            {"path": path, "title": _page_title(contents.get(path), path)}
            for path in selected
        ]
        return {"total": total, "offset": offset, "limit": limit, "items": items}

    def page(self, page_path: str) -> dict[str, Any]:
        _validate_page_path(self.root, page_path)
        commit = self.workspace.current_commit()
        content = self.workspace.version_store.read_text_at(commit, page_path)
        if content is None:
            raise FileNotFoundError("page not found")
        title_match = _PAGE_TITLE.search(content)
        return {
            "path": page_path,
            "title": title_match.group(1) if title_match else Path(page_path).stem,
            "content": content,
            "html": _markdown_html(content),
        }

    def dispatch(self, request_path: str) -> tuple[int, str, bytes]:
        parsed = urlparse(request_path)
        try:
            if parsed.path == "/":
                return 200, _HTML, _INDEX_HTML.encode("utf-8")
            if parsed.path == "/api/summary":
                return _json_response(self.summary())
            if parsed.path == "/api/pages":
                params = parse_qs(parsed.query)
                try:
                    offset = int(params.get("offset", ["0"])[0])
                    limit = int(params.get("limit", [str(_DEFAULT_LIMIT)])[0])
                except ValueError as exc:
                    raise ValueError("invalid pagination") from exc
                return _json_response(
                    self.list_pages(params.get("q", [""])[0], offset=offset, limit=limit)
                )
            if parsed.path == "/api/page":
                params = parse_qs(parsed.query)
                paths = params.get("path")
                if not paths:
                    raise ValueError("page path is required")
                return _json_response(self.page(paths[0]))
            return _json_response({"error": "not found"}, status=404)
        except ValueError:
            return _json_response({"error": "invalid request"}, status=400)
        except FileNotFoundError:
            return _json_response({"error": "page not found"}, status=404)
        except (
            MemoryForgeError,
            WorkspaceIntegrityError,
            WorkspaceSecurityError,
            sqlite3.Error,
            OSError,
        ):
            return _json_response({"error": "workspace unavailable"}, status=500)

    def _applied_page_paths(self) -> list[str]:
        commit = self.workspace.current_commit()
        return [
            path
            for path in self.workspace.version_store.list_wiki_paths_at(commit)
            if _is_stable_wiki_page_path(path)
        ]


def make_handler(portal: LocalPortalApp) -> type[BaseHTTPRequestHandler]:
    """Return a bound GET handler for one LocalPortalApp."""

    class LocalPortalHandler(BaseHTTPRequestHandler):
        portal: LocalPortalApp

        def do_GET(self) -> None:
            try:
                status, content_type, body = self.portal.dispatch(self.path)
            except Exception:
                status, content_type, body = _json_response(
                    {"error": "internal server error"},
                    status=500,
                )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    LocalPortalHandler.portal = portal
    return LocalPortalHandler


class LocalPortalServer(ThreadingHTTPServer):
    """Loopback-only server for one read-only Workspace."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, workspace: Path, port: int = 8765) -> None:
        self.app = LocalPortalApp(workspace)
        super().__init__((_HOST, port), make_handler(self.app))


def serve_local_portal(workspace: Path, port: int = 8765) -> None:
    server = LocalPortalServer(workspace, port=port)
    print(f"MemoryForge local Wiki: http://{_HOST}:{port} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _json_response(payload: dict[str, Any], *, status: int = 200) -> tuple[int, str, bytes]:
    body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    return status, _JSON, body


def _page_title(content: str | None, page_path: str) -> str:
    if content is not None and (match := _PAGE_TITLE.search(content)) is not None:
        return match.group(1)
    stem = Path(page_path).stem
    return stem[:12] + "…" if len(stem) > 16 else stem


def _validate_page_path(root: Path, page_path: str) -> None:
    if not _is_stable_wiki_page_path(page_path):
        raise ValueError("invalid page path")
    current = root
    for part in PurePosixPath(page_path).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("invalid page path")
    resolved = (root / page_path).resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("invalid page path")


def _is_stable_wiki_page_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        "\\" not in path
        and len(parts) >= 3
        and parts[:2] == ("wiki", "pages")
        and path.endswith(".md")
        and all(part not in {"", ".", ".."} for part in parts)
        and str(PurePosixPath(path)) == path
    )
