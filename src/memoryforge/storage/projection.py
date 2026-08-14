"""Stable Wiki projection metadata and validation helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

from memoryforge.compiler.wiki_facts import IndexedWikiFact, WikiFact
from memoryforge.storage.errors import WorkspaceIntegrityError
from memoryforge.storage.identifiers import CHAR_LOCATOR, CONTENT_SHA256


def candidate_page_sources(candidate_files: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Read locally generated `sources` frontmatter for candidate stable pages."""
    page_sources: dict[str, tuple[str, ...]] = {}
    for path, content in candidate_files.items():
        if is_stable_wiki_page_path(path):
            if is_generated_navigation_page(content):
                continue
            source_ids = page_source_ids_from_frontmatter(content)
            if not source_ids:
                raise WorkspaceIntegrityError("candidate Wiki page has invalid sources metadata")
            page_sources[path] = source_ids
    return page_sources


def is_generated_repository_overview(content: str) -> bool:
    """Recognize local navigation pages that deliberately have no source ownership."""
    return _generated_navigation_kind(content) == "repository_overview"


def is_generated_navigation_page(content: str) -> bool:
    """Recognize locally derived navigation pages that own no source evidence."""
    return _generated_navigation_kind(content) is not None


def _generated_navigation_kind(content: str) -> str | None:
    if not content.startswith("---\n"):
        return None
    closing = content.find("\n---\n", len("---\n"))
    if closing < 0:
        return None
    fields = {
        key.strip(): value.strip()
        for line in content[len("---\n") : closing].splitlines()
        for key, separator, value in (line.partition(":"),)
        if separator
    }
    generated = fields.get("generated")
    common = (
        fields.get("type") == "entity"
        and bool(fields.get("title"))
        and bool(fields.get("summary"))
        and CONTENT_SHA256.fullmatch(fields.get("repository_id", "")) is not None
        and "sources" not in fields
    )
    if not common:
        return None
    if generated == "repository_overview":
        return generated
    if (
        generated == "code_module_overview"
        and CONTENT_SHA256.fullmatch(fields.get("module_id", "")) is not None
    ):
        return generated
    return None


def validate_changeset_page_sources(
    page_sources: Mapping[str, tuple[str, ...]],
    source_ids: Iterable[str],
) -> None:
    """Require candidate pages to give every ChangeSet source one page owner."""
    if not page_sources:
        return
    expected_source_ids = set(source_ids)
    source_owners: dict[str, str] = {}
    for page_path, page_source_ids in page_sources.items():
        for source_id in page_source_ids:
            previous_page_path = source_owners.get(source_id)
            if previous_page_path is not None:
                raise WorkspaceIntegrityError(
                    "candidate Wiki page source belongs to multiple pages: " + source_id
                )
            source_owners[source_id] = page_path
    if set(source_owners) != expected_source_ids:
        raise WorkspaceIntegrityError(
            "candidate Wiki page sources must exactly match ChangeSet source IDs"
        )


def normalize_page_sources(
    page_sources: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for page_path, source_ids in page_sources.items():
        if not is_stable_wiki_page_path(page_path):
            raise ValueError("page source mappings must stay below wiki/pages/")
        if not source_ids:
            raise ValueError(f"page source mappings must include sources: {page_path}")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"page source mappings must not duplicate sources: {page_path}")
        if any(CONTENT_SHA256.fullmatch(source_id) is None for source_id in source_ids):
            raise ValueError(f"page source mappings contain an invalid source ID: {page_path}")
        normalized[page_path] = tuple(sorted(source_ids))
    return normalized


def normalize_page_facts(
    page_facts: Mapping[str, tuple[WikiFact, ...]],
) -> dict[str, tuple[WikiFact, ...]]:
    normalized: dict[str, tuple[WikiFact, ...]] = {}
    validate_stable_page_paths(page_facts)
    for page_path, facts in page_facts.items():
        if any(fact.page_path != page_path for fact in facts):
            raise ValueError("Wiki fact page path does not match its mapping")
        if len({fact.fact_id for fact in facts}) != len(facts):
            raise ValueError(f"Wiki facts must have unique identities: {page_path}")
        for fact in facts:
            if CONTENT_SHA256.fullmatch(fact.fact_id) is None:
                raise ValueError("Wiki fact identity must be a SHA-256 digest")
            if CONTENT_SHA256.fullmatch(fact.source_id) is None:
                raise ValueError("Wiki fact source identity must be a SHA-256 digest")
            if fact.source_version < 1:
                raise ValueError("Wiki fact SourceVersion must be positive")
            if CHAR_LOCATOR.fullmatch(fact.locator) is None:
                raise ValueError("Wiki fact locator must be a character range")
            if not fact.quote:
                raise ValueError("Wiki fact quote must not be empty")
        normalized[page_path] = tuple(sorted(facts, key=lambda fact: fact.fact_id))
    return normalized


def validate_stable_page_paths(page_paths: Iterable[str]) -> None:
    if any(not is_stable_wiki_page_path(path) for path in page_paths):
        raise ValueError("Wiki fact mappings must stay below wiki/pages/")


def indexed_facts(rows: Iterable[sqlite3.Row]) -> dict[str, tuple[IndexedWikiFact, ...]]:
    facts: dict[str, list[IndexedWikiFact]] = {}
    for row in rows:
        page_path = str(row["page_path"])
        facts.setdefault(page_path, []).append(
            IndexedWikiFact(
                fact_id=str(row["fact_id"]),
                page_path=page_path,
                repository_id=(
                    str(row["repository_id"]) if row["repository_id"] is not None else None
                ),
                source_id=str(row["source_id"]),
                source_version=int(row["source_version"]),
                locator=str(row["locator"]),
                section_path=str(row["section_path"]),
                quote=str(row["quote"]),
                routing_text=str(row["routing_text"]),
                symbol=str(row["symbol"]) if row["symbol"] is not None else None,
                relation_type=(
                    str(row["relation_type"]) if row["relation_type"] is not None else None
                ),
            )
        )
    return {path: tuple(records) for path, records in facts.items()}


def is_stable_wiki_page_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        "\\" not in path
        and len(parts) >= 3
        and parts[:2] == ("wiki", "pages")
        and path.endswith(".md")
        and all(part not in {"", ".", ".."} for part in parts)
        and str(PurePosixPath(path)) == path
    )


def page_source_ids_from_frontmatter(content: str) -> tuple[str, ...]:
    if not content.startswith("---\n"):
        return ()
    closing = content.find("\n---\n", len("---\n"))
    if closing < 0:
        return ()
    for line in content[len("---\n") : closing].splitlines():
        key, separator, value = line.partition(":")
        if key.strip() != "sources" or not separator:
            continue
        try:
            decoded = json.loads(value.strip())
        except json.JSONDecodeError as exc:
            raise WorkspaceIntegrityError(
                "candidate Wiki page has invalid sources metadata"
            ) from exc
        if not isinstance(decoded, list) or not all(
            isinstance(source_id, str) for source_id in decoded
        ):
            raise WorkspaceIntegrityError("candidate Wiki page has invalid sources metadata")
        source_ids = tuple(decoded)
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise WorkspaceIntegrityError("candidate Wiki page has invalid sources metadata")
        if any(CONTENT_SHA256.fullmatch(source_id) is None for source_id in source_ids):
            raise WorkspaceIntegrityError("candidate Wiki page has invalid sources metadata")
        return source_ids
    return ()
