"""Deterministic question answering over applied Wiki pages."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, TypedDict

from memoryforge.workspace import _read_blob_bytes

_FACT = re.compile(r"^- (?P<quote>.+?) \[\^(?P<footnote>[^\]]+)\]$", re.MULTILINE)
_FOOTNOTE = re.compile(
    r"^\[\^(?P<footnote>[^\]]+)\]: "
    r"`(?P<snapshot_uri>mf://blob/[a-f0-9]{64})` · "
    r"`(?P<locator>chars:\d+-\d+)` · "
    r"source `(?P<source_id>[a-f0-9]{64})`$",
    re.MULTILINE,
)
_WORDS = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)
_STOP_WORDS = {
    "a",
    "an",
    "are",
    "do",
    "does",
    "how",
    "is",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "了",
    "什么",
    "是",
    "的",
}


class CitationPayload(TypedDict):
    source_id: str
    snapshot_uri: str
    locator: str
    quote: str


class AskPayload(TypedDict):
    status: Literal["answered", "unknown"]
    answer: str
    citations: list[CitationPayload]
    source_id: str | None
    snapshot_uri: str | None
    locator: str | None
    quote: str | None


def answer_question(workspace_root: Path, question: str) -> AskPayload:
    """Return the most relevant verified fact from applied Wiki Markdown."""
    question_terms = _terms(question)
    best: tuple[tuple[int, int], CitationPayload] | None = None

    wiki_root = workspace_root / "wiki"
    for page in sorted(wiki_root.glob("**/*.md")):
        for citation in _page_citations(
            workspace_root,
            page.read_text(encoding="utf-8"),
        ):
            overlap = question_terms & _terms(citation["quote"])
            score = (len(overlap), sum(len(term) for term in overlap))
            sufficient_match = len(overlap) >= 2 or (len(question_terms) == 1 and len(overlap) == 1)
            if sufficient_match and (best is None or score > best[0]):
                best = (score, citation)

    if best is None:
        return {
            "status": "unknown",
            "answer": "不知道",
            "citations": [],
            "source_id": None,
            "snapshot_uri": None,
            "locator": None,
            "quote": None,
        }

    citation = best[1]
    return {
        "status": "answered",
        "answer": citation["quote"],
        "citations": [citation],
        **citation,
    }


def _page_citations(workspace_root: Path, content: str) -> list[CitationPayload]:
    section_match = re.search(
        r"^## Verified facts\s*$\n(?P<section>.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if section_match is None:
        return []

    footnotes = {
        match.group("footnote"): match.groupdict() for match in _FOOTNOTE.finditer(content)
    }
    citations: list[CitationPayload] = []
    for fact in _FACT.finditer(section_match.group("section")):
        footnote = footnotes.get(fact.group("footnote"))
        if footnote is None:
            continue
        citations.append(
            {
                "source_id": footnote["source_id"],
                "snapshot_uri": footnote["snapshot_uri"],
                "locator": footnote["locator"],
                "quote": _blob_quote(
                    workspace_root,
                    footnote["snapshot_uri"],
                    footnote["locator"],
                ),
            }
        )
    return citations


def _blob_quote(workspace_root: Path, snapshot_uri: str, locator: str) -> str:
    content_sha256 = snapshot_uri.removeprefix("mf://blob/")
    snapshot_path = Path("raw/blobs") / content_sha256[:2] / f"{content_sha256}.blob"
    content = _read_blob_bytes(
        workspace_root,
        content_sha256,
        snapshot_path,
    ).decode("utf-8")
    start_text, end_text = locator.removeprefix("chars:").split("-")
    return content[int(start_text) : int(end_text)]


def _terms(text: str) -> set[str]:
    return {
        token
        for token in (match.group().lower() for match in _WORDS.finditer(text))
        if token not in _STOP_WORDS
    }
