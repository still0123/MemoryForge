"""Deterministic progressive queries over applied Wiki pages."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal, NotRequired, TypedDict

from memoryforge.provider import OpenAICompatibleProvider, ProviderUnavailableError
from memoryforge.workspace import (
    find_applied_page_paths,
    is_public_source_version,
    read_source_excerpt,
    repository_page_paths,
)

_FACT = re.compile(r"^- (?P<quote>.+?) \[\^(?P<footnote>[^\]]+)\]$", re.MULTILINE)
_FOOTNOTE = re.compile(
    r"^\[\^(?P<footnote>[^\]]+)\]: source "
    r"`(?P<source_id>[a-f0-9]{64})` · "
    r"revision `(?P<source_version>\d+)` · "
    r"`(?P<locator>chars:\d+-\d+)`$",
    re.MULTILINE,
)
_INDEX_ENTRY = re.compile(
    r"^- \[(?P<title>(?:\\.|[^\]])+)\]\((?P<path>[^)]+)\) — (?P<summary>.+)$",
    re.MULTILINE,
)
_FRONTMATTER = re.compile(r"\A---\n(?P<fields>.*?)\n---\n", re.DOTALL)
_WORDS = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_REPOSITORY_OVERVIEW_LINK = re.compile(r"^pages/repository-[a-f0-9]{12}\.md$")
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
# ponytail: downweight two generic verbs; learn weights only if the fixed suite regresses.
_RANKING_STOP_WORDS = {"不能", "运行"}


class CitationPayload(TypedDict):
    source_id: str
    source_version: int
    locator: str
    quote: str


class EvidencePayload(CitationPayload):
    text: str


class AskPayload(TypedDict):
    status: Literal["answered", "unknown"]
    answer: str
    citations: list[CitationPayload]
    wiki_pages: list[str]
    source_id: str | None
    source_version: int | None
    locator: str | None
    quote: str | None
    model_status: NotRequired[Literal["used", "fallback"]]
    trace: NotRequired[list[TraceStep]]
    evidence: NotRequired[list[EvidencePayload]]


class TraceStep(TypedDict):
    level: Literal["L0", "L1", "L2", "L3"]
    artifact: str


def answer_question(
    workspace_root: Path,
    question: str,
    *,
    debug: bool = False,
    verify: bool = False,
    max_pages: int = 3,
    max_citations: int = 1,
    provider: OpenAICompatibleProvider | None = None,
    allow_local: bool = False,
    repository_id: str | None = None,
) -> AskPayload:
    """Answer from a bounded set of Wiki pages, expanding raw evidence only on request."""
    _validate_max_pages(max_pages)
    _validate_max_citations(max_citations)
    question_terms = _terms(question)
    trace: list[TraceStep] = []
    if not question_terms:
        return _unknown_payload(debug, trace)

    matches: list[tuple[tuple[int, ...], str, CitationPayload]] = []
    candidate_matches: list[tuple[tuple[int, ...], str, CitationPayload]] = []

    for page in _candidate_pages(
        workspace_root,
        question_terms,
        max_pages=max_pages,
        trace=trace,
        repository_id=repository_id,
    ):
        content = page.read_text(encoding="utf-8")
        trace.append({"level": "L1", "artifact": str(page.relative_to(workspace_root))})
        for citation in _page_citations(content):
            overlap = question_terms & _terms(citation["quote"])
            ranking_overlap = overlap - _RANKING_STOP_WORDS
            score = (
                sum(not _CJK.fullmatch(term) for term in ranking_overlap),
                len(ranking_overlap),
                sum(len(term) for term in ranking_overlap),
            )
            candidate_matches.append((score, str(page.relative_to(workspace_root)), citation))
            has_cjk_terms = any(_CJK.fullmatch(term) for term in question_terms)
            required_overlap = 1 if len(question_terms) == 1 else 2
            if has_cjk_terms:
                required_overlap = min(3, len(question_terms))
                if len(overlap) >= 2 and any(
                    not _CJK.fullmatch(term) for term in overlap
                ):
                    required_overlap = 2
            sufficient_match = len(overlap) >= required_overlap
            if sufficient_match:
                matches.append((score, str(page.relative_to(workspace_root)), citation))

    if not matches and provider is None:
        return _unknown_payload(debug, trace)

    model_status: Literal["used", "fallback"] | None = None
    if provider is None:
        selected = _top_matches(matches, max_citations, question_terms=question_terms)
        answer = " ".join(citation["quote"] for _, citation in selected)
    else:
        try:
            generated = _model_answer(
                workspace_root,
                question,
                matches or candidate_matches,
                provider,
                allow_local=allow_local,
            )
        except ProviderUnavailableError:
            fallback_matches = _usable_matches(
                workspace_root,
                matches or candidate_matches,
                allow_local=allow_local,
            )
            if not fallback_matches:
                return _unknown_payload(debug, trace)
            selected = _top_matches(
                fallback_matches,
                max_citations,
                question_terms=question_terms,
            )
            answer = " ".join(citation["quote"] for _, citation in selected)
            model_status = "fallback"
        else:
            if generated is None:
                return _unknown_payload(debug, trace)
            answer, selected = generated
            model_status = "used"

    citations = [citation for _, citation in selected]
    pages = list(dict.fromkeys(page_path for page_path, _ in selected))
    for page_path in pages:
        trace.append({"level": "L2", "artifact": page_path})
    citation = citations[0]
    result: AskPayload = {
        "status": "answered",
        "answer": answer,
        "citations": citations,
        "wiki_pages": pages,
        "source_id": citation["source_id"],
        "source_version": citation["source_version"],
        "locator": citation["locator"],
        "quote": citation["quote"],
    }
    if model_status is not None:
        result["model_status"] = model_status
    if verify:
        evidence: list[EvidencePayload] = []
        for selected_citation in citations:
            evidence.append(
                {
                    **selected_citation,
                    "text": read_source_excerpt(
                        workspace_root,
                        source_id=selected_citation["source_id"],
                        source_version=selected_citation["source_version"],
                        locator=selected_citation["locator"],
                    ),
                }
            )
            trace.append(
                {
                    "level": "L3",
                    "artifact": (
                        f"source {selected_citation['source_id']} "
                        f"revision {selected_citation['source_version']}"
                    ),
                }
            )
        result["evidence"] = evidence
    if debug:
        result["trace"] = trace
    return result


def _model_answer(
    workspace_root: Path,
    question: str,
    matches: list[tuple[tuple[int, ...], str, CitationPayload]],
    provider: OpenAICompatibleProvider,
    *,
    allow_local: bool,
) -> tuple[str, list[tuple[str, CitationPayload]]] | None:
    usable_matches = [
        (page_path, citation)
        for _, page_path, citation in _usable_matches(
            workspace_root, matches, allow_local=allow_local
        )
    ][:12]
    if not usable_matches:
        raise ValueError("LLM answers require public source evidence")

    answer, indexes = provider.answer_with_evidence(_answer_messages(question, usable_matches))
    selected: list[tuple[str, CitationPayload]] = []
    seen: set[tuple[str, int, str]] = set()
    for index in indexes:
        if isinstance(index, bool) or not 0 <= index < len(usable_matches):
            continue
        page_path, citation = usable_matches[index]
        key = (citation["source_id"], citation["source_version"], citation["locator"])
        if key not in seen:
            seen.add(key)
            selected.append((page_path, citation))
    if not answer.strip() or answer.strip() == "不知道" or not selected:
        return None
    return answer.strip(), selected


def _usable_matches(
    workspace_root: Path,
    matches: list[tuple[tuple[int, ...], str, CitationPayload]],
    *,
    allow_local: bool,
) -> list[tuple[tuple[int, ...], str, CitationPayload]]:
    return [
        match
        for match in matches
        if allow_local
        or is_public_source_version(
            workspace_root,
            source_id=match[2]["source_id"],
            source_version=match[2]["source_version"],
        )
    ]


def _answer_messages(
    question: str,
    matches: list[tuple[str, CitationPayload]],
) -> list[dict[str, str]]:
    facts = [
        {"index": index, "quote": citation["quote"]}
        for index, (_, citation) in enumerate(matches)
    ]
    return [
        {
            "role": "system",
            "content": (
                "Answer only from the candidate facts. Return JSON with answer and "
                "citation_indexes. citation_indexes must contain the zero-based indexes of "
                "facts that support the answer. If the facts do not answer the question, "
                'return {"answer":"不知道","citation_indexes":[]}. Reply in the question language.'
                " You may restate an explicit contrast in direct language, but do not add details "
                "beyond the facts."
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"question": question, "facts": facts}, ensure_ascii=False),
        },
    ]


def _unknown_payload(debug: bool, trace: list[TraceStep]) -> AskPayload:
    result: AskPayload = {
        "status": "unknown",
        "answer": "不知道",
        "citations": [],
        "wiki_pages": [],
        "source_id": None,
        "source_version": None,
        "locator": None,
        "quote": None,
    }
    if debug:
        result["trace"] = trace
    return result


def _candidate_pages(
    workspace_root: Path,
    question_terms: set[str],
    *,
    max_pages: int,
    trace: list[TraceStep],
    repository_id: str | None,
) -> list[Path]:
    wiki_root = workspace_root / "wiki"
    index = wiki_root / "INDEX.md"
    scored: list[tuple[tuple[int, ...], Path]] = []
    allowed_paths = (
        set(repository_page_paths(workspace_root, repository_id))
        if repository_id is not None
        else None
    )
    safe_index = _safe_wiki_index(workspace_root, index)
    if safe_index is not None:
        trace.append({"level": "L0", "artifact": "wiki/INDEX.md"})
        for entry in _INDEX_ENTRY.finditer(safe_index.read_text(encoding="utf-8")):
            if _REPOSITORY_OVERVIEW_LINK.fullmatch(entry.group("path")):
                continue
            page = _safe_wiki_page(workspace_root, wiki_root / entry.group("path"))
            if page is None:
                continue
            relative_path = str(page.relative_to(workspace_root))
            if allowed_paths is not None and relative_path not in allowed_paths:
                continue
            title = _unescape_link_text(entry.group("title"))
            overlap = question_terms & _terms(f"{title} {entry.group('summary')}")
            if overlap:
                scored.append(
                    (
                        (
                            sum(not _CJK.fullmatch(term) for term in overlap),
                            len(overlap),
                            sum(len(term) for term in overlap),
                        ),
                        page,
                    )
                )
    fts_paths: tuple[str, ...] = ()
    index_path = workspace_root / ".memoryforge" / "index.sqlite"
    if safe_index is None or (index_path.is_file() and not index_path.is_symlink()):
        if repository_id is None:
            fts_paths = find_applied_page_paths(
                workspace_root,
                " ".join(sorted(question_terms)),
                limit=max_pages,
            )
        else:
            fts_paths = find_applied_page_paths(
                workspace_root,
                " ".join(sorted(question_terms)),
                limit=max_pages,
                repository_id=repository_id,
            )
    if fts_paths:
        trace.append({"level": "L0", "artifact": "SQLite FTS5 applied-source index"})
    candidates: list[Path] = []
    for path in fts_paths:
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None:
            candidates.append(page)
    for _, page in sorted(
        scored,
        key=lambda candidate: (
            -candidate[0][0],
            -candidate[0][1],
            -candidate[0][2],
            str(candidate[1].relative_to(workspace_root)),
        ),
    ):
        if page not in candidates:
            candidates.append(page)
    return candidates[:max_pages]


def _validate_max_pages(max_pages: int) -> None:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 10:
        raise ValueError("max_pages must be an integer between 1 and 10")


def _validate_max_citations(max_citations: int) -> None:
    if (
        isinstance(max_citations, bool)
        or not isinstance(max_citations, int)
        or not 1 <= max_citations <= 10
    ):
        raise ValueError("max_citations must be an integer between 1 and 10")


def _top_matches(
    matches: list[tuple[tuple[int, ...], str, CitationPayload]],
    max_citations: int,
    *,
    question_terms: set[str],
) -> list[tuple[str, CitationPayload]]:
    # ponytail: greedy page-level coverage is O(n²), sufficient for the 6-citation budget.
    selected: list[tuple[str, CitationPayload]] = []
    seen: set[tuple[str, int, str]] = set()
    remaining = sorted(matches, key=lambda match: match[0], reverse=True)
    covered_terms: set[str] = set()
    while remaining and len(selected) < max_citations:
        if not selected:
            selected_index = 0
        else:
            selected_index = max(
                range(len(remaining)),
                key=lambda index: (
                    len(
                        (_terms(remaining[index][2]["quote"]) & question_terms)
                        - covered_terms
                    ),
                    remaining[index][0][0],
                    remaining[index][0][1],
                ),
            )
        _, page_path, citation = remaining.pop(selected_index)
        key = (citation["source_id"], citation["source_version"], citation["locator"])
        if key in seen:
            continue
        seen.add(key)
        selected.append((page_path, citation))
        covered_terms.update(_terms(citation["quote"]) & question_terms)
    return selected


def _safe_wiki_index(workspace_root: Path, index: Path) -> Path | None:
    """Accept only the real ``workspace/wiki/INDEX.md`` file."""
    wiki_root = workspace_root / "wiki"
    expected = wiki_root / "INDEX.md"
    if (
        index != expected
        or not wiki_root.is_dir()
        or wiki_root.is_symlink()
        or index.is_symlink()
        or not index.is_file()
    ):
        return None
    try:
        if index.resolve(strict=True) != wiki_root.resolve(strict=True) / "INDEX.md":
            return None
    except OSError:
        return None
    return index


def _safe_wiki_page(workspace_root: Path, page: Path) -> Path | None:
    """Accept only real stable Wiki pages below ``wiki/pages``."""
    pages_root = workspace_root / "wiki" / "pages"
    try:
        stable_path = page.relative_to(workspace_root).as_posix()
        page_relative = page.relative_to(pages_root)
    except ValueError:
        return None
    parts = PurePosixPath(stable_path).parts
    if (
        "\\" in stable_path
        or len(parts) < 3
        or parts[:2] != ("wiki", "pages")
        or not stable_path.endswith(".md")
        or any(part in {"", ".", ".."} for part in parts)
        or str(PurePosixPath(stable_path)) != stable_path
        or not pages_root.is_dir()
        or pages_root.is_symlink()
        or not page.is_file()
    ):
        return None

    current = pages_root
    for part in page_relative.parts:
        current /= part
        if current.is_symlink():
            return None
    try:
        page.resolve(strict=True).relative_to(pages_root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return page


def _page_citations(content: str) -> list[CitationPayload]:
    if not _page_matches_frontmatter(content):
        return []
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
                "source_version": int(footnote["source_version"]),
                "locator": footnote["locator"],
                "quote": fact.group("quote"),
            }
        )
    return citations


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


def _unescape_link_text(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for match in _WORDS.finditer(text):
        token = match.group().lower()
        if _CJK.fullmatch(token):
            if token in _STOP_WORDS:
                continue
            if len(token) == 1:
                terms.add(token)
            else:
                terms.update(token[index : index + 2] for index in range(len(token) - 1))
        elif token not in _STOP_WORDS:
            terms.add(token)
    return terms
