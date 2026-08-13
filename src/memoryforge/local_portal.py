"""Local HTTP knowledge portal for one MemoryForge Workspace."""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import webbrowser
from contextlib import suppress
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

from memoryforge.errors import MemoryForgeError
from memoryforge.lifecycle import get_update, list_updates, reject_changeset
from memoryforge.portal_assets import APP_CSS, CONTROL_JS, INDEX_HTML
from memoryforge.portal_catalog import PortalCatalog
from memoryforge.portal_jobs import (
    PortalJobManager,
    automation_status,
    configure_automation,
)
from memoryforge.provider import OpenAICompatibleProvider
from memoryforge.query import answer_question
from memoryforge.showcase import _display_wiki_text, _markdown_document, _markdown_html
from memoryforge.workspace import (
    Workspace,
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
)

_HOST = "127.0.0.1"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
_JSON = "application/json"
_HTML = "text/html; charset=utf-8"
_CSS = "text/css; charset=utf-8"
_JAVASCRIPT = "text/javascript; charset=utf-8"
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024 + 64 * 1024
_PAGE_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_HOST_HEADER = re.compile(r"^(?:127\.0\.0\.1|localhost)(?::\d{1,5})?$", re.IGNORECASE)
_HEADING_TRANSLATIONS = {
    "Responsibilities": "职责",
    "Entry points and handlers": "入口与处理器",
    "Module dependencies": "依赖模块",
    "Representative files": "代表文件",
    "Verified symbols": "主要类型与方法",
    "Sources": "代码依据",
    "Module": "模块信息",
    "Code outline": "文件用途",
    "Verified facts": "已验证事实",
    "Conversation notes (unverified)": "会话结论（未验证）",
    "Assistant conclusions": "首要结论",
    "User prompts (search only)": "用户问题线索",
    "Related pages": "相关知识",
}
_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MemoryForge 本地知识库</title>
<script>
try {
  const theme=localStorage.getItem("memoryforge-theme");
  if(theme)document.documentElement.dataset.theme=theme
} catch(_){}
</script>
<style>
:root{color-scheme:light;--bg:#f7f7f5;--panel:#fff;--soft:#f1f3f2;--fg:#202522;
  --muted:#6e7772;--line:#dde2df;--accent:#247a6b;--accent-soft:#dff3ed;
  --link:#315fbd;--code:#f3f5f4}
:root[data-theme=dark]{color-scheme:dark;--bg:#0f1211;--panel:#151918;--soft:#1b211f;
  --fg:#e8ecea;--muted:#9aa5a0;--line:#2b3431;--accent:#68cbb6;
  --accent-soft:#173a32;--link:#9ab5ff;--code:#0c0f0e}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;
  --bg:#0f1211;--panel:#151918;--soft:#1b211f;--fg:#e8ecea;--muted:#9aa5a0;
  --line:#2b3431;--accent:#68cbb6;--accent-soft:#173a32;--link:#9ab5ff;--code:#0c0f0e}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 system-ui,sans-serif}
button,input{font:inherit;color:inherit}
button{cursor:pointer}
.topbar{position:sticky;top:0;z-index:20;display:grid;
  grid-template-columns:auto minmax(260px,680px) auto;align-items:center;gap:22px;
  min-height:64px;padding:9px 22px;background:color-mix(in srgb,var(--bg) 90%,transparent);
  border-bottom:1px solid var(--line);backdrop-filter:blur(16px)}
.brand{display:flex;align-items:center;gap:10px;color:var(--fg);font-weight:700;text-decoration:none}
.brand-mark{display:grid;width:34px;height:34px;place-items:center;color:#fff;background:var(--accent);
  border-radius:9px;font-size:12px;letter-spacing:.06em}
.brand small{display:block;color:var(--muted);font-size:10px;font-weight:500;
  letter-spacing:.08em;text-transform:uppercase}
.search-wrap{display:flex;align-items:center;height:40px;padding:0 10px;background:var(--panel);
  border:1px solid var(--line);border-radius:9px}
#search{flex:1;min-width:0;background:transparent;border:0;outline:0}
kbd{padding:2px 6px;color:var(--muted);background:var(--soft);border:1px solid var(--line);
  border-radius:5px;font-size:11px}
.actions{display:flex;gap:6px}
.tool{padding:7px 10px;color:var(--muted);background:var(--panel);border:1px solid var(--line);
  border-radius:8px}
.tool:hover,.tool[aria-pressed=true]{color:var(--accent);background:var(--accent-soft);
  border-color:var(--accent)}
#menu{display:none}
.wiki-shell{display:grid;grid-template-columns:290px minmax(0,820px) 220px;
  justify-content:center;gap:44px;max-width:1500px;min-height:calc(100vh - 64px);
  margin:auto;padding:0 24px}
.explorer{position:sticky;top:64px;align-self:start;height:calc(100vh - 64px);
  padding:22px 12px 30px;overflow:auto;border-right:1px solid var(--line)}
.explorer-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
.explorer-title strong{font-size:13px}.explorer-title span{color:var(--muted);font-size:11px}
#summary{margin-bottom:14px;color:var(--muted);font-size:12px}
#result-count{min-height:22px;padding:3px 6px;color:var(--muted);font-size:11px}
#pages{list-style:none;margin:0;padding:0}
#pages li{margin:2px 0}
.page-link{display:block;width:100%;padding:8px 9px;color:var(--fg);background:transparent;
  border:0;border-radius:7px;text-align:left}
.page-link:hover{background:color-mix(in srgb,var(--accent-soft) 55%,transparent)}
.page-link[aria-selected=true]{color:var(--accent);background:var(--accent-soft);
  box-shadow:inset 3px 0 var(--accent)}
.page-link strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.page-link small{display:block;overflow:hidden;color:var(--muted);font-size:10px;
  text-overflow:ellipsis;white-space:nowrap}
.pagination{display:flex;gap:6px;margin-top:14px}
.pagination button{flex:1;padding:7px;color:var(--muted);background:var(--panel);
  border:1px solid var(--line);border-radius:7px}
button:disabled{opacity:.45;cursor:default}
.reader{min-width:0;padding:42px 0 80px}
.welcome{padding:70px 0;color:var(--muted)}
.welcome .eyebrow,.page-kicker{color:var(--accent);font-size:11px;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase}
.welcome h1,#page-title{margin:8px 0 12px;color:var(--fg);font-size:34px;line-height:1.2;
  letter-spacing:-.03em}
#page-path{display:block;margin-bottom:22px;color:var(--muted);font-size:11px}
#page-summary{margin:0 0 22px;padding:13px 15px;background:var(--accent-soft);
  border-left:3px solid var(--accent);border-radius:0 7px 7px 0}
#page-summary:empty{display:none}
#page-body{font-size:16px;line-height:1.8}
#page-body h1{display:none}
#page-body h2,#page-body h3,#page-body h4{scroll-margin-top:82px;line-height:1.35}
#page-body h2{margin:1.7em 0 .55em;padding-bottom:7px;border-bottom:1px solid var(--line);
  font-size:23px}
#page-body h3{margin:1.5em 0 .5em;font-size:19px}
#page-body a{color:var(--link)}
#page-body pre{margin:16px 0;background:var(--code);border:1px solid var(--line);
  border-radius:8px;padding:14px;overflow:auto}
#page-body :not(pre)>code{padding:2px 5px;background:var(--code);border:1px solid var(--line);
  border-radius:4px;font-size:.88em}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.toc{position:sticky;top:86px;align-self:start;max-height:calc(100vh - 110px);
  padding-top:22px;overflow:auto}
.toc strong{display:block;margin-bottom:8px;color:var(--muted);font-size:11px;
  letter-spacing:.06em;text-transform:uppercase}
#toc-links a{display:block;margin:6px 0;color:var(--muted);font-size:12px;line-height:1.4;
  text-decoration:none}
#toc-links a:hover{color:var(--accent)}
#toc-links .depth-3{padding-left:10px}
.empty{color:var(--muted)}
body.reader-mode .explorer,body.reader-mode .toc{display:none}
body.reader-mode .wiki-shell{display:block;max-width:820px}
body.reader-mode .reader{padding-top:34px}
@media(max-width:1080px){
  .wiki-shell{grid-template-columns:270px minmax(0,760px);gap:32px}
  .toc{display:none}
}
@media(max-width:760px){
  .topbar{grid-template-columns:auto minmax(0,1fr) auto;gap:9px;padding:8px 12px}
  .brand-copy,kbd,.actions #reader{display:none}
  #menu{display:inline-flex}
  .wiki-shell{display:block;padding:0 18px}
  .explorer{display:none}
  body.explorer-open{overflow:hidden}
  body.explorer-open .explorer{position:fixed;z-index:30;inset:64px 0 0;display:block;
    width:100%;height:auto;padding:18px;background:var(--bg);border:0}
  .reader{padding-top:28px}
  .welcome h1,#page-title{font-size:29px}
}
</style>
</head>
<body>
<header class="topbar">
  <a class="brand" href="/"><span class="brand-mark">MF</span>
    <span class="brand-copy">MemoryForge<small>本地知识库</small></span></a>
  <div class="search-wrap"><input id="search" type="search" placeholder="搜索标题、路径或正文…"
    aria-label="搜索知识库"><kbd>⌘K</kbd></div>
  <div class="actions">
    <button id="menu" class="tool" type="button" aria-expanded="false">目录</button>
    <button id="reader" class="tool" type="button" aria-pressed="false">阅读</button>
    <button id="theme" class="tool" type="button" aria-label="切换明暗主题">主题</button></div>
</header>
<main class="wiki-shell">
  <aside class="explorer" id="explorer">
    <div class="explorer-title"><strong>资料目录</strong><span id="page-total"></span></div>
    <div id="summary">正在读取知识库…</div>
    <div id="result-count" aria-live="polite"></div>
    <ul id="pages"></ul>
    <div class="pagination"><button id="prev" type="button">上一页</button>
      <button id="next" type="button">下一页</button></div>
  </aside>
  <section class="reader">
    <div class="welcome" id="welcome"><span class="eyebrow">个人知识库</span>
      <h1>选择一篇资料开始阅读</h1><p>使用左侧目录浏览，或按 ⌘K / Ctrl+K 搜索全部资料。</p></div>
    <article id="viewer" hidden><div class="page-kicker">已审核知识页</div>
      <h1 id="page-title"></h1><code id="page-path"></code>
      <p id="page-summary"></p><div id="page-body"></div></article>
  </section>
  <aside class="toc"><strong>本页目录</strong><nav id="toc-links"></nav></aside>
</main>
<script>
const state={q:"",offset:0,limit:50};
async function fetchJson(url){
  const r=await fetch(url);if(!r.ok)throw new Error("request failed");return r.json()
}
async function loadSummary(){
  const s=await fetchJson("/api/summary");
  const summary=document.getElementById("summary");
  summary.textContent=s.source_count+" 个来源 · 快照 "+s.workspace_commit.slice(0,8);
  document.getElementById("page-total").textContent=s.page_count+" 页";
}
async function loadPages(){
  const q=encodeURIComponent(state.q);
  const url=`/api/pages?limit=${state.limit}&offset=${state.offset}${q?`&q=${q}`:""}`;
  const data=await fetchJson(url);const list=document.getElementById("pages");
  list.textContent="";
  for(const item of data.items){
    const li=document.createElement("li");const button=document.createElement("button");
    button.className="page-link";button.type="button";button.dataset.path=item.path;
    button.setAttribute("aria-selected","false");
    const title=document.createElement("strong");title.textContent=item.title;
    const path=document.createElement("small");path.textContent=item.path.replace("wiki/pages/","");
    button.append(title,path);button.addEventListener("click",()=>openPage(item.path));
    li.append(button);list.append(li)
  }
  document.getElementById("result-count").textContent=state.q?data.total+" 个匹配页面":"";
  document.getElementById("prev").disabled=state.offset===0;
  document.getElementById("next").disabled=state.offset+data.items.length>=data.total
}
async function openPage(path){
  const data=await fetchJson(`/api/page?path=${encodeURIComponent(path)}`);
  document.getElementById("welcome").hidden=true;
  document.getElementById("page-title").textContent=data.title;
  document.getElementById("page-path").textContent=data.path;
  document.getElementById("page-summary").textContent=data.summary||"";
  document.getElementById("page-body").innerHTML=data.html;
  document.getElementById("viewer").hidden=false;
  document.querySelectorAll(".page-link").forEach(button=>
    button.setAttribute("aria-selected",String(button.dataset.path===path)));
  buildToc();history.replaceState(null,"",`#page=${encodeURIComponent(path)}`);
  document.body.classList.remove("explorer-open");
  document.getElementById("menu").setAttribute("aria-expanded","false")
}
function buildToc(){
  const toc=document.getElementById("toc-links");toc.textContent="";
  document.querySelectorAll("#page-body h2,#page-body h3").forEach((heading,index)=>{
    heading.id="section-"+index;const link=document.createElement("a");
    link.href="#"+heading.id;link.textContent=heading.textContent||"";
    if(heading.tagName==="H3")link.className="depth-3";toc.append(link)
  });
  if(!toc.children.length){const empty=document.createElement("span");
    empty.className="empty";empty.textContent="当前页面没有章节";toc.append(empty)}
}
let searchTimer;
document.getElementById("search").addEventListener("input",e=>{
  clearTimeout(searchTimer);searchTimer=setTimeout(()=>{
    state.q=e.target.value;state.offset=0;loadPages()
  },150)
});
document.getElementById("prev").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-state.limit);loadPages()});
document.getElementById("next").addEventListener("click",()=>{state.offset+=state.limit;loadPages()});
document.getElementById("theme").addEventListener("click",()=>{
  const root=document.documentElement;
  const dark=root.dataset.theme?root.dataset.theme==="dark":
    matchMedia("(prefers-color-scheme:dark)").matches;
  const theme=dark?"light":"dark";root.dataset.theme=theme;
  try{localStorage.setItem("memoryforge-theme",theme)}catch(_){}
});
function setReader(active){
  document.body.classList.toggle("reader-mode",active);
  document.getElementById("reader").setAttribute("aria-pressed",String(active))
}
document.getElementById("reader").addEventListener("click",()=>{
  const active=!document.body.classList.contains("reader-mode");setReader(active);
  try{localStorage.setItem("memoryforge-reader-mode",String(active))}catch(_){}
});
document.getElementById("menu").addEventListener("click",()=>{
  const active=document.body.classList.toggle("explorer-open");
  document.getElementById("menu").setAttribute("aria-expanded",String(active))
});
document.addEventListener("keydown",event=>{
  if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){
    event.preventDefault();document.getElementById("search").focus()
  }
  if(event.key==="/"&&!["INPUT","TEXTAREA"].includes(document.activeElement?.tagName)){
    event.preventDefault();document.getElementById("search").focus()
  }
  if(event.key==="Escape"){
    document.body.classList.remove("explorer-open");
    document.getElementById("menu").setAttribute("aria-expanded","false")
  }
});
try{setReader(localStorage.getItem("memoryforge-reader-mode")==="true")}catch(_){}
Promise.all([loadSummary(),loadPages()]).then(()=>{
  if(location.hash.startsWith("#page="))openPage(decodeURIComponent(location.hash.slice(6)))
}).catch(()=>{document.getElementById("summary").textContent="知识库暂时不可用"});
</script>
</body>
</html>
"""


class LocalPortalApp:
    """Testable portal logic: no process, no persistent page cache."""

    def __init__(
        self,
        workspace: Path,
        *,
        provider: OpenAICompatibleProvider | None = None,
        allow_local_llm: bool = False,
    ) -> None:
        self.workspace = Workspace.open_readonly(workspace)
        self.root = self.workspace.root
        self.provider = provider
        self.allow_local_llm = allow_local_llm
        self._catalog_cache: PortalCatalog | None = None
        self._catalog_lock = Lock()
        self.jobs = PortalJobManager(self.root)
        self.csrf_token = secrets.token_urlsafe(32)

    def summary(self) -> dict[str, Any]:
        payload = self._catalog().summary()
        jobs = self.jobs.list_jobs()["items"]
        updates = list_updates(self.root)
        payload.update(
            pending_updates=len(updates),
            active_jobs=sum(item["status"] in {"queued", "running"} for item in jobs),
            failed_jobs=sum(item["status"] == "failed" for item in jobs),
        )
        return payload

    def close(self) -> None:
        self.jobs.close()

    def projects(self) -> dict[str, Any]:
        return self._catalog().list_projects()

    def project(self, repository_id: str) -> dict[str, Any]:
        return self._catalog().project(repository_id)

    def sources(self, kind: str, *, offset: int, limit: int) -> dict[str, Any]:
        _validate_pagination(offset, limit)
        return self._catalog().list_sources(
            kind,
            offset=offset,
            limit=min(limit, _MAX_LIMIT),
        )

    def list_pages(
        self,
        query: str = "",
        *,
        kind: str = "",
        project: str = "",
        parent: str = "",
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        _validate_pagination(offset, limit)
        return self._catalog().list_pages(
            query,
            kind=kind,
            project=project,
            parent=parent,
            offset=offset,
            limit=min(limit, _MAX_LIMIT),
        )

    def page(self, page_path: str) -> dict[str, Any]:
        _validate_page_path(self.root, page_path)
        catalog = self._catalog()
        details = catalog.page_details(page_path)
        content = self.workspace.version_store.read_text_at(catalog.commit, page_path)
        if content is None:
            raise FileNotFoundError("page not found")
        metadata, body = _markdown_document(content)
        rendered_body = _portal_markdown(body)
        details["title"] = _display_wiki_text(str(details["title"]))
        details["summary"] = _display_wiki_text(str(details["summary"]))
        for item in details["structure"]:
            item["title"] = _display_heading(str(item["title"]))
        return {
            **details,
            "content": content,
            "summary": _display_wiki_text(metadata.get("summary", "")),
            "html": _markdown_html(rendered_body),
        }

    def dispatch(self, request_path: str) -> tuple[int, str, bytes]:
        parsed = urlparse(request_path)
        try:
            if parsed.path == "/":
                return 200, _HTML, INDEX_HTML.encode("utf-8")
            if parsed.path == "/app.css":
                return 200, _CSS, APP_CSS.encode("utf-8")
            if parsed.path == "/app.js":
                return 200, _JAVASCRIPT, CONTROL_JS.encode("utf-8")
            if parsed.path == "/api/summary":
                return _json_response(self.summary())
            if parsed.path == "/api/session":
                return _json_response(
                    {
                        "workspace_commit": self.workspace.current_commit(),
                        "csrf_token": self.csrf_token,
                    }
                )
            if parsed.path == "/api/projects":
                return _json_response(self.projects())
            if parsed.path == "/api/project":
                params = parse_qs(parsed.query)
                repository_ids = params.get("id")
                if not repository_ids:
                    raise ValueError("project id is required")
                return _json_response(self.project(repository_ids[0]))
            if parsed.path == "/api/sources":
                params = parse_qs(parsed.query)
                kinds = params.get("kind")
                if not kinds:
                    raise ValueError("source kind is required")
                offset, limit = _pagination(params)
                return _json_response(self.sources(kinds[0], offset=offset, limit=limit))
            if parsed.path == "/api/source":
                params = parse_qs(parsed.query)
                refs = params.get("ref")
                if not refs:
                    raise ValueError("source ref is required")
                return _json_response(self._catalog().source_details(refs[0]))
            if parsed.path == "/api/pages":
                params = parse_qs(parsed.query)
                offset, limit = _pagination(params)
                return _json_response(
                    self.list_pages(
                        params.get("q", [""])[0],
                        kind=params.get("kind", [""])[0],
                        project=params.get("project", [""])[0],
                        parent=params.get("parent", [""])[0],
                        offset=offset,
                        limit=limit,
                    )
                )
            if parsed.path == "/api/page":
                params = parse_qs(parsed.query)
                paths = params.get("path")
                if not paths:
                    raise ValueError("page path is required")
                return _json_response(self.page(paths[0]))
            if parsed.path == "/api/jobs":
                return _json_response(self._with_commit(self.jobs.list_jobs()))
            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.removeprefix("/api/jobs/")
                return _json_response(self._with_commit(self.jobs.get(job_id)))
            if parsed.path == "/api/updates":
                return _json_response(
                    self._with_commit({"items": list_updates(self.root)})
                )
            if parsed.path.startswith("/api/updates/"):
                changeset_id = parsed.path.removeprefix("/api/updates/")
                return _json_response(get_update(self.root, changeset_id))
            if parsed.path == "/api/automation":
                return _json_response(self._with_commit(automation_status(self.root)))
            return self._error_response("not found", status=404)
        except ValueError:
            return self._error_response("invalid request", status=400)
        except FileNotFoundError:
            return self._error_response("page not found", status=404)
        except (
            MemoryForgeError,
            WorkspaceIntegrityError,
            WorkspaceSecurityError,
            sqlite3.Error,
            OSError,
        ):
            return self._error_response("workspace unavailable", status=500)

    def _catalog(self) -> PortalCatalog:
        commit = self.workspace.current_commit()
        with self._catalog_lock:
            if self._catalog_cache is None or self._catalog_cache.commit != commit:
                self._catalog_cache = PortalCatalog(self.workspace, commit)
            return self._catalog_cache

    def _error_response(
        self,
        message: str,
        *,
        status: int,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> tuple[int, str, bytes]:
        payload: dict[str, Any] = {
            "error": message,
            "error_code": error_code or message.replace(" ", "_"),
            "user_message": message,
            "retryable": retryable,
        }
        with suppress(MemoryForgeError, OSError):
            payload["workspace_commit"] = self.workspace.current_commit()
        return _json_response(payload, status=status)

    def dispatch_post(
        self,
        request_path: str,
        payload: dict[str, Any],
    ) -> tuple[int, str, bytes]:
        parsed = urlparse(request_path)
        try:
            if parsed.path == "/api/sources/preview":
                return _json_response(
                    self._with_commit(self.jobs.preview(payload))
                )
            if parsed.path == "/api/sources":
                return _json_response(
                    self._with_commit(self.jobs.submit_source(payload)),
                    status=202,
                )
            if parsed.path.startswith("/api/sources/") and parsed.path.endswith("/refresh"):
                source_id = parsed.path.removeprefix("/api/sources/").removesuffix("/refresh")
                return _json_response(
                    self._with_commit(self.jobs.submit_refresh(source_id)),
                    status=202,
                )
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/cancel")
                return _json_response(self._with_commit(self.jobs.cancel(job_id)))
            if parsed.path.startswith("/api/updates/") and parsed.path.endswith("/reject"):
                changeset_id = parsed.path.removeprefix("/api/updates/").removesuffix("/reject")
                return _json_response(
                    self._with_commit(reject_changeset(self.root, changeset_id))
                )
            if (
                parsed.path.startswith("/api/updates/")
                and parsed.path.endswith("/approve-and-apply")
            ):
                changeset_id = (
                    parsed.path.removeprefix("/api/updates/")
                    .removesuffix("/approve-and-apply")
                )
                update = get_update(self.root, changeset_id)
                return _json_response(
                    self._with_commit(
                        self.jobs.submit_apply(changeset_id, str(update["name"]))
                    ),
                    status=202,
                )
            if parsed.path == "/api/automation":
                return _json_response(
                    self._with_commit(configure_automation(self.root, payload))
                )
            if parsed.path == "/api/ask":
                question = payload.get("question")
                if not isinstance(question, str) or not question.strip():
                    raise ValueError("question is required")
                answer = answer_question(
                    self.root,
                    question.strip(),
                    max_citations=3,
                    provider=self.provider,
                    allow_local=self.allow_local_llm,
                )
                return _json_response(
                    self._with_commit(
                        {
                            "status": answer["status"],
                            "answer": answer["answer"],
                            "citations": [
                                {
                                    "quote": citation["quote"],
                                    "section": citation["section_path"],
                                }
                                for citation in answer["citations"]
                            ],
                        }
                    )
                )
            return self._error_response("not found", status=404)
        except ValueError:
            return self._error_response(
                "invalid request",
                status=400,
                error_code="invalid_request",
                retryable=False,
            )
        except FileNotFoundError:
            return self._error_response(
                "not found",
                status=404,
                error_code="not_found",
                retryable=False,
            )
        except (
            MemoryForgeError,
            WorkspaceIntegrityError,
            WorkspaceSecurityError,
            sqlite3.Error,
            OSError,
        ):
            return self._error_response(
                "operation failed safely",
                status=500,
                error_code="workflow_error",
                retryable=True,
            )

    def submit_upload(
        self,
        filename: str,
        content: bytes,
        kind: str,
        *,
        public: bool,
        confirm_public: bool,
    ) -> tuple[int, str, bytes]:
        try:
            return _json_response(
                self._with_commit(
                    self.jobs.submit_upload(
                        filename,
                        content,
                        kind,
                        public=public,
                        confirm_public=confirm_public,
                    )
                ),
                status=202,
            )
        except ValueError:
            return self._error_response(
                "invalid upload",
                status=400,
                error_code="invalid_upload",
                retryable=False,
            )

    def _with_commit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"workspace_commit": self.workspace.current_commit(), **payload}


def make_handler(portal: LocalPortalApp) -> type[BaseHTTPRequestHandler]:
    """Return a loopback-only handler for one LocalPortalApp."""

    class LocalPortalHandler(BaseHTTPRequestHandler):
        portal: LocalPortalApp

        def do_GET(self) -> None:
            self._dispatch(send_body=True)

        def do_HEAD(self) -> None:
            self._dispatch(send_body=False)

        def do_POST(self) -> None:
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin", "")
            port = int(getattr(self.server, "server_port", 0))
            if not _host_is_allowed(host, port) or not _allowed_origin(origin, host):
                self._send_json_error(403, "cross_site_request", "拒绝跨站写入请求。")
                return
            if not hmac.compare_digest(
                self.headers.get("X-MemoryForge-CSRF", ""),
                self.portal.csrf_token,
            ):
                self._send_json_error(403, "invalid_csrf", "页面会话已失效，请刷新页面。")
                return
            try:
                content_length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send_json_error(400, "invalid_request", "请求长度无效。")
                return
            raw_content_type = self.headers.get("Content-Type", "")
            content_type = raw_content_type.split(";", 1)[0].strip()
            maximum = (
                _MAX_UPLOAD_BYTES if content_type == "multipart/form-data" else _MAX_REQUEST_BYTES
            )
            if not 0 < content_length <= maximum:
                self._send_json_error(413, "request_too_large", "请求内容过大。")
                return
            raw = self.rfile.read(content_length)
            if content_type == "multipart/form-data":
                if self.path != "/api/sources":
                    self._send_json_error(404, "not_found", "接口不存在。")
                    return
                try:
                    filename, content, kind, public, confirm_public = _parse_upload(
                        raw_content_type,
                        raw,
                    )
                    status, response_type, body = self.portal.submit_upload(
                        filename,
                        content,
                        kind,
                        public=public,
                        confirm_public=confirm_public,
                    )
                except ValueError:
                    self._send_json_error(400, "invalid_upload", "上传文件无效。")
                    return
                self._send(status, response_type, body, send_body=True)
                return
            if content_type != "application/json":
                self._send_json_error(415, "invalid_content_type", "只接受 JSON 或文件上传。")
                return
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json_error(400, "invalid_json", "JSON 内容无效。")
                return
            if not isinstance(payload, dict):
                self._send_json_error(400, "invalid_json", "JSON 必须是对象。")
                return
            try:
                status, response_type, body = self.portal.dispatch_post(self.path, payload)
            except Exception:
                status, response_type, body = _json_response(
                    {
                        "error": "internal server error",
                        "error_code": "internal_error",
                        "user_message": "本地操作失败。",
                        "retryable": True,
                    },
                    status=500,
                )
            self._send(status, response_type, body, send_body=True)

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:
            self._method_not_allowed()

        def do_TRACE(self) -> None:
            self._method_not_allowed()

        def do_CONNECT(self) -> None:
            self._method_not_allowed()

        def _dispatch(self, *, send_body: bool) -> None:
            port = int(getattr(self.server, "server_port", 0))
            if not _host_is_allowed(self.headers.get("Host", ""), port):
                status, content_type, body = _json_response(
                    {
                        "error": "forbidden host",
                        "error_code": "forbidden_host",
                        "user_message": "拒绝非本机请求。",
                        "retryable": False,
                    },
                    status=403,
                )
                self._send(status, content_type, body, send_body=send_body)
                return
            try:
                status, content_type, body = self.portal.dispatch(self.path)
            except Exception:
                status, content_type, body = _json_response(
                    {"error": "internal server error"},
                    status=500,
                )
            self._send(status, content_type, body, send_body=send_body)

        def _method_not_allowed(self) -> None:
            status, content_type, body = _json_response(
                {"error": "method not allowed"},
                status=405,
            )
            self._send(status, content_type, body, send_body=True)

        def _send_json_error(self, status: int, code: str, message: str) -> None:
            response_status, content_type, body = _json_response(
                {
                    "error": message,
                    "error_code": code,
                    "user_message": message,
                    "retryable": False,
                },
                status=status,
            )
            self._send(response_status, content_type, body, send_body=True)

        def _send(
            self,
            status: int,
            content_type: str,
            body: bytes,
            *,
            send_body: bool,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                "frame-ancestors 'none'",
            )
            if status == 405:
                self.send_header("Allow", "GET, HEAD, POST")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    LocalPortalHandler.portal = portal
    return LocalPortalHandler


class LocalPortalServer(ThreadingHTTPServer):
    """Loopback-only server for one Workspace."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        workspace: Path,
        port: int = 8765,
        *,
        provider: OpenAICompatibleProvider | None = None,
        allow_local_llm: bool = False,
    ) -> None:
        self.app = LocalPortalApp(
            workspace,
            provider=provider,
            allow_local_llm=allow_local_llm,
        )
        super().__init__((_HOST, port), make_handler(self.app))

    def server_close(self) -> None:
        self.app.close()
        super().server_close()


def serve_local_portal(
    workspace: Path,
    port: int = 8765,
    *,
    open_browser: bool = False,
    provider: OpenAICompatibleProvider | None = None,
    allow_local_llm: bool = False,
) -> None:
    server = LocalPortalServer(
        workspace,
        port=port,
        provider=provider,
        allow_local_llm=allow_local_llm,
    )
    actual_port = int(server.server_address[1])
    url = f"http://{_HOST}:{actual_port}"
    print(f"MemoryForge local Wiki: {url} (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _json_response(payload: dict[str, Any], *, status: int = 200) -> tuple[int, str, bytes]:
    body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    return status, _JSON, body


def _pagination(params: dict[str, list[str]]) -> tuple[int, int]:
    try:
        offset = int(params.get("offset", ["0"])[0])
        limit = int(params.get("limit", [str(_DEFAULT_LIMIT)])[0])
    except ValueError as exc:
        raise ValueError("invalid pagination") from exc
    _validate_pagination(offset, limit)
    return offset, min(limit, _MAX_LIMIT)


def _validate_pagination(offset: int, limit: int) -> None:
    if offset < 0:
        raise ValueError("offset must not be negative")
    if limit < 1:
        raise ValueError("limit must be at least 1")


def _allowed_host(host: str) -> bool:
    return _HOST_HEADER.fullmatch(host.strip()) is not None


def _host_is_allowed(host: str, port: int) -> bool:
    return host.strip().casefold() in {
        "127.0.0.1",
        "localhost",
        f"127.0.0.1:{port}",
        f"localhost:{port}",
    }


def _allowed_origin(origin: str, host: str) -> bool:
    parsed = urlparse(origin)
    return (
        parsed.scheme == "http"
        and parsed.netloc.casefold() == host.strip().casefold()
        and _allowed_host(parsed.netloc)
        and not parsed.path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _parse_upload(
    content_type: str,
    body: bytes,
) -> tuple[str, bytes, str, bool, bool]:
    message = BytesParser(policy=policy.default).parsebytes(
        (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("ascii")
        + body
    )
    filename = ""
    content = b""
    kind = ""
    public = False
    confirm_public = False
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "file":
            filename = part.get_filename() or ""
            payload = part.get_payload(decode=True)
            content = payload if isinstance(payload, bytes) else b""
        elif name == "kind":
            payload = part.get_payload(decode=True)
            kind = payload.decode("utf-8").strip() if isinstance(payload, bytes) else ""
        elif name in {"public", "confirm_public"}:
            payload = part.get_payload(decode=True)
            enabled = (
                payload.decode("utf-8").strip().lower() == "true"
                if isinstance(payload, bytes)
                else False
            )
            if name == "public":
                public = enabled
            else:
                confirm_public = enabled
    if not filename or not content or kind not in {"file", "codex"}:
        raise ValueError("invalid upload")
    return filename, content, kind, public, confirm_public


def _portal_markdown(markdown: str) -> str:
    return _HEADING_LINE.sub(
        lambda match: f"{match.group(1)} {_display_heading(match.group(2))}",
        markdown,
    )


def _display_heading(value: str) -> str:
    return _HEADING_TRANSLATIONS.get(value, _display_wiki_text(value))


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
