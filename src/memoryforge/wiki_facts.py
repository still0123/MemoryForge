"""Grounded Wiki fact parsing shared by queries and the local fact index."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, NotRequired, TypedDict

_FACT = re.compile(r"^- (?P<quote>.+?) \[\^(?P<footnote>[^\]]+)\]$", re.MULTILINE)
_RELATION_FACT = re.compile(
    r"^(?P<route>`[^`]+` \([a-z_]+\)): "
    r'(?P<evidence>"(?:\\.|[^"\\])*")$'
)
_RELATION_ROUTE = re.compile(r"^`[^`]+` \((?P<relation_type>[a-z_]+)\)$")
_SYMBOL_FACT = re.compile(r"^`(?P<symbol>[^`]+)` \([^)]+\): `.*`$")
_FOOTNOTE = re.compile(
    r"^\[\^(?P<footnote>[^\]]+)\]: source "
    r"`(?P<source_id>[a-f0-9]{64})` · "
    r"revision `(?P<source_version>\d+)` · "
    r"`(?P<locator>chars:\d+-\d+)`$",
    re.MULTILINE,
)
_FRONTMATTER = re.compile(r"\A---\n(?P<fields>.*?)\n---\n", re.DOTALL)
_FACT_SECTION = re.compile(r"^#{3,6} (?P<section>.+?)\s*$", re.MULTILINE)
_STABLE_PAGE = re.compile(r"^wiki/pages/(?:[^/]+/)*[^/]+\.md$")


class CitationPayload(TypedDict):
    source_id: str
    source_version: int
    locator: str
    quote: str
    section_path: NotRequired[str]
    routing_text: NotRequired[str]
    is_summary: NotRequired[bool]


@dataclass(frozen=True)
class WikiFact:
    fact_id: str
    page_path: str
    source_id: str
    source_version: int
    locator: str
    section_path: str
    quote: str
    routing_text: str
    symbol: str | None
    relation_type: str | None


@dataclass(frozen=True)
class IndexedWikiFact:
    fact_id: str
    page_path: str
    repository_id: str | None
    source_id: str
    source_version: int
    locator: str
    section_path: str
    quote: str
    routing_text: str
    symbol: str | None
    relation_type: str | None


@dataclass(frozen=True)
class WikiFactSearchResult(IndexedWikiFact):
    rank: float


@dataclass(frozen=True)
class AppliedCodeSymbolMatch(IndexedWikiFact):
    identifier: str
    match_kind: Literal["qualified_name", "display_name"]


def parse_page_citations(content: str) -> list[CitationPayload]:
    """Parse exact Citation facts from one generated Wiki page."""
    if not _page_matches_frontmatter(content):
        return []
    section_matches = re.finditer(
        r"^## (?P<name>Verified facts|Verified symbols|Verified dependencies)\s*$"
        r"\n(?P<section>.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    footnotes = {
        match.group("footnote"): match.groupdict() for match in _FOOTNOTE.finditer(content)
    }
    citations: list[CitationPayload] = []
    for section_match in section_matches:
        section_name = section_match.group("name")
        section_text = section_match.group("section")
        for fact_index, fact in enumerate(_FACT.finditer(section_text)):
            footnote = footnotes.get(fact.group("footnote"))
            if footnote is None:
                continue
            quote = fact.group("quote")
            relation_fact = _RELATION_FACT.fullmatch(quote)
            if section_name == "Verified dependencies":
                if relation_fact is None:
                    continue
                try:
                    evidence = json.loads(relation_fact.group("evidence"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(evidence, str):
                    continue
                quote = evidence
            citation: CitationPayload = {
                "source_id": footnote["source_id"],
                "source_version": int(footnote["source_version"]),
                "locator": footnote["locator"],
                "quote": quote,
            }
            if relation_fact is not None:
                citation["routing_text"] = relation_fact.group("route")
            if section_name != "Verified dependencies" and fact_index == 0:
                citation["is_summary"] = True
            section = next(
                (
                    match.group("section")
                    for match in reversed(list(_FACT_SECTION.finditer(section_text)))
                    if match.start() <= fact.start()
                ),
                "",
            )
            if section:
                citation["section_path"] = section
            citations.append(citation)
    return citations


def parse_page_facts(page_path: str, content: str) -> tuple[WikiFact, ...]:
    """Create deterministic fact records for one stable Wiki page."""
    _validate_page_path(page_path)
    facts = []
    for citation in parse_page_citations(content):
        routing_text = citation.get("routing_text", "")
        symbol_match = _SYMBOL_FACT.fullmatch(citation["quote"])
        relation_match = _RELATION_ROUTE.fullmatch(routing_text)
        identity = (
            page_path,
            citation["source_id"],
            citation["source_version"],
            citation["locator"],
            citation.get("section_path", ""),
            citation["quote"],
            routing_text,
        )
        facts.append(
            WikiFact(
                fact_id=hashlib.sha256(
                    json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                page_path=page_path,
                source_id=citation["source_id"],
                source_version=citation["source_version"],
                locator=citation["locator"],
                section_path=citation.get("section_path", ""),
                quote=citation["quote"],
                routing_text=routing_text,
                symbol=symbol_match.group("symbol") if symbol_match else None,
                relation_type=(relation_match.group("relation_type") if relation_match else None),
            )
        )
    return tuple(facts)


def _page_matches_frontmatter(content: str) -> bool:
    match = _FRONTMATTER.match(content)
    if match is None:
        return True
    fields = {
        key.strip(): value.strip()
        for line in match.group("fields").splitlines()
        for key, separator, value in (line.partition(":"),)
        if separator
    }
    return fields.get("type") in {"entity", "concept", "synthesis"}


def _validate_page_path(page_path: str) -> None:
    if (
        _STABLE_PAGE.fullmatch(page_path) is None
        or PurePosixPath(page_path).is_absolute()
        or ".." in PurePosixPath(page_path).parts
    ):
        raise ValueError("fact page path must be a Markdown file below wiki/pages/")
