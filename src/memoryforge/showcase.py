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
            facts.source_id,
            facts.source_version,
            versions.sensitivity
        FROM wiki_facts AS facts
        JOIN source_versions AS versions
          ON versions.id = facts.source_version
        ORDER BY facts.page_path, facts.source_id, facts.source_version
        """,
    )
    owners: dict[str, set[str]] = defaultdict(set)
    private_pages: set[str] = set()
    for row in rows:
        page_path = str(row["page_path"])
        owners[page_path].add(str(row["source_id"]))
        if str(row["sensitivity"]) != "public":
            private_pages.add(page_path)
    pages = []
    for path, source_ids in sorted(owners.items()):
        if not include_local and (not source_ids or path in private_pages):
            continue
        content = workspace.version_store.read_text_at(workspace_commit, path)
        if content is None:
            raise ShowcaseBuildError("Showcase Wiki page is missing from its fixed Commit")
        match = _HEADING.search(content)
        pages.append(
            {
                "path": path,
                "title": match.group(1) if match else Path(path).stem,
                "source_ids": sorted(source_ids),
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
        if not include_local and (
            not source_versions or not source_versions <= public_source_versions
        ):
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
    for page in pages:
        path = str(page["path"])
        content = workspace.version_store.read_text_at(workspace_commit, path)
        if content is None:
            raise ShowcaseBuildError("Code Wiki architecture page is missing from its Commit")
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
    sources_section = _sources_html(sources, snapshot["privacy"])
    query_section = _query_html(query, snapshot["rejection"])
    architecture_section = _architecture_html(architecture)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MemoryForge Showcase</title>
<style>
:root {{ color-scheme: dark; --bg:#0b1020; --panel:#151d32; --line:#2b3858;
  --text:#e8edf8; --muted:#9eabc5; --accent:#71d1b3; --bad:#ff8f8f; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--text);
  font:15px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace }}
main {{ max-width:1120px; margin:auto; padding:32px 20px 80px }}
h1 {{ font-size:34px }} h2 {{ margin-top:42px; border-bottom:1px solid var(--line);
  padding-bottom:10px }} h3 {{ color:var(--accent) }}
.meta,.grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)) }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px;
  overflow:auto }} .muted {{ color:var(--muted) }} .good {{ color:var(--accent) }}
.bad {{ color:var(--bad) }} code,pre {{ white-space:pre-wrap; overflow-wrap:anywhere }}
pre {{ background:#080d19; border:1px solid var(--line); border-radius:8px; padding:14px }}
table {{ width:100%; border-collapse:collapse }} th,td {{ text-align:left; padding:8px;
  border-bottom:1px solid var(--line); vertical-align:top }}
nav a {{ color:var(--accent); margin-right:14px }} svg {{ width:100%; min-height:180px }}
</style>
</head>
<body><main>
<h1>MemoryForge Showcase</h1>
<p class="muted">Read-only evidence snapshot at Commit
<code>{_h(snapshot["workspace_commit"])}</code>.</p>
<nav>{_nav()}</nav>
<section id="sources"><h2>Sources and versions</h2>{sources_section}</section>
<section id="wiki"><h2>Wiki page tree</h2>{_pages_html(pages)}</section>
<section id="changeset-diff"><h2>ChangeSet diff</h2>{_diff_html(changeset)}</section>
<section id="lifecycle"><h2>Review / approve / apply</h2>{_lifecycle_html(changeset)}</section>
<section id="query-trace"><h2>Query routing and Citation trace</h2>{query_section}</section>
<section id="benchmarks"><h2>Benchmark metrics</h2>{_metrics_html(benchmark)}</section>
<section id="failures"><h2>Failures and abstentions</h2>{_failures_html(benchmark)}</section>
<section id="architecture"><h2>Code Wiki Mermaid architecture</h2>{architecture_section}</section>
</main></body></html>
"""


def _nav() -> str:
    return " ".join(
        f'<a href="#{identifier}">{_h(label)}</a>'
        for identifier, label in (
            ("sources", "Sources"),
            ("wiki", "Wiki"),
            ("changeset-diff", "Diff"),
            ("lifecycle", "Lifecycle"),
            ("query-trace", "Query"),
            ("benchmarks", "Metrics"),
            ("failures", "Failures"),
            ("architecture", "Architecture"),
        )
    )


def _sources_html(sources: list[dict[str, Any]], privacy: dict[str, Any]) -> str:
    cards = []
    for source in sources:
        versions = "".join(
            "<tr>"
            f"<td>{version['version_id']}</td><td>{_h(version['title'])}</td>"
            f"<td>{_h(version['category'])}</td><td>{_h(version['sensitivity'])}</td>"
            f"<td>{'yes' if version['current'] else 'no'}</td>"
            f"<td><code>{_h(str(version['content_sha256'])[:12])}</code></td></tr>"
            for version in source["versions"]
        )
        cards.append(
            '<article class="card"><h3>Source '
            f"<code>{_h(str(source['source_id'])[:12])}</code></h3>"
            "<table><thead><tr><th>Version</th><th>Title</th><th>Category</th>"
            f"<th>Sensitivity</th><th>Current</th><th>SHA</th></tr></thead><tbody>{versions}"
            "</tbody></table></article>"
        )
    redacted = (
        f'<p class="muted">Redacted local-only sources: {privacy["redacted_source_count"]}; '
        f"versions: {privacy['redacted_version_count']}.</p>"
    )
    return redacted + ('<div class="grid">' + "".join(cards) + "</div>" if cards else _empty())


def _pages_html(pages: list[dict[str, Any]]) -> str:
    if not pages:
        return _empty()
    return (
        "<ul>"
        + "".join(
            f"<li><code>{_h(page['path'])}</code> - {_h(page['title'])}</li>" for page in pages
        )
        + "</ul>"
    )


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
        state_label = "recorded" if state else "missing"
        states.append(
            f'<div class="card"><strong>{_h(name)}</strong><br>'
            f'<span class="{state_class}">{state_label}</span></div>'
        )
    return (
        f'<p><code>{_h(changeset["changeset_id"])}</code></p><div class="grid">'
        + "".join(states)
        + "</div>"
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
            f"<ol>{trace}</ol><h3>Citations</h3><ul>{citations}</ul>"
        )
    return (
        answered
        + "<h3>Deterministic refusal</h3>"
        + f"<p><code>{_h(rejection['question'])}</code>: "
        + f"<strong>{_h(rejection['status'])}</strong></p>"
    )


def _metrics_html(benchmark: dict[str, Any]) -> str:
    metrics = benchmark["metrics"]
    if not metrics:
        return _empty()
    rows = "".join(
        f"<tr><td>{_h(key)}</td><td>{_h(value)}</td></tr>" for key, value in metrics.items()
    )
    return (
        f"<p>{_h(benchmark['suite'])}; cases: {benchmark['case_count']}</p>"
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
        '<h3>Failures</h3><div class="grid">'
        + (failures or _empty())
        + '</div><h3>Correct abstentions</h3><div class="grid">'
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
        f'<svg viewBox="0 0 900 {height}" role="img" aria-label="Code architecture">'
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


def _h(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _empty() -> str:
    return '<p class="muted">No public evidence available.</p>'
