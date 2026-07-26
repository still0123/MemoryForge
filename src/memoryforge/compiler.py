"""Deterministic local compilation from current source versions to Wiki proposals."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass

from memoryforge.changesets import StoredChangeSet
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    Citation,
    Claim,
    ClaimStatus,
)
from memoryforge.workspace import Workspace

_SOURCE_MARKER = "<!-- memoryforge:source_id={source_id} -->"
_CONTENT_MARKER = "<!-- memoryforge:content_sha256={content_sha256} -->"


@dataclass(frozen=True)
class CurrentSource:
    source_id: str
    title: str
    content_sha256: str
    snapshot_uri: str
    snapshot_path: str
    content: str


@dataclass(frozen=True)
class Compilation:
    changeset: ChangeSet
    candidate_files: dict[str, str]


def compile_pending_sources(
    workspace: Workspace,
    *,
    source_ids: tuple[str, ...] = (),
) -> Compilation | None:
    """Compile current sources whose content hash is not present in their stable page."""
    selected = set(source_ids)
    current_sources = _load_current_sources(workspace)
    known_source_ids = {source.source_id for source in current_sources}
    unknown_source_ids = selected - known_source_ids
    if unknown_source_ids:
        listed = ", ".join(sorted(unknown_source_ids))
        raise ValueError(f"unknown source id: {listed}")
    sources = [source for source in current_sources if not selected or source.source_id in selected]
    pending = [source for source in sources if not _stable_page_is_current(workspace, source)]
    if not pending:
        return None

    operations: list[ChangeOperation] = []
    claims: list[Claim] = []
    candidate_files: dict[str, str] = {}
    for source in pending:
        path = _wiki_path(source)
        claim = _compile_claim(source)
        stable_path = workspace.root / path
        operation_type = (
            ChangeOperationType.UPDATE_PAGE
            if stable_path.is_file()
            else ChangeOperationType.CREATE_PAGE
        )
        operations.append(ChangeOperation(type=operation_type, path=path))
        claims.append(claim)
        candidate_files[path] = _render_page(source, claim)

    base_commit = workspace.current_commit()
    identity = "\n".join(
        [
            base_commit,
            *(
                f"{source.source_id}:{source.content_sha256}:{_wiki_path(source)}"
                for source in pending
            ),
        ]
    )
    changeset_id = "chg_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    return Compilation(
        changeset=ChangeSet(
            changeset_id=changeset_id,
            base_commit=base_commit,
            source_ids=tuple(source.source_id for source in pending),
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
            claims=tuple(claims),
        ),
        candidate_files=candidate_files,
    )


def compilation_payload(stored: StoredChangeSet) -> dict[str, object]:
    return {
        "changeset_id": stored.changeset.changeset_id,
        "status": stored.changeset.status.value,
        "files": sorted(stored.candidate_files),
        "claims": [claim.model_dump(mode="json") for claim in stored.changeset.claims],
    }


def _load_current_sources(workspace: Workspace) -> list[CurrentSource]:
    connection = sqlite3.connect(workspace.index_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                s.source_id,
                v.title,
                b.content_sha256,
                b.snapshot_path
            FROM source_versions AS v
            JOIN sources AS s ON s.id = v.source_id
            JOIN blobs AS b ON b.id = v.blob_id
            WHERE v.is_current = 1
            ORDER BY s.source_id
            """
        ).fetchall()
    finally:
        connection.close()

    sources: list[CurrentSource] = []
    for row in rows:
        snapshot_path = str(row["snapshot_path"])
        content = (workspace.root / snapshot_path).read_text(encoding="utf-8")
        content_sha256 = str(row["content_sha256"])
        sources.append(
            CurrentSource(
                source_id=str(row["source_id"]),
                title=str(row["title"]),
                content_sha256=content_sha256,
                snapshot_uri=f"mf://blob/{content_sha256}",
                snapshot_path=snapshot_path,
                content=content,
            )
        )
    return sources


def _compile_claim(source: CurrentSource) -> Claim:
    quote, start = _first_meaningful_paragraph(source.content)
    digest = hashlib.sha256(f"{source.source_id}:{quote}".encode()).hexdigest()
    citation = Citation(
        source_id=source.source_id,
        content_sha256=source.content_sha256,
        snapshot_uri=source.snapshot_uri,
        quote=quote,
        quote_sha256=hashlib.sha256(quote.encode()).hexdigest(),
        locator=f"chars:{start}-{start + len(quote)}",
    )
    return Claim(
        claim_id=f"clm_{digest[:20]}",
        subject=source.title,
        predicate="states",
        object=quote,
        status=ClaimStatus.VERIFIED,
        confidence=1.0,
        citations=(citation,),
    )


def _first_meaningful_paragraph(content: str) -> tuple[str, int]:
    for match in re.finditer(
        r"(?:\A|\n[ \t]*\n)(?P<paragraph>.*?)(?=\n[ \t]*\n|\Z)",
        content,
        re.DOTALL,
    ):
        paragraph = match.group("paragraph")
        leading = len(paragraph) - len(paragraph.lstrip())
        quote = paragraph.strip()
        if quote and not quote.startswith("#"):
            return quote, match.start("paragraph") + leading
    for line in content.splitlines():
        candidate = line.lstrip("#").strip()
        if candidate:
            return candidate, content.index(candidate)
    raise ValueError("source contains no meaningful text")


def _wiki_path(source: CurrentSource) -> str:
    return f"wiki/sources/{source.source_id}.md"


def _stable_page_is_current(workspace: Workspace, source: CurrentSource) -> bool:
    page = workspace.root / _wiki_path(source)
    if not page.is_file():
        return False
    content = page.read_text(encoding="utf-8")
    return (
        _SOURCE_MARKER.format(source_id=source.source_id) in content
        and _CONTENT_MARKER.format(content_sha256=source.content_sha256) in content
    )


def _render_page(source: CurrentSource, claim: Claim) -> str:
    citation = claim.citations[0]
    footnote = f"source-{source.source_id[:8]}"
    displayed_claim = " ".join(claim.object.splitlines())
    return (
        f"# {source.title}\n\n"
        f"{_SOURCE_MARKER.format(source_id=source.source_id)}\n"
        f"{_CONTENT_MARKER.format(content_sha256=source.content_sha256)}\n\n"
        "## Verified facts\n\n"
        f"- {displayed_claim} [^{footnote}]\n\n"
        f"[^{footnote}]: `{citation.snapshot_uri}` · `{citation.locator}` · "
        f"source `{citation.source_id}`\n"
    )
