"""Read-only static Showcase export for one validated Workspace."""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from memoryforge.errors import MemoryForgeError
from memoryforge.models import ApprovalReceipt, ChangeSetStatus, ReviewReceipt, StagedChangeSet
from memoryforge.query import answer_question
from memoryforge.workspace import Workspace

_MARKER_NAME = ".memoryforge-showcase"
_MARKER = "memoryforge-showcase-v1\n"
_OUTPUT_FILES = {_MARKER_NAME, "index.html", "showcase.json"}
_MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
_MAX_STAGED_BYTES = 5 * 1024 * 1024
_MAX_DIFF_CHARS = 200_000
_MERMAID = re.compile(r"```mermaid\r?\n(?P<body>.*?)\r?\n```", re.DOTALL)
_MERMAID_NODE = re.compile(r'^\s*m_(?P<id>[a-f0-9]+)\[(?P<label>".*")\]\s*$')
_MERMAID_EDGE = re.compile(
    r"^\s*m_(?P<source>[a-f0-9]+)\s+-->\|(?P<label>[^|]+)\|\s+"
    r"m_(?P<target>[a-f0-9]+)\s*$"
)
_MERMAID_CONTAINS = re.compile(
    r"^\s*m_(?P<source>[a-f0-9]+)\s+-\.\s*(?P<label>[^.]+)\s*\."
    r"->\s+m_(?P<target>[a-f0-9]+)\s*$"
)
_HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class ShowcaseBuildError(MemoryForgeError):
    """Raised when a static Showcase cannot be exported safely."""


def build_showcase(
    workspace: Path,
    output: Path,
    *,
    evidence: Path | None = None,
    include_local: bool = False,
) -> dict[str, Any]:
    """Build a deterministic static Showcase without modifying the Workspace."""
    opened = Workspace.open_readonly(workspace)
    destination = _validate_output(opened.root, output)
    workspace_commit = opened.current_commit()
    evidence_payload = _read_evidence(evidence) if evidence is not None else None
    snapshot = _build_snapshot(
        opened,
        workspace_commit=workspace_commit,
        evidence_payload=evidence_payload,
        include_local=include_local,
    )
    if opened.current_commit() != workspace_commit:
        raise ShowcaseBuildError("Workspace changed during Showcase build")
    payload = (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    page = _render_html(snapshot).encode("utf-8")
    _publish(destination, payload=payload, page=page)
    return {
        "status": "built",
        "workspace_commit": snapshot["workspace_commit"],
        "files": [_MARKER_NAME, "index.html", "showcase.json"],
        "showcase_sha256": hashlib.sha256(payload).hexdigest(),
        "index_sha256": hashlib.sha256(page).hexdigest(),
    }


def _build_snapshot(
    workspace: Workspace,
    *,
    workspace_commit: str,
    evidence_payload: dict[str, Any] | None,
    include_local: bool,
) -> dict[str, Any]:
    sources, public_source_versions, privacy = _source_snapshot(
        workspace,
        include_local=include_local,
    )
    pages = _wiki_snapshot(
        workspace,
        workspace_commit=workspace_commit,
        include_local=include_local,
    )
    source_details = {
        (str(source["source_id"]), int(version["version_id"])): version
        for source in sources
        for version in source["versions"]
    }
    for page in pages:
        for item in page["evidence"]:
            version = source_details.get((item["source_id"], item["source_version"]))
            if version is not None:
                item["source_title"] = version["title"]
                item["source_category"] = version["category"]
                item["source_sensitivity"] = version["sensitivity"]
    query = _query_snapshot(
        evidence_payload,
        public_source_versions=public_source_versions,
        include_local=include_local,
    )
    benchmark = _benchmark_snapshot(evidence_payload)
    return {
        "schema_version": 1,
        "workspace_commit": workspace_commit,
        "privacy": privacy,
        "sources": sources,
        "wiki": {"pages": pages},
        "changeset": _changeset_snapshot(
            workspace,
            workspace_commit=workspace_commit,
            public_source_versions=public_source_versions,
            include_local=include_local,
        ),
        "query": query,
        "rejection": _rejection_snapshot(workspace),
        "benchmark": benchmark,
        "architecture": _architecture_snapshot(workspace, workspace_commit, pages),
    }


def _source_snapshot(
    workspace: Workspace,
    *,
    include_local: bool,
) -> tuple[list[dict[str, Any]], set[tuple[str, int]], dict[str, Any]]:
    rows = _query_rows(
        workspace,
        """
        SELECT
            sources.source_id,
            versions.id AS version_id,
            versions.title,
            versions.category,
            versions.observed_at,
            versions.sensitivity,
            versions.tags_json,
            versions.is_current,
            blobs.content_sha256
        FROM source_versions AS versions
        JOIN sources ON sources.id = versions.source_id
        JOIN blobs ON blobs.id = versions.blob_id
        ORDER BY sources.source_id, versions.id
        """,
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    public_source_versions: set[tuple[str, int]] = set()
    redacted_source_ids: set[str] = set()
    redacted_version_count = 0
    for row in rows:
        source_id = str(row["source_id"])
        version_id = int(row["version_id"])
        sensitivity = str(row["sensitivity"])
        if sensitivity == "public":
            public_source_versions.add((source_id, version_id))
        elif not include_local:
            redacted_source_ids.add(source_id)
            redacted_version_count += 1
            continue
        try:
            tags = json.loads(str(row["tags_json"]))
        except json.JSONDecodeError as exc:
            raise ShowcaseBuildError("SourceVersion tags are invalid") from exc
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ShowcaseBuildError("SourceVersion tags are invalid")
        grouped[source_id].append(
            {
                "version_id": version_id,
                "title": str(row["title"]),
                "category": str(row["category"]),
                "observed_at": str(row["observed_at"]),
                "sensitivity": sensitivity,
                "tags": tags,
                "current": bool(row["is_current"]),
                "content_sha256": str(row["content_sha256"]),
            }
        )
    sources = [
        {"source_id": source_id, "versions": versions}
        for source_id, versions in sorted(grouped.items())
        if versions
    ]
    return (
        sources,
        public_source_versions,
        {
            "include_local": include_local,
            "redacted_source_count": 0 if include_local else len(redacted_source_ids),
            "redacted_version_count": 0 if include_local else redacted_version_count,
        },
    )


def _wiki_snapshot(
    workspace: Workspace,
    *,
    workspace_commit: str,
    include_local: bool,
) -> list[dict[str, Any]]:
    rows = _query_rows(
        workspace,
        """
        SELECT
            facts.page_path,
            facts.repository_id,
            facts.source_id,
            facts.source_version,
            versions.sensitivity,
            repositories.name AS repository_name
        FROM wiki_facts AS facts
        JOIN source_versions AS versions
          ON versions.id = facts.source_version
        LEFT JOIN git_repositories AS repositories
          ON repositories.repository_id = facts.repository_id
        ORDER BY facts.page_path, facts.source_id, facts.source_version
        """,
    )
    owners: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, set[tuple[str, int]]] = defaultdict(set)
    repositories: dict[str, set[tuple[str, str]]] = defaultdict(set)
    private_pages: set[str] = set()
    for row in rows:
        page_path = str(row["page_path"])
        source_id = str(row["source_id"])
        owners[page_path].add(source_id)
        evidence[page_path].add((source_id, int(row["source_version"])))
        if row["repository_id"] is not None and row["repository_name"] is not None:
            repositories[page_path].add((str(row["repository_id"]), str(row["repository_name"])))
        if str(row["sensitivity"]) != "public":
            private_pages.add(page_path)
    contents = workspace.version_store.read_wiki_texts_at(workspace_commit)
    pages = []
    for path, source_ids in sorted(owners.items()):
        if not include_local and (not source_ids or path in private_pages):
            continue
        content = contents.get(path)
        if content is None:
            workspace.version_store.read_text_at(workspace_commit, path)
            raise ShowcaseBuildError("Showcase Wiki page is missing from its fixed Commit")
        match = _HEADING.search(content)
        pages.append(
            {
                "path": path,
                "title": match.group(1) if match else Path(path).stem,
                "source_ids": sorted(source_ids),
                "repositories": [
                    {"repository_id": repository_id, "name": name}
                    for repository_id, name in sorted(repositories[path], key=lambda item: item[1])
                ],
                "evidence": [
                    {"source_id": source_id, "source_version": source_version}
                    for source_id, source_version in sorted(evidence[path])
                ],
                "content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    return pages


def _changeset_snapshot(
    workspace: Workspace,
    *,
    workspace_commit: str,
    public_source_versions: set[tuple[str, int]],
    include_local: bool,
) -> dict[str, Any] | None:
    applied_root = workspace.staging_dir / "applied"
    if not applied_root.exists():
        return None
    _require_real_directory(applied_root)
    candidates: list[tuple[str, dict[str, Any]]] = []
    for entry in sorted(applied_root.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            raise ShowcaseBuildError("Applied ChangeSet history is unsafe")
        record_payload = _read_hashed_file(entry, "changeset.json", "changeset.sha256")
        try:
            record = StagedChangeSet.model_validate_json(record_payload)
        except ValidationError as exc:
            raise ShowcaseBuildError("Applied ChangeSet metadata is invalid") from exc
        if record.changeset.status is not ChangeSetStatus.PROPOSED:
            raise ShowcaseBuildError("Applied ChangeSet proposal status is invalid")
        source_ids = set(record.changeset.source_ids)
        source_versions = set(record.changeset.source_versions.items())
        local_only = not source_versions or not source_versions <= public_source_versions
        if not include_local and local_only:
            continue
        proposal_sha256 = hashlib.sha256(record_payload).hexdigest()
        review_payload = _read_optional_hashed_file(entry, "review.json", "review.sha256")
        approval_payload = _read_optional_hashed_file(
            entry,
            "approval.json",
            "approval.sha256",
        )
        review = _validate_review(review_payload, record, proposal_sha256)
        approval = _validate_approval(
            approval_payload,
            review_payload,
            record,
            proposal_sha256,
        )
        if review is None or approval is None:
            if include_local and local_only:
                continue
            raise ShowcaseBuildError("Applied ChangeSet lacks review or approval Evidence")
        receipt = _read_json_file(entry / "receipt.json")
        if (
            receipt.get("status") != "APPLIED"
            or receipt.get("changeset_id") != record.changeset.changeset_id
            or re.fullmatch(r"[a-f0-9]{40,64}", str(receipt.get("commit", ""))) is None
        ):
            raise ShowcaseBuildError("Applied ChangeSet receipt is invalid")
        applied_commit = str(receipt["commit"])
        if not workspace.version_store.is_ancestor(
            record.changeset.base_commit,
            applied_commit,
        ) or not workspace.version_store.is_ancestor(applied_commit, workspace_commit):
            raise ShowcaseBuildError("Applied ChangeSet Commit history is invalid")
        diffs = []
        for proposed in record.proposed_files:
            candidate_path = entry / "proposed" / proposed.path
            candidate = _read_regular_file(
                candidate_path,
                max_bytes=_MAX_STAGED_BYTES,
            ).decode("utf-8")
            if hashlib.sha256(candidate.encode("utf-8")).hexdigest() != proposed.content_sha256:
                raise ShowcaseBuildError("Applied ChangeSet candidate hash is invalid")
            applied = workspace.version_store.read_text_at(applied_commit, proposed.path)
            if (
                applied is None
                or hashlib.sha256(applied.encode("utf-8")).hexdigest() != proposed.content_sha256
            ):
                raise ShowcaseBuildError("Applied ChangeSet Commit content is invalid")
            base = workspace.version_store.read_text_at(
                record.changeset.base_commit,
                proposed.path,
            )
            diff = "".join(
                difflib.unified_diff(
                    (base or "").splitlines(keepends=True),
                    candidate.splitlines(keepends=True),
                    fromfile=proposed.path,
                    tofile=f"{proposed.path} (proposed)",
                )
            )
            diffs.append({"path": proposed.path, "diff": diff[:_MAX_DIFF_CHARS]})
        candidates.append(
            (
                str(receipt.get("applied_at", "")),
                {
                    "changeset_id": record.changeset.changeset_id,
                    "base_commit": record.changeset.base_commit,
                    "applied_commit": applied_commit,
                    "source_ids": sorted(source_ids),
                    "lifecycle": {
                        "proposed": True,
                        "reviewed": True,
                        "approved": True,
                        "applied": True,
                    },
                    "unified_diff": diffs,
                },
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]["changeset_id"]))[1]


def _query_snapshot(
    evidence: dict[str, Any] | None,
    *,
    public_source_versions: set[tuple[str, int]],
    include_local: bool,
) -> dict[str, Any] | None:
    if evidence is None:
        return None
    raw = evidence.get("sample_query")
    if not isinstance(raw, dict):
        return None
    question = raw.get("question")
    answer = raw.get("answer")
    citations = raw.get("citations")
    trace = raw.get("trace")
    if (
        not isinstance(question, str)
        or not isinstance(answer, str)
        or not isinstance(citations, list)
        or not isinstance(trace, list)
    ):
        raise ShowcaseBuildError("public evidence query is invalid")
    normalized_citations = []
    for citation in citations:
        if not isinstance(citation, dict):
            raise ShowcaseBuildError("public evidence Citation is invalid")
        source_id = citation.get("source_id")
        source_version = citation.get("source_version")
        if (
            not isinstance(source_id, str)
            or isinstance(source_version, bool)
            or not isinstance(source_version, int)
        ):
            raise ShowcaseBuildError("public evidence Citation source is invalid")
        if not include_local and (source_id, source_version) not in public_source_versions:
            raise ShowcaseBuildError("public evidence cites a non-public SourceVersion")
        normalized_citations.append(
            _allowlist(
                citation,
                ("source_id", "source_version", "locator", "quote", "section_path"),
            )
        )
    normalized_trace = []
    for step in trace:
        if not isinstance(step, dict):
            raise ShowcaseBuildError("public evidence trace is invalid")
        level = step.get("level")
        artifact = step.get("artifact")
        if level not in {"L0", "L1", "L2", "L3"} or not isinstance(artifact, str):
            raise ShowcaseBuildError("public evidence trace is invalid")
        normalized_trace.append({"level": level, "artifact": artifact})
    return {
        "question": question,
        "answer": answer,
        "citations": normalized_citations,
        "trace": normalized_trace,
    }


def _benchmark_snapshot(evidence: dict[str, Any] | None) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "suite": None,
        "case_count": 0,
        "metrics": {},
        "failures": [],
        "abstentions": [],
    }
    if evidence is None:
        return empty
    evaluation = evidence.get("evaluation")
    if not isinstance(evaluation, dict):
        return empty
    metrics = evaluation.get("memoryforge")
    cases = evaluation.get("cases")
    if not isinstance(metrics, dict) or not isinstance(cases, list):
        raise ShowcaseBuildError("public benchmark evidence is invalid")
    allowed_metrics = {
        key: value
        for key, value in sorted(metrics.items())
        if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    failures = []
    abstentions = []
    for case in cases:
        if not isinstance(case, dict):
            raise ShowcaseBuildError("public benchmark case is invalid")
        memoryforge = case.get("memoryforge")
        if not isinstance(memoryforge, dict):
            raise ShowcaseBuildError("public benchmark case result is invalid")
        summary = _allowlist(case, ("id", "category", "error_classification"))
        if memoryforge.get("answer_correct") is False:
            failures.append(summary)
        if case.get("category") == "unanswerable" and memoryforge.get("abstention_correct") is True:
            abstentions.append(summary)
    suite = evaluation.get("suite")
    case_count = evaluation.get("case_count")
    return {
        "suite": suite if isinstance(suite, str) else None,
        "case_count": case_count
        if isinstance(case_count, int) and not isinstance(case_count, bool)
        else 0,
        "metrics": allowed_metrics,
        "failures": failures,
        "abstentions": abstentions,
    }


def _rejection_snapshot(workspace: Workspace) -> dict[str, Any]:
    question = "zzqvnoevidence7f4c2a9d"
    result = answer_question(workspace.root, question, debug=True)
    if result["status"] != "unknown":
        raise ShowcaseBuildError("deterministic rejection probe unexpectedly matched")
    return {
        "question": question,
        "status": result["status"],
        "trace": result.get("trace", []),
    }


def _architecture_snapshot(
    workspace: Workspace,
    workspace_commit: str,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    del workspace, workspace_commit
    for page in pages:
        path = str(page["path"])
        content = str(page["content"])
        match = _MERMAID.search(content)
        if match is None:
            continue
        mermaid = match.group("body")
        nodes: dict[str, str] = {}
        edges = []
        for line in mermaid.splitlines():
            node = _MERMAID_NODE.fullmatch(line)
            if node is not None:
                try:
                    label = json.loads(node.group("label"))
                except json.JSONDecodeError as exc:
                    raise ShowcaseBuildError("Code Wiki Mermaid label is invalid") from exc
                if not isinstance(label, str):
                    raise ShowcaseBuildError("Code Wiki Mermaid label is invalid")
                nodes[node.group("id")] = label
                continue
            edge = _MERMAID_EDGE.fullmatch(line) or _MERMAID_CONTAINS.fullmatch(line)
            if edge is not None:
                edges.append(
                    {
                        "source": edge.group("source"),
                        "target": edge.group("target"),
                        "label": edge.group("label").strip(),
                    }
                )
        return {
            "page_path": path,
            "mermaid": mermaid,
            "nodes": [
                {"id": identifier, "label": label} for identifier, label in sorted(nodes.items())
            ],
            "edges": sorted(
                edges,
                key=lambda item: (item["source"], item["target"], item["label"]),
            ),
        }
    return {"page_path": None, "mermaid": "", "nodes": [], "edges": []}


def _render_html(snapshot: dict[str, Any]) -> str:
    sources = snapshot["sources"]
    pages = snapshot["wiki"]["pages"]
    changeset = snapshot["changeset"]
    query = snapshot["query"]
    benchmark = snapshot["benchmark"]
    architecture = snapshot["architecture"]
    overview_section = _overview_html(snapshot)
    sources_section = _sources_html(sources, snapshot["privacy"])
    query_section = _query_html(query, snapshot["rejection"])
    architecture_section = _architecture_html(architecture)
    short_commit = _short_identifier(snapshot["workspace_commit"], head=8, tail=0)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>我的技术知识库 · MemoryForge</title>
<script>
try {{
  const theme = localStorage.getItem("memoryforge-theme");
  if (theme) document.documentElement.dataset.theme = theme;
}} catch (_) {{}}
</script>
<style>
:root {{ color-scheme:light; --bg:#f7f7f5; --panel:#ffffff; --panel-2:#f1f3f2;
  --line:#dde2df; --text:#202522; --muted:#6e7772; --accent:#247a6b;
  --accent-soft:#dff3ed; --link:#315fbd; --bad:#b42318; --code:#f3f5f4;
  --shadow:0 10px 30px rgba(30,45,38,.06); }}
:root[data-theme="dark"] {{ color-scheme:dark; --bg:#0f1211; --panel:#151918;
  --panel-2:#1b211f; --line:#2b3431; --text:#e8ecea; --muted:#9aa5a0;
  --accent:#68cbb6; --accent-soft:#173a32; --link:#9ab5ff; --bad:#ff9b92;
  --code:#0c0f0e; --shadow:0 14px 38px rgba(0,0,0,.22); }}
@media (prefers-color-scheme:dark) {{
  :root:not([data-theme="light"]) {{ color-scheme:dark; --bg:#0f1211; --panel:#151918;
    --panel-2:#1b211f; --line:#2b3431; --text:#e8ecea; --muted:#9aa5a0;
    --accent:#68cbb6; --accent-soft:#173a32; --link:#9ab5ff; --bad:#ff9b92;
    --code:#0c0f0e; --shadow:0 14px 38px rgba(0,0,0,.22); }}
}}
* {{ box-sizing:border-box }}
html {{ scroll-behavior:smooth }}
body {{ margin:0; background:var(--bg); color:var(--text);
  font:15px/1.65 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif }}
button,input {{ font:inherit }}
button {{ color:inherit }}
a {{ color:var(--link) }}
.site-header {{ position:sticky; top:0; z-index:20; display:grid;
  grid-template-columns:auto auto minmax(240px,720px) auto; gap:20px; align-items:center;
  min-height:64px; padding:9px 24px; background:color-mix(in srgb,var(--bg) 90%,transparent);
  border-bottom:1px solid var(--line); backdrop-filter:blur(16px) }}
.brand {{ display:flex; align-items:center; gap:10px; color:var(--text); text-decoration:none;
  font-weight:700; white-space:nowrap }}
.brand-mark {{ display:grid; width:34px; height:34px; place-items:center; color:#fff;
  background:var(--accent); border-radius:9px; font-size:12px; letter-spacing:.06em }}
.brand-copy small {{ display:block; color:var(--muted); font-size:10px; font-weight:500;
  letter-spacing:.08em; text-transform:uppercase }}
.site-nav {{ display:flex; gap:4px }}
.site-nav a {{ padding:6px 8px; color:var(--muted); border-radius:6px; text-decoration:none }}
.site-nav a:hover {{ color:var(--text); background:var(--panel-2) }}
.header-search {{ display:flex; align-items:center; min-width:0; height:40px; padding-left:12px;
  background:var(--panel); border:1px solid var(--line); border-radius:9px;
  box-shadow:0 1px 2px rgba(0,0,0,.03) }}
.global-search {{ flex:1; min-width:0; color:var(--text); background:transparent; border:0;
  outline:0 }}
.search-shortcut {{ margin:0 8px; padding:2px 6px; color:var(--muted);
  background:var(--panel-2); border:1px solid var(--line); border-radius:5px; font-size:11px }}
.search-button {{ align-self:stretch; padding:0 13px; color:var(--accent); background:transparent;
  border:0; border-left:1px solid var(--line); cursor:pointer; font-weight:650 }}
.header-actions {{ display:flex; gap:6px }}
.tool-button {{ padding:7px 10px; color:var(--muted); background:var(--panel);
  border:1px solid var(--line); border-radius:8px; cursor:pointer }}
.tool-button:hover,.tool-button[aria-pressed="true"] {{ color:var(--accent);
  background:var(--accent-soft); border-color:var(--accent) }}
.explorer-toggle {{ display:none }}
.portal-main {{ max-width:1600px; margin:auto; padding:24px 24px 80px }}
.section-heading {{ display:flex; align-items:end; justify-content:space-between; gap:20px;
  margin:8px 0 14px }}
.section-heading h1,.section-heading h2 {{ margin:0; border:0; padding:0 }}
.section-heading h1 {{ font-size:28px; letter-spacing:-.025em }}
.section-heading h2 {{ font-size:20px }}
.section-heading p {{ margin:3px 0 0; color:var(--muted) }}
.eyebrow {{ color:var(--accent); font-size:11px; font-weight:700; letter-spacing:.12em }}
.commit {{ color:var(--muted); font-size:12px }}
.commit summary {{ padding:5px 8px; border:1px solid var(--line); border-radius:7px }}
.commit code {{ display:block; margin-top:6px; padding:6px 8px; background:var(--code);
  border-radius:6px }}
.meta,.grid {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)) }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px;
  overflow:auto }}
.muted {{ color:var(--muted) }} .good {{ color:var(--accent) }} .bad {{ color:var(--bad) }}
code,pre {{ white-space:pre-wrap; overflow-wrap:anywhere;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace }}
pre {{ background:var(--code); border:1px solid var(--line); border-radius:8px; padding:14px }}
table {{ width:100%; border-collapse:collapse }} th,td {{ text-align:left; padding:8px;
  border-bottom:1px solid var(--line); vertical-align:top }}
details summary {{ cursor:pointer }}
svg {{ width:100%; min-height:180px }}
#overview {{ padding-bottom:28px; border-bottom:1px solid var(--line) }}
.overview-grid {{ display:grid; gap:10px; grid-template-columns:repeat(5,minmax(120px,1fr)) }}
.overview-card {{ background:var(--panel); border:1px solid var(--line); border-radius:9px;
  padding:13px 14px }}
.overview-card strong {{ color:var(--muted); font-size:11px; font-weight:600 }}
.overview-card span {{ display:block; margin-top:2px; color:var(--text); font-size:22px;
  font-weight:650; font-variant-numeric:tabular-nums }}
.overview-panels {{ display:grid; grid-template-columns:minmax(0,1.5fr) minmax(260px,1fr);
  gap:10px; margin-top:10px }}
.overview-panel h3 {{ margin:0 0 3px; font-size:15px }}
.directory-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:7px; margin-top:12px }}
.directory-item {{ display:flex; justify-content:space-between; gap:8px; padding:8px 10px;
  color:var(--text); background:var(--panel-2); border:1px solid transparent; border-radius:7px;
  text-align:left; cursor:pointer }}
.directory-item:hover {{ border-color:var(--accent); background:var(--accent-soft) }}
.directory-item span {{ color:var(--accent); font-variant-numeric:tabular-nums }}
.recent-list {{ margin:0; padding-left:20px }} .recent-list li {{ margin:5px 0 }}
.recent-list small {{ display:block; color:var(--muted) }}
.recent-opened {{ display:block; width:100%; margin:4px 0; padding:7px 9px; color:var(--text);
  background:var(--panel-2); border:1px solid transparent; border-radius:7px; text-align:left;
  cursor:pointer }}
.recent-opened:hover {{ border-color:var(--line) }}
.recent-opened small {{ display:block; color:var(--muted) }}
.wiki-section {{ padding-top:26px }}
.wiki-layout {{ display:grid; grid-template-columns:280px minmax(0,1fr); gap:0;
  min-height:70vh; background:var(--panel); border:1px solid var(--line); border-radius:12px;
  box-shadow:var(--shadow); overflow:clip }}
.wiki-list {{ position:sticky; top:80px; align-self:start; height:calc(100vh - 104px);
  padding:14px 10px; overflow:auto; background:var(--panel-2); border-right:1px solid var(--line) }}
.explorer-head {{ display:flex; align-items:center; justify-content:space-between;
  padding:2px 5px 10px }}
.explorer-head strong {{ font-size:13px }}
.explorer-head span {{ color:var(--muted); font-size:11px }}
.wiki-search {{ width:100%; margin-bottom:9px; padding:9px 10px; color:var(--text);
  background:var(--panel); border:1px solid var(--line); border-radius:7px; outline:0 }}
.wiki-search:focus,.global-search:focus {{ border-color:var(--accent) }}
.search-status {{ min-height:20px; padding:0 5px 5px; color:var(--muted); font-size:11px }}
.wiki-group {{ margin:5px 0; border:0 }}
.wiki-group summary {{ display:flex; justify-content:space-between; padding:7px 6px;
  color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.04em;
  text-transform:uppercase }}
.wiki-result {{ display:block; width:100%; margin:2px 0; padding:8px 9px; color:var(--text);
  background:transparent; border:0; border-radius:7px; text-align:left; cursor:pointer }}
.wiki-result:hover {{ background:color-mix(in srgb,var(--accent-soft) 55%,transparent) }}
.wiki-result[aria-selected="true"] {{ color:var(--accent); background:var(--accent-soft);
  box-shadow:inset 3px 0 var(--accent) }}
.wiki-result strong {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
.wiki-result small {{ display:block; overflow:hidden; color:var(--muted); font-size:11px;
  text-overflow:ellipsis; white-space:nowrap }}
.wiki-reader {{ min-width:0; min-height:520px }}
.wiki-page {{ display:grid; grid-template-columns:minmax(0,820px) 220px;
  justify-content:center; gap:48px; padding:38px 40px 64px }}
.wiki-page[hidden] {{ display:none }}
.wiki-content {{ min-width:0 }}
.page-kicker {{ margin-bottom:8px; color:var(--accent); font-size:11px; font-weight:700;
  letter-spacing:.08em }}
.wiki-page-title {{ margin:0; color:var(--text); font-size:34px; line-height:1.2;
  letter-spacing:-.03em }}
.page-summary {{ margin:16px 0 22px; padding:13px 15px; background:var(--accent-soft);
  border-left:3px solid var(--accent); border-radius:0 7px 7px 0 }}
.page-rail {{ position:sticky; top:88px; align-self:start; max-height:calc(100vh - 112px);
  padding-top:4px; overflow:auto }}
.page-outline {{ margin:0 0 20px; padding:0 0 16px; border-bottom:1px solid var(--line) }}
.page-outline strong,.evidence-title {{ display:block; margin-bottom:7px; color:var(--muted);
  font-size:11px; letter-spacing:.06em; text-transform:uppercase }}
.page-outline a {{ display:block; margin:5px 0; color:var(--muted); font-size:12px;
  line-height:1.4; text-decoration:none }}
.page-outline a:hover {{ color:var(--accent) }}
.page-outline .toc-level-3 {{ padding-left:10px }}
.wiki-evidence {{ display:grid; gap:6px }}
.evidence-chip {{ display:flex; flex-direction:column; gap:1px; padding:7px 8px;
  background:var(--panel-2); border:1px solid var(--line); border-radius:7px; font-size:11px }}
.evidence-chip strong {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
.evidence-chip small {{ color:var(--muted) }}
.wiki-markdown {{ color:var(--text); font-size:16px; line-height:1.8 }}
.wiki-markdown h1,.wiki-markdown h2,.wiki-markdown h3,.wiki-markdown h4,
.wiki-markdown h5,.wiki-markdown h6 {{ color:var(--text); border:0; line-height:1.35;
  margin:1.7em 0 .55em; padding:0; scroll-margin-top:82px }}
.wiki-markdown h2 {{ padding-bottom:7px; border-bottom:1px solid var(--line); font-size:23px }}
.wiki-markdown h3 {{ font-size:19px }}
.wiki-markdown p {{ margin:.8em 0 }}
.wiki-markdown ul,.wiki-markdown ol {{ padding-left:24px }}
.wiki-markdown li {{ margin:4px 0 }}
.wiki-markdown code {{ padding:2px 5px; background:var(--code); border:1px solid var(--line);
  border-radius:4px; font-size:.88em }}
.wiki-markdown pre {{ margin:16px 0; overflow:auto }}
.wiki-markdown pre code {{ padding:0; background:transparent; border:0 }}
.md-link,.citation-ref {{ color:var(--link) }}
.citation-details {{ margin-top:22px; padding:0 12px; background:var(--panel-2);
  border:1px solid var(--line); border-radius:8px }}
.citation-details pre {{ margin:0 0 12px; color:var(--muted) }}
.page-meta {{ margin-top:30px; border-top:1px solid var(--line) }}
.page-meta summary {{ padding:12px 0; color:var(--muted); font-size:12px }}
.wiki-meta {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center }}
.wiki-meta code {{ color:var(--muted) }}
.source-identity {{ margin:8px 0 }} .source-identity code {{ display:block; padding:7px 9px;
  background:var(--code); border-radius:6px }}
.audit-shell {{ margin-top:28px }}
.audit-shell>details {{ background:var(--panel); border:1px solid var(--line); border-radius:10px }}
.audit-shell>details>summary {{ display:flex; justify-content:space-between; padding:14px 16px;
  color:var(--text); font-weight:650 }}
.audit-content {{ padding:0 16px 18px }}
.secondary-section {{ padding-top:8px }}
.secondary-section h2 {{ margin:22px 0 10px; font-size:17px }}
.secondary-section>details {{ background:var(--panel-2); border:1px solid var(--line);
  border-radius:8px; padding:0 12px }}
.quick-links {{ display:grid; gap:8px }}
.quick-link {{ display:block; padding:10px 12px; color:var(--link); background:var(--panel-2);
  border:1px solid var(--line); border-radius:7px; text-decoration:none }}
body.reader-mode #overview,body.reader-mode .site-nav,body.reader-mode .wiki-list,
body.reader-mode .page-rail,body.reader-mode .audit-shell {{ display:none }}
body.reader-mode .site-header {{ grid-template-columns:auto minmax(240px,720px) auto }}
body.reader-mode .wiki-section {{ padding-top:10px }}
body.reader-mode .wiki-layout {{ display:block; background:transparent; border:0; box-shadow:none }}
body.reader-mode .wiki-page {{ display:block; max-width:820px; margin:auto; padding-top:32px }}
body.reader-mode .wiki-page[hidden] {{ display:none }}
@media (max-width:1180px) {{
  .site-header {{ grid-template-columns:auto minmax(220px,1fr) auto }}
  .site-nav {{ display:none }}
  .wiki-page {{ grid-template-columns:minmax(0,820px); padding:34px 36px }}
  .page-rail {{ display:none }}
}}
@media (max-width:780px) {{
  .site-header {{ grid-template-columns:auto minmax(0,1fr) auto; gap:9px; padding:8px 12px }}
  .brand-copy,.search-shortcut,.header-actions .reader-toggle {{ display:none }}
  .explorer-toggle {{ display:inline-flex }}
  .portal-main {{ padding:14px 12px 48px }}
  .overview-grid {{ grid-template-columns:repeat(2,1fr) }}
  .overview-panels {{ grid-template-columns:1fr }}
  .section-heading {{ align-items:start }}
  .wiki-layout {{ display:block; overflow:visible }}
  .wiki-list {{ display:none }}
  body.explorer-open {{ overflow:hidden }}
  body.explorer-open .wiki-list {{ position:fixed; z-index:30; inset:64px 0 0; display:block;
    width:100%; height:auto; max-height:none; border:0; border-radius:0 }}
  .wiki-page {{ display:block; padding:28px 20px 48px }}
  .wiki-page[hidden] {{ display:none }}
  .wiki-page-title {{ font-size:29px }}
}}
</style>
</head>
<body>
<header class="site-header">
<a class="brand" href="#overview"><span class="brand-mark">MF</span>
<span class="brand-copy">MemoryForge<small>个人知识库</small></span></a>
<nav class="site-nav" aria-label="门户导航">{_nav()}</nav>
<div class="header-search"><input id="global-search" class="global-search" type="search"
placeholder="搜索全部资料…" aria-label="搜索全部资料"><kbd class="search-shortcut">⌘K</kbd>
<button id="global-search-button" class="search-button" type="button">搜索</button></div>
<div class="header-actions">
<button id="explorer-toggle" class="tool-button explorer-toggle" type="button"
aria-expanded="false">目录</button>
<button id="reader-toggle" class="tool-button reader-toggle" type="button"
aria-pressed="false">阅读</button>
<button id="theme-toggle" class="tool-button" type="button" aria-label="切换明暗主题">主题</button>
</div>
</header>
<main class="portal-main">
<section id="overview">
<div class="section-heading"><div><span class="eyebrow">知识库概览</span>
<h1>我的技术知识库</h1><p>浏览项目、笔记、代码知识和已审核的 AI 对话。</p></div>
<details class="commit"><summary>快照 {_h(short_commit)}</summary>
<code>{_h(snapshot["workspace_commit"])}</code></details></div>
{overview_section}</section>
<section id="wiki" class="wiki-section">
<div class="section-heading"><div><span class="eyebrow">资料浏览</span>
<h2>资料库</h2><p>左侧选择资料，中间专注阅读，右侧快速定位章节和证据。</p></div></div>
{_pages_html(pages)}</section>
<section id="audit" class="audit-shell"><details><summary>
<span>审计与系统信息</span><span class="muted">来源、变更、查询和评测</span></summary>
<div class="audit-content">
<section id="sources" class="secondary-section"><h2>来源与版本</h2>
<details><summary>来源登记 · {_h(len(sources))} 个来源</summary>
{sources_section}</details></section>
<section id="changeset-diff" class="secondary-section"><h2>ChangeSet 差异</h2>
<details><summary>查看已应用的差异</summary>{_diff_html(changeset)}</details></section>
<section id="lifecycle" class="secondary-section"><h2>评审 / 审批 / 应用</h2>
<details><summary>查看生命周期记录</summary>{_lifecycle_html(changeset)}</details></section>
<section id="query-trace" class="secondary-section"><h2>查询路由与引用链路</h2>
<details open><summary>查看最近一次有依据的查询</summary>{query_section}</details></section>
<section id="benchmarks" class="secondary-section"><h2>基准指标</h2>
<details><summary>查看评测指标</summary>{_metrics_html(benchmark)}</details></section>
<section id="failures" class="secondary-section"><h2>失败与正确拒答</h2>
<details><summary>查看评测案例</summary>{_failures_html(benchmark)}</details></section>
<section id="architecture" class="secondary-section"><h2>代码 Wiki 架构</h2>
<details><summary>查看生成的架构</summary>{architecture_section}</details></section>
</div></details></section>
</main>{_portal_script()}</body></html>
"""


def _nav() -> str:
    return " ".join(
        f'<a href="#{identifier}">{_h(label)}</a>'
        for identifier, label in (
            ("overview", "概览"),
            ("wiki", "资料库"),
            ("audit", "审计"),
        )
    )


def _overview_html(snapshot: dict[str, Any]) -> str:
    pages = snapshot["wiki"]["pages"]
    group_counts: dict[str, int] = defaultdict(int)
    project_counts: dict[str, int] = defaultdict(int)
    for page in pages:
        group_counts[
            _page_group(
                str(page["title"]),
                page.get("repositories"),
                page_path=str(page["path"]),
            )
        ] += 1
        project = _page_project(page.get("repositories"))
        if project:
            project_counts[project] += 1
    ai_count = group_counts.get("AI 对话", 0)
    code_count = sum(count for group, count in group_counts.items() if group.startswith("代码 · "))
    other_count = len(pages) - ai_count - code_count
    values = (
        ("全部页面", len(pages)),
        ("AI 对话", ai_count),
        ("代码知识", code_count),
        ("其他主题", other_count),
        ("知识来源", len(snapshot["sources"])),
    )
    recent: list[tuple[str, str, str]] = []
    for page in pages:
        metadata, _ = _markdown_document(str(page["content"]))
        updated = metadata.get("updated", "")
        if updated:
            title = str(page["title"])
            recent.append(
                (
                    updated,
                    _display_title(title),
                    _page_group(
                        title,
                        page.get("repositories"),
                        page_path=str(page["path"]),
                    ),
                )
            )
    recent_items = "".join(
        f"<li><strong>{_h(title)}</strong><small>{_h(group)} · {_h(updated)}</small></li>"
        for updated, title, group in sorted(recent, reverse=True)[:6]
    )
    recent_html = f'<ol class="recent-list">{recent_items}</ol>' if recent_items else _empty()
    ordered_groups = (
        ([("代码知识", code_count, "代码 ·", "prefix")] if code_count else [])
        + ([("AI 对话", ai_count, "AI 对话", "group")] if ai_count else [])
        + [
            (f"项目 · {name}", count, name, "project")
            for name, count in sorted(project_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
    )
    if other_count:
        ordered_groups.append(("专题与文档", other_count, "", "other"))
    directory_html = "".join(
        f'<button class="directory-item" type="button" data-wiki-filter="{_h(filter_value)}" '
        f'data-wiki-filter-mode="{mode}">'
        f"<strong>{_h(group)}</strong><span>{count}</span></button>"
        for group, count, filter_value, mode in ordered_groups
    )
    return (
        '<div class="overview-grid">'
        + "".join(
            f'<div class="overview-card"><strong>{_h(label)}</strong><span>{_h(value)}</span></div>'
            for label, value in values
        )
        + '</div><div class="overview-panels">'
        + '<article class="card overview-panel"><h3>知识库目录</h3>'
        + '<p class="muted">按项目和内容类型整理。完整目录在下方页面树。</p>'
        + f'<div class="directory-grid">{directory_html}</div></article>'
        + '<article class="card overview-panel"><h3>最近打开</h3>'
        + '<div id="recent-opened-list"><p class="muted">还没有打开过页面。</p></div>'
        + "<h3>最近更新</h3>"
        + recent_html
        + "</article></div>"
    )


def _sources_html(sources: list[dict[str, Any]], privacy: dict[str, Any]) -> str:
    cards = []
    for source in sources:
        current_versions = [version for version in source["versions"] if version["current"]]
        identity_version = current_versions[-1] if current_versions else source["versions"][-1]
        versions = "".join(
            "<tr>"
            f"<td>{version['version_id']}</td><td>{_h(version['title'])}</td>"
            f"<td>{_category_label(version['category'])}</td>"
            f"<td>{_sensitivity_label(version['sensitivity'])}</td>"
            f"<td>{'是' if version['current'] else '否'}</td>"
            f"<td><code>{_h(str(version['content_sha256'])[:12])}</code></td></tr>"
            for version in source["versions"]
        )
        cards.append(
            f'<article class="card"><h3>{_h(identity_version["title"])}</h3>'
            '<details class="source-identity"><summary>来源标识 '
            f"{_h(_short_identifier(str(source['source_id'])))}</summary>"
            f"<code>{_h(source['source_id'])}</code></details>"
            "<table><thead><tr><th>版本</th><th>标题</th><th>类别</th>"
            f"<th>敏感级别</th><th>当前版本</th><th>内容哈希</th></tr></thead><tbody>{versions}"
            "</tbody></table></article>"
        )
    redacted = (
        f'<p class="muted">已隐藏的本地来源：{privacy["redacted_source_count"]} 个；'
        f"版本：{privacy['redacted_version_count']} 个。</p>"
    )
    return redacted + ('<div class="grid">' + "".join(cards) + "</div>" if cards else _empty())


def _pages_html(pages: list[dict[str, Any]]) -> str:
    if not pages:
        return _empty()
    results = []
    readers = []
    groups: dict[str, list[str]] = defaultdict(list)
    ordered_pages = sorted(pages, key=_page_sort_key, reverse=True)
    for index, page in enumerate(ordered_pages):
        selected = "true" if index == 0 else "false"
        hidden = "" if index == 0 else " hidden"
        target = f"wiki-page-{index}"
        page_group = _page_group(
            str(page["title"]),
            page.get("repositories"),
            page_path=str(page["path"]),
        )
        page_project = _page_project(page.get("repositories"))
        groups[page_group].append(target)
        display_title = _display_title(str(page["title"]))
        source_chips = []
        for item in page["evidence"]:
            source_title = _display_title(str(item.get("source_title", item["source_id"][:12])))
            source_chips.append(
                '<span class="evidence-chip">'
                f"<strong>{_h(source_title)}</strong>"
                f"<small>{_category_label(item.get('source_category', 'source'))} · "
                f"v{_h(item['source_version'])}</small></span>"
            )
        sources = "".join(source_chips)
        metadata, body = _markdown_document(str(page["content"]))
        body = _without_duplicate_title(body, str(page["title"]))
        updated = metadata.get("updated", "")
        context = f"{page_group} · {updated}" if updated else page_group
        results.append(
            f'<button class="wiki-result" type="button" data-wiki-target="{target}" '
            f'data-wiki-key="{_h(page["path"])}" '
            f'data-wiki-group="{_h(page_group)}" data-wiki-project="{_h(page_project)}" '
            f'aria-selected="{selected}"><strong>{_h(display_title)}</strong>'
            f"<small>{_h(context)}</small></button>"
        )
        summary = metadata.get("summary", "")
        summary_html = (
            f'<p class="page-summary">{_markdown_inline(_display_wiki_text(summary))}</p>'
            if summary
            else ""
        )
        updated_html = f"<span>更新时间：{_h(updated)}</span>" if updated else ""
        source_identities = "".join(
            '<details class="source-identity"><summary>来源标识 '
            f"{_h(_short_identifier(str(item['source_id'])))}</summary>"
            f"<code>{_h(item['source_id'])}</code></details>"
            for item in page["evidence"]
        )
        page_meta = (
            '<details class="page-meta"><summary>查看页面元数据</summary>'
            '<div class="wiki-meta">'
            f"<code>{_h(page['path'])}</code>"
            f'<span class="muted">内容哈希 {_h(str(page["content_sha256"])[:12])}</span>'
            f"{updated_html}</div>{source_identities}</details>"
        )
        outline = _markdown_outline(body, target)
        readers.append(
            f'<article class="wiki-page" id="{target}"{hidden}>'
            f'<div class="wiki-content"><div class="page-kicker">{_h(page_group)}</div>'
            f'<h1 class="wiki-page-title">{_h(display_title)}</h1>{summary_html}'
            f'<div class="wiki-markdown">{_markdown_html(body, heading_prefix=target)}</div>'
            f'{page_meta}</div><aside class="page-rail">{outline}'
            f'<div class="wiki-evidence"><span class="evidence-title">依据来源</span>'
            f"{sources}</div></aside></article>"
        )
    result_by_target = {f"wiki-page-{index}": result for index, result in enumerate(results)}
    grouped_results = [
        f'<details class="wiki-group" data-wiki-group{" open" if index == 0 else ""}>'
        f"<summary>{_h(group)} "
        f'<span class="muted">· {len(targets)}</span></summary>'
        + "".join(result_by_target[target] for target in targets)
        + "</details>"
        for index, (group, targets) in enumerate(sorted(groups.items()))
    ]
    return (
        '<div class="wiki-layout"><aside class="wiki-list">'
        f'<div class="explorer-head"><strong>资料目录</strong><span>{len(pages)} 页</span></div>'
        '<input id="wiki-search" class="wiki-search" type="search" '
        'placeholder="筛选当前资料库…" aria-label="筛选当前资料库">'
        '<div id="wiki-search-status" class="search-status" aria-live="polite"></div>'
        '<div id="wiki-results">' + "".join(grouped_results) + "</div>"
        '<p id="wiki-no-results" class="muted" hidden>没有匹配的知识页面。</p>'
        '</aside><div class="wiki-reader">' + "".join(readers) + "</div></div>"
    )


def _page_group(
    title: str,
    repositories: list[dict[str, Any]] | None = None,
    *,
    page_path: str = "",
) -> str:
    if page_path.startswith("wiki/pages/code/") or title.startswith(("Code:", "Code module:")):
        if title.startswith(("Code:", "Code module:")):
            path = title.split(":", 1)[1].strip()
        else:
            parts = page_path.split("/")
            path = "/".join(parts[4:]) if len(parts) > 4 else title
        root = path.split("/", 1)[0] or "root"
        repository_names = sorted(
            str(repository["name"]) for repository in repositories or [] if repository.get("name")
        )
        if repository_names:
            project = repository_names[0]
            if len(repository_names) > 1:
                project += f" +{len(repository_names) - 1}"
            return f"代码 · {project} · {root}"
        return f"代码 · {root}"
    if title.startswith(
        (
            "Codex session:",
            "AI session:",
            "Chat session:",
            "Codex 会话：",
            "Codex 会话:",
            "会话：",
            "对话：",
        )
    ):
        return "AI 对话"
    if ":" in title:
        return title.split(":", 1)[0].strip() or "其他"
    return "其他"


def _page_project(repositories: list[dict[str, Any]] | None) -> str:
    names = sorted(
        str(repository["name"]) for repository in repositories or [] if repository.get("name")
    )
    if len(names) == 1:
        return names[0]
    return "跨项目" if names else ""


def _page_sort_key(page: dict[str, Any]) -> tuple[str, str, str]:
    metadata, _ = _markdown_document(str(page["content"]))
    return (metadata.get("updated", ""), str(page["title"]), str(page["path"]))


def _short_identifier(value: str, *, head: int = 8, tail: int = 4) -> str:
    if len(value) <= head + tail + 1:
        return value
    suffix = value[-tail:] if tail else ""
    return f"{value[:head]}…{suffix}"


def _display_title(value: str) -> str:
    for prefix, label in (
        ("Code module:", "代码模块："),
        ("Code:", "代码："),
        ("Codex session:", "Codex 会话："),
        ("AI session:", "AI 会话："),
        ("Chat session:", "聊天会话："),
    ):
        if value.startswith(prefix):
            return label + value[len(prefix) :].strip()
    return value


def _display_wiki_text(value: str) -> str:
    translated_title = _display_title(value)
    if translated_title != value:
        return translated_title
    exact = {
        "Architecture": "架构关系",
        "Child modules": "子模块",
        "Code outline": "代码结构",
        "Model summary (unverified)": "模型摘要（未验证）",
        "Module": "模块信息",
        "Related pages": "相关页面",
        "Sources": "证据来源",
        "Verified dependencies": "已验证依赖",
        "Verified facts": "已验证事实",
        "Verified symbols": "已验证符号",
        "This page is generated from the deterministic module hierarchy.": (
            "本页由确定性模块层级生成。"
        ),
    }
    if value in exact:
        return exact[value]
    symbol_summary = re.fullmatch(r"(\d+) verified code symbols in module (.+)\.", value)
    if symbol_summary is not None:
        return f"模块 {symbol_summary.group(2)} 包含 {symbol_summary.group(1)} 个已验证代码符号。"
    navigation_summary = re.fullmatch(
        r"Navigation for deterministic code module (.+)\.",
        value,
    )
    if navigation_summary is not None:
        return f"确定性代码模块 {navigation_summary.group(1)} 的导航页。"
    for prefix, label in (
        ("Verified symbols:", "已验证符号数："),
        ("Languages:", "语言："),
        ("Language:", "语言："),
        ("Path:", "路径："),
        ("File:", "文件："),
        ("Python code:", "Python 代码："),
        ("Go code:", "Go 代码："),
        ("Markdown document:", "Markdown 文档："),
        ("Shell script:", "Shell 脚本："),
    ):
        if value.startswith(prefix):
            return label + value[len(prefix) :].strip()
    translated = value
    for original, label in (
        (" (calls):", "（调用）："),
        (" (imports):", "（导入）："),
        (" (contains):", "（包含）："),
        (" (inherits):", "（继承）："),
        (" (implements):", "（实现）："),
        (" (references):", "（引用）："),
        (" (package):", "（包）："),
        (" (struct):", "（结构体）："),
        (" (function):", "（函数）："),
        (" (method):", "（方法）："),
        (" (module):", "（模块）："),
        (" (class):", "（类）："),
    ):
        translated = translated.replace(original, label)
    return translated


def _without_duplicate_title(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.strip() == f"# {title}":
            del lines[index]
            while index < len(lines) and not lines[index].strip():
                del lines[index]
        break
    return "\n".join(lines)


def _markdown_document(markdown: str) -> tuple[dict[str, str], str]:
    """Drop the small YAML front matter block from the reader body."""
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---\n", 4)
    if end < 0:
        return {}, markdown
    metadata: dict[str, str] = {}
    for line in markdown[4:end].splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$", line)
        if match is None:
            continue
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        metadata[match.group(1)] = value
    return metadata, markdown[end + len("\n---\n") :]


def _markdown_outline(markdown: str, prefix: str) -> str:
    headings = _markdown_headings(markdown)
    if not headings:
        return ""
    links = "".join(
        f'<a class="toc-level-{min(level, 3)}" href="#{prefix}-heading-{index}">'
        f"{_markdown_inline(_display_wiki_text(text))}</a>"
        for index, (level, text) in enumerate(headings)
    )
    return f'<nav class="page-outline" aria-label="页面结构"><strong>本页目录</strong>{links}</nav>'


def _markdown_headings(markdown: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    in_fence = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if re.match(r"^\s*```\s*[\w.+-]*\s*$", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2)))
    return headings


def _markdown_html(markdown: str, *, heading_prefix: str = "") -> str:
    output: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    code_lines: list[str] = []
    code_language = ""
    in_fence = False
    heading_index = 0
    footnotes = [
        (match.group(1), match.group(2))
        for line in markdown.splitlines()
        if (match := re.match(r"^\[\^([^]]+)\]:\s+(.+)$", line.rstrip())) is not None
    ]
    footnote_numbers = {label: index for index, (label, _) in enumerate(footnotes, start=1)}

    def flush_paragraph() -> None:
        if paragraph:
            text = _display_wiki_text(" ".join(paragraph))
            output.append(f"<p>{_markdown_inline(text, footnote_numbers)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            output.append(f"</{list_kind}>")
            list_kind = None

    def close_fence() -> None:
        nonlocal in_fence, code_language
        code = _h("\n".join(code_lines))
        output.append(
            f'<pre class="wiki-code"><code class="language-{_h(code_language)}">{code}</code></pre>'
        )
        code_lines.clear()
        code_language = ""
        in_fence = False

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if in_fence:
            if line.strip() == "```":
                close_fence()
            else:
                code_lines.append(line)
            continue
        fence = re.match(r"^\s*```\s*([\w.+-]*)\s*$", line)
        if fence:
            flush_paragraph()
            close_list()
            in_fence = True
            code_language = fence.group(1)
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        if re.match(r"^\[\^([^]]+)\]:\s+(.+)$", line):
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            heading_id = f' id="{heading_prefix}-heading-{heading_index}"' if heading_prefix else ""
            heading_text = _display_wiki_text(heading.group(2))
            output.append(
                f"<h{level}{heading_id}>"
                f"{_markdown_inline(heading_text, footnote_numbers)}</h{level}>"
            )
            heading_index += 1
            continue
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if bullet or numbered:
            flush_paragraph()
            kind = "ul" if bullet else "ol"
            if list_kind != kind:
                close_list()
                output.append(f"<{kind}>")
                list_kind = kind
            match = bullet if bullet is not None else numbered
            assert match is not None
            item = _display_wiki_text(match.group(1))
            output.append(f"<li>{_markdown_inline(item, footnote_numbers)}</li>")
            continue
        close_list()
        paragraph.append(line.strip())
    if in_fence:
        close_fence()
    flush_paragraph()
    close_list()
    if footnotes:
        citation_text = "\n".join(
            f"{number}. "
            f"{_display_wiki_text(text.replace('source ', '来源 ').replace('revision ', '版本 '))}"
            for number, (_, text) in enumerate(footnotes, start=1)
        )
        output.append(
            f'<details class="citation-details"><summary>引用详情 · {len(footnotes)} 条</summary>'
            f"<pre>{_h(citation_text)}</pre></details>"
        )
    return "".join(output) or '<p class="muted">知识页面为空。</p>'


def _markdown_inline(
    text: str,
    footnotes: dict[str, int] | None = None,
) -> str:
    text = re.sub(r"https?://[^\s<]+", "[external link]", text)
    escaped = _h(text)
    escaped = re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r'<span class="md-link">\1</span>', escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r'<span class="md-link">\1</span>', escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    if footnotes:
        for label, number in footnotes.items():
            reference = _h(f"[^{label}]")
            escaped = escaped.replace(
                reference,
                f'<sup class="citation-ref">[{number}]</sup>',
            )
    return escaped


def _portal_script() -> str:
    return """<script>
const wikiButtons = [...document.querySelectorAll('[data-wiki-target]')];
const wikiGroups = [...document.querySelectorAll('.wiki-group[data-wiki-group]')];
const wikiPages = new Map(wikiButtons.map(button => [
  button.dataset.wikiTarget, document.getElementById(button.dataset.wikiTarget)
]));
const wikiSearch = document.getElementById('wiki-search');
const globalSearch = document.getElementById('global-search');
const recentOpened = document.getElementById('recent-opened-list');
const searchStatus = document.getElementById('wiki-search-status');
const themeButton = document.getElementById('theme-toggle');
const readerButton = document.getElementById('reader-toggle');
const explorerButton = document.getElementById('explorer-toggle');
const recentKey = 'memoryforge-recent-wiki-v1';
const themeKey = 'memoryforge-theme';
const readerKey = 'memoryforge-reader-mode';
function selectWiki(button, { updateHash = true, remember = true } = {}) {
  wikiButtons.forEach(item => item.setAttribute('aria-selected', String(item === button)));
  wikiPages.forEach((page, id) => { page.hidden = id !== button.dataset.wikiTarget; });
  if (remember) recordRecent(button.dataset.wikiKey);
  if (updateHash) {
    history.replaceState(null, '', `#page=${encodeURIComponent(button.dataset.wikiKey)}`);
  }
  document.body.classList.remove('explorer-open');
  explorerButton?.setAttribute('aria-expanded', 'false');
}
wikiButtons.forEach(button => button.addEventListener('click', () => selectWiki(button)));
function applyWikiSearch(value, mode = 'text') {
  const query = value.trim().toLocaleLowerCase();
  wikiButtons.forEach(button => {
    const page = wikiPages.get(button.dataset.wikiTarget);
    const searchable = `${button.textContent}\n${page.textContent}`.toLocaleLowerCase();
    let matches = searchable.includes(query);
    if (mode === 'project') matches = button.dataset.wikiProject === value;
    if (mode === 'group') matches = button.dataset.wikiGroup === value;
    if (mode === 'prefix') matches = button.dataset.wikiGroup.startsWith(value);
    if (mode === 'other') {
      matches = button.dataset.wikiGroup !== 'AI 对话' &&
        !button.dataset.wikiGroup.startsWith('代码 ·');
    }
    button.hidden = !matches;
  });
  wikiGroups.forEach(group => {
    const groupButtons = [...group.querySelectorAll('[data-wiki-target]')];
    group.hidden = !groupButtons.some(button => !button.hidden);
  });
  const visible = wikiButtons.filter(button => !button.hidden);
  document.getElementById('wiki-no-results').hidden = visible.length !== 0;
  if (searchStatus) searchStatus.textContent = query || mode !== 'text'
    ? `${visible.length} 个匹配页面` : '';
  if (visible.length) {
    const selected = visible.find(button => button.getAttribute('aria-selected') === 'true');
    const next = selected || visible[0];
    selectWiki(next);
    const group = next.closest('.wiki-group');
    if (group) group.open = true;
  }
  if (!visible.length) wikiPages.forEach(page => { page.hidden = true; });
}
wikiSearch?.addEventListener('input', event => {
  if (globalSearch) globalSearch.value = event.target.value;
  applyWikiSearch(event.target.value);
});
function jumpToSearch() {
  if (!wikiSearch || !globalSearch) return;
  wikiSearch.value = globalSearch.value;
  applyWikiSearch(globalSearch.value);
  document.getElementById('wiki')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  wikiSearch.focus({ preventScroll: true });
}
globalSearch?.addEventListener('input', event => applyWikiSearch(event.target.value));
globalSearch?.addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); jumpToSearch(); }
});
document.getElementById('global-search-button')?.addEventListener('click', jumpToSearch);
document.querySelectorAll('[data-wiki-filter]').forEach(item => {
  item.addEventListener('click', () => {
    const value = item.dataset.wikiFilter || '';
    const mode = item.dataset.wikiFilterMode || 'text';
    if (wikiSearch) wikiSearch.value = value;
    if (globalSearch) globalSearch.value = value;
    applyWikiSearch(value, mode);
    document.getElementById('wiki')?.scrollIntoView({ behavior: 'smooth' });
  });
});
document.addEventListener('keydown', event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault(); globalSearch?.focus();
  }
  if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
    event.preventDefault(); globalSearch?.focus();
  }
  if (event.key === 'Escape') {
    document.body.classList.remove('explorer-open');
    explorerButton?.setAttribute('aria-expanded', 'false');
  }
});
function currentTheme() {
  if (document.documentElement.dataset.theme) return document.documentElement.dataset.theme;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
themeButton?.addEventListener('click', () => {
  const theme = currentTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(themeKey, theme); } catch (_) {}
});
function setReaderMode(active) {
  document.body.classList.toggle('reader-mode', active);
  readerButton?.setAttribute('aria-pressed', String(active));
}
readerButton?.addEventListener('click', () => {
  const active = !document.body.classList.contains('reader-mode');
  setReaderMode(active);
  try { localStorage.setItem(readerKey, String(active)); } catch (_) {}
});
explorerButton?.addEventListener('click', () => {
  const active = document.body.classList.toggle('explorer-open');
  explorerButton.setAttribute('aria-expanded', String(active));
});
function readRecent() {
  try {
    const parsed = JSON.parse(localStorage.getItem(recentKey) || '[]');
    return Array.isArray(parsed)
      ? parsed.filter(key => wikiButtons.some(button => button.dataset.wikiKey === key))
      : [];
  } catch (_) { return []; }
}
function recordRecent(id) {
  try {
    const ids = [id, ...readRecent().filter(item => item !== id)].slice(0, 8);
    localStorage.setItem(recentKey, JSON.stringify(ids));
    renderRecent(ids);
  } catch (_) {}
}
function renderRecent(ids = readRecent()) {
  if (!recentOpened) return;
  recentOpened.replaceChildren();
  if (!ids.length) {
    const empty = document.createElement('p');
    empty.className = 'muted'; empty.textContent = '还没有打开过页面。';
    recentOpened.appendChild(empty); return;
  }
  ids.forEach(id => {
    const source = wikiButtons.find(button => button.dataset.wikiKey === id);
    if (!source) return;
    const recent = document.createElement('button');
    recent.className = 'recent-opened'; recent.type = 'button';
    const title = document.createElement('strong');
    title.textContent = source.querySelector('strong')?.textContent || '知识页面';
    const path = document.createElement('small');
    path.textContent = source.querySelector('small')?.textContent || '';
    recent.append(title, path);
    recent.addEventListener('click', () => {
      selectWiki(source);
      document.getElementById('wiki')?.scrollIntoView({ behavior: 'smooth' });
    });
    recentOpened.appendChild(recent);
  });
}
try { setReaderMode(localStorage.getItem(readerKey) === 'true'); } catch (_) {}
renderRecent();
const initialPath = location.hash.startsWith('#page=')
  ? decodeURIComponent(location.hash.slice(6)) : '';
const initialButton = wikiButtons.find(button => button.dataset.wikiKey === initialPath);
if (initialButton) {
  selectWiki(initialButton, { updateHash: false, remember: false });
  initialButton.closest('.wiki-group')?.setAttribute('open', '');
} else if (wikiButtons[0]) {
  selectWiki(wikiButtons[0], { updateHash: false, remember: false });
}
</script>"""


def _diff_html(changeset: dict[str, Any] | None) -> str:
    if changeset is None:
        return _empty()
    return "".join(
        f"<h3>{_h(item['path'])}</h3><pre>{_h(item['diff'])}</pre>"
        for item in changeset["unified_diff"]
    )


def _lifecycle_html(changeset: dict[str, Any] | None) -> str:
    if changeset is None:
        return _empty()
    lifecycle = changeset["lifecycle"]
    states = []
    for name, state in lifecycle.items():
        state_class = "good" if state else "bad"
        state_label = "已记录" if state else "缺失"
        states.append(
            f'<div class="card"><strong>{_lifecycle_label(name)}</strong><br>'
            f'<span class="{state_class}">{state_label}</span></div>'
        )
    return (
        '<details class="source-identity"><summary>变更集标识 '
        f"{_h(_short_identifier(str(changeset['changeset_id'])))}</summary>"
        f"<code>{_h(changeset['changeset_id'])}</code></details>"
        '<div class="grid">' + "".join(states) + "</div>"
    )


def _query_html(query: dict[str, Any] | None, rejection: dict[str, Any]) -> str:
    answered = _empty()
    if query is not None:
        trace = "".join(
            f"<li><code>{_h(step['level'])}</code> {_h(step['artifact'])}</li>"
            for step in query["trace"]
        )
        citations = "".join(
            f"<li><code>{_h(citation.get('source_id', '')[:12])} "
            f"v{_h(citation.get('source_version', ''))}</code> "
            f"{_h(citation.get('quote', ''))}</li>"
            for citation in query["citations"]
        )
        answered = (
            f"<h3>{_h(query['question'])}</h3><p>{_h(query['answer'])}</p>"
            f"<h3>路由链路</h3><ol>{trace}</ol><h3>引用来源</h3><ul>{citations}</ul>"
        )
    return (
        answered
        + "<h3>确定性拒答</h3>"
        + f"<p><code>{_h(rejection['question'])}</code>: "
        + f"<strong>{_status_label(rejection['status'])}</strong></p>"
    )


def _metrics_html(benchmark: dict[str, Any]) -> str:
    metrics = benchmark["metrics"]
    if not metrics:
        return _empty()
    rows = "".join(
        f"<tr><td>{_metric_label(key)}</td><td>{_h(value)}</td></tr>"
        for key, value in metrics.items()
    )
    return (
        f"<p>{_h(benchmark['suite'])}；案例数：{benchmark['case_count']}</p>"
        f"<table><tbody>{rows}</tbody></table>"
    )


def _failures_html(benchmark: dict[str, Any]) -> str:
    def cards(cases: list[dict[str, Any]], css: str) -> str:
        return "".join(
            f'<article class="card {css}"><strong>{_h(case.get("id", ""))}</strong><br>'
            f"{_h(case.get('category', ''))}<br>{_h(case.get('error_classification', ''))}"
            "</article>"
            for case in cases
        )

    failures = cards(benchmark["failures"], "bad")
    abstentions = cards(benchmark["abstentions"], "good")
    if not failures and not abstentions:
        return _empty()
    return (
        '<h3>失败案例</h3><div class="grid">'
        + (failures or _empty())
        + '</div><h3>正确拒答</h3><div class="grid">'
        + (abstentions or _empty())
        + "</div>"
    )


def _architecture_html(architecture: dict[str, Any]) -> str:
    if not architecture["mermaid"]:
        return _empty()
    return (
        _architecture_svg(architecture["nodes"], architecture["edges"])
        + f'<p class="muted"><code>{_h(architecture["page_path"])}</code></p>'
        + f"<pre>{_h(architecture['mermaid'])}</pre>"
    )


def _architecture_svg(nodes: list[dict[str, str]], edges: list[dict[str, str]]) -> str:
    if not nodes:
        return ""
    positions = {
        node["id"]: (40 + (index % 2) * 430, 35 + (index // 2) * 100)
        for index, node in enumerate(nodes)
    }
    height = 90 + ((len(nodes) + 1) // 2) * 100
    edge_svg = []
    for edge in edges:
        if edge["source"] not in positions or edge["target"] not in positions:
            continue
        sx, sy = positions[edge["source"]]
        tx, ty = positions[edge["target"]]
        edge_svg.append(
            f'<line x1="{sx + 160}" y1="{sy + 25}" x2="{tx}" y2="{ty + 25}" '
            'stroke="#71d1b3" stroke-width="2" marker-end="url(#arrow)"/>'
            f'<text x="{(sx + tx + 160) // 2}" y="{(sy + ty) // 2 + 14}" '
            f'fill="#9eabc5" font-size="12">{_h(edge["label"])}</text>'
        )
    node_svg = "".join(
        f'<rect x="{positions[node["id"]][0]}" y="{positions[node["id"]][1]}" '
        'width="160" height="50" rx="8" fill="#151d32" stroke="#2b3858"/>'
        f'<text x="{positions[node["id"]][0] + 10}" y="{positions[node["id"]][1] + 30}" '
        f'fill="#e8edf8" font-size="13">{_h(node["label"][:20])}</text>'
        for node in nodes
    )
    return (
        f'<svg viewBox="0 0 900 {height}" role="img" aria-label="代码架构">'
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="7" refY="3" '
        'orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#71d1b3"/></marker></defs>'
        + "".join(edge_svg)
        + node_svg
        + "</svg>"
    )


def _read_evidence(path: Path) -> dict[str, Any]:
    payload = _read_regular_file(
        path.expanduser().absolute(),
        max_bytes=_MAX_EVIDENCE_BYTES,
    )
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShowcaseBuildError("public evidence must be valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict) or parsed.get("schema_version") != 1:
        raise ShowcaseBuildError("public evidence schema is invalid")
    return cast(dict[str, Any], parsed)


def _query_rows(workspace: Workspace, statement: str) -> list[sqlite3.Row]:
    uri = f"{workspace.index_path.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(statement).fetchall()
    except sqlite3.Error as exc:
        raise ShowcaseBuildError("Workspace Showcase metadata could not be read") from exc


def _read_hashed_file(directory: Path, name: str, digest_name: str) -> bytes:
    payload = _read_regular_file(directory / name, max_bytes=_MAX_STAGED_BYTES)
    digest = _read_regular_file(directory / digest_name, max_bytes=128).decode("ascii")
    if digest != hashlib.sha256(payload).hexdigest() + "\n":
        raise ShowcaseBuildError("Applied ChangeSet integrity check failed")
    return payload


def _read_optional_hashed_file(
    directory: Path,
    name: str,
    digest_name: str,
) -> bytes | None:
    path = directory / name
    digest_path = directory / digest_name
    if not path.exists() and not digest_path.exists():
        return None
    if not path.exists() or not digest_path.exists():
        raise ShowcaseBuildError("Applied ChangeSet lifecycle receipt is incomplete")
    return _read_hashed_file(directory, name, digest_name)


def _validate_review(
    payload: bytes | None,
    record: StagedChangeSet,
    proposal_sha256: str,
) -> ReviewReceipt | None:
    if payload is None:
        return None
    try:
        review = ReviewReceipt.model_validate_json(payload)
    except ValidationError as exc:
        raise ShowcaseBuildError("Applied ChangeSet review receipt is invalid") from exc
    if (
        review.changeset_id != record.changeset.changeset_id
        or review.proposal_sha256 != proposal_sha256
    ):
        raise ShowcaseBuildError("Applied ChangeSet review binding is invalid")
    return review


def _validate_approval(
    payload: bytes | None,
    review_payload: bytes | None,
    record: StagedChangeSet,
    proposal_sha256: str,
) -> ApprovalReceipt | None:
    if payload is None:
        return None
    if review_payload is None:
        raise ShowcaseBuildError("Applied ChangeSet approval lacks a review")
    try:
        approval = ApprovalReceipt.model_validate_json(payload)
    except ValidationError as exc:
        raise ShowcaseBuildError("Applied ChangeSet approval receipt is invalid") from exc
    if (
        approval.changeset_id != record.changeset.changeset_id
        or approval.proposal_sha256 != proposal_sha256
        or approval.review_sha256 != hashlib.sha256(review_payload).hexdigest()
    ):
        raise ShowcaseBuildError("Applied ChangeSet approval binding is invalid")
    return approval


def _read_json_file(path: Path) -> dict[str, Any]:
    return _parse_json(_read_regular_file(path, max_bytes=_MAX_STAGED_BYTES))


def _parse_json(payload: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShowcaseBuildError("Showcase source metadata is invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ShowcaseBuildError("Showcase source metadata must be an object")
    return cast(dict[str, Any], parsed)


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    try:
        _require_no_symlink_components(path)
        if path.is_symlink():
            raise ShowcaseBuildError("Showcase input must not be a symbolic link")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, ShowcaseBuildError):
            raise
        raise ShowcaseBuildError("Showcase input could not be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise ShowcaseBuildError("Showcase input must be a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ShowcaseBuildError("Showcase input exceeds its size limit")
        return payload
    finally:
        os.close(descriptor)


def _validate_output(workspace: Path, output: Path) -> Path:
    destination = output.expanduser().absolute()
    if destination.is_symlink():
        raise ShowcaseBuildError("Showcase output must not be a symbolic link")
    try:
        destination.resolve(strict=False).relative_to(workspace.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ShowcaseBuildError("Showcase output must stay outside the Workspace")
    _require_no_symlink_components(destination.parent)
    if destination.exists():
        if not destination.is_dir():
            raise ShowcaseBuildError("Showcase output must be a directory")
        entries = {entry.name for entry in destination.iterdir()}
        if entries:
            marker = destination / _MARKER_NAME
            if (
                entries != _OUTPUT_FILES
                or marker.is_symlink()
                or not marker.is_file()
                or _read_regular_file(marker, max_bytes=128).decode("ascii") != _MARKER
            ):
                raise ShowcaseBuildError("Showcase output is not owned by MemoryForge")
    return destination


def _publish(destination: Path, *, payload: bytes, page: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _require_no_symlink_components(destination.parent)
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700)
    try:
        _write_new_file(temporary / _MARKER_NAME, _MARKER.encode("ascii"))
        _write_new_file(temporary / "showcase.json", payload)
        _write_new_file(temporary / "index.html", page)
        if destination.exists():
            _publish_into_existing(destination, temporary)
        else:
            os.replace(temporary, destination)
    except OSError as exc:
        raise ShowcaseBuildError("Showcase output could not be published safely") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _publish_into_existing(destination: Path, temporary: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    destination_fd = os.open(destination, flags)
    temporary_fd = os.open(temporary, flags)
    try:
        identity = os.fstat(destination_fd)
        entries = set(os.listdir(destination_fd))
        names: tuple[str, ...]
        if entries:
            if entries != _OUTPUT_FILES:
                raise ShowcaseBuildError("Showcase output is not owned by MemoryForge")
            marker = _read_regular_file_at(destination_fd, _MARKER_NAME, max_bytes=128)
            if marker.decode("ascii") != _MARKER:
                raise ShowcaseBuildError("Showcase output is not owned by MemoryForge")
            names = ("showcase.json", "index.html")
        else:
            names = ("showcase.json", "index.html", _MARKER_NAME)
        for name in names:
            os.replace(
                name,
                name,
                src_dir_fd=temporary_fd,
                dst_dir_fd=destination_fd,
            )
        os.fsync(destination_fd)
        current = os.stat(destination, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise ShowcaseBuildError("Showcase output changed during publication")
        if set(os.listdir(destination_fd)) != _OUTPUT_FILES:
            raise ShowcaseBuildError("Showcase output changed during publication")
    finally:
        os.close(temporary_fd)
        os.close(destination_fd)


def _read_regular_file_at(directory_fd: int, name: str, *, max_bytes: int) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise ShowcaseBuildError("Showcase output marker is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read(max_bytes + 1)
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_no_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ShowcaseBuildError("Showcase path contains a symbolic link")


def _require_real_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ShowcaseBuildError("Showcase history directory is unsafe")


def _allowlist(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source}


def _category_label(value: object) -> str:
    return _ui_label(
        value,
        {
            "refs": "引用",
            "code": "代码",
            "conversation": "对话",
            "chat": "对话",
            "folder": "文件夹",
            "document": "文档",
            "concept": "概念",
            "source": "来源",
        },
    )


def _sensitivity_label(value: object) -> str:
    return _ui_label(value, {"public": "公开", "private": "私有", "local_only": "仅本地"})


def _lifecycle_label(value: object) -> str:
    return _ui_label(
        value,
        {"proposed": "已提议", "reviewed": "已评审", "approved": "已审批", "applied": "已应用"},
    )


def _metric_label(value: object) -> str:
    return _ui_label(
        value,
        {
            "answer_accuracy": "回答正确率",
            "citation_grounding_accuracy": "引用依据正确率",
            "abstention_accuracy": "拒答正确率",
            "citation_coverage": "引用覆盖率",
        },
    )


def _status_label(value: object) -> str:
    return _ui_label(value, {"unknown": "未知", "answered": "已回答", "rejected": "已拒答"})


def _ui_label(value: object, mapping: dict[str, str]) -> str:
    return mapping.get(str(value), str(value))


def _h(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _empty() -> str:
    return '<p class="muted">暂无公开证据。</p>'
