"""Deterministic progressive queries over applied Wiki pages."""

from __future__ import annotations

import json
import re
from collections import Counter
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
_FACT_SECTION = re.compile(r"^#{3,6} (?P<section>.+?)\s*$", re.MULTILINE)
_WORDS = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_CAMEL_CASE_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+")
_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_REPOSITORY_OVERVIEW_LINK = re.compile(r"^pages/repository-[a-f0-9]{12}\.md$")
_NEGATION_CUES = ("不", "无", "未", "没", "避免", "拒绝")
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
_QUESTION_NOISE_TERMS = {"在哪"}
_FAILURE_TERM_EXPANSIONS = {"超时", "异常", "失败", "缺失", "回退"}
_CODE_QUERY_EXPANSIONS = {
    "模块": {"module", "modules"},
    "子模": {"child", "children"},
    "入口": {"entry", "entries", "point", "points", "handler", "handlers"},
    "依赖": {"depend", "dependency", "dependencies", "import", "imports"},
    "操作": {"operation", "operations"},
    "职责": {"responsibility", "responsibilities"},
    "方法": {"method", "methods", "func", "function", "functions"},
    "字段": {"field", "fields", "struct", "attribute", "attributes"},
}
_ENVIRONMENT_ASSIGNMENT = re.compile(r"\b(?:export\s+)?[A-Z][A-Z0-9_]{2,}=")
_CODE_FACT = re.compile(r"^(?:package|type|func|class|def)\b")


class CitationPayload(TypedDict):
    source_id: str
    source_version: int
    locator: str
    quote: str
    section_path: NotRequired[str]


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
    conversation_context: str = "",
) -> AskPayload:
    """Answer from a bounded set of Wiki pages, expanding raw evidence only on request."""
    _validate_max_pages(max_pages)
    _validate_max_citations(max_citations)
    base_question_terms = _terms(question)
    question_terms = _expanded_question_terms(base_question_terms)
    identifier_terms = {term for term in base_question_terms if not _CJK.fullmatch(term)}
    yes_no_focus_terms = _yes_no_focus_terms(question)
    focus_terms = _question_focus_terms(question) | yes_no_focus_terms
    if "子模" in base_question_terms:
        focus_terms.update({"child", "children", "modules"})
    if "方法" in base_question_terms:
        focus_terms.update({"method", "methods", "func", "function", "functions"})
    if "字段" in base_question_terms:
        focus_terms.update({"field", "fields", "struct", "attribute", "attributes"})
    answer_citation_limit = _answer_citation_limit(question, max_citations)
    prefer_environment_assignments = "环境变量" in question
    prefer_failure_facts = bool({"不可", "可用", "失败", "超时"} & base_question_terms)
    use_section_routes = (
        prefer_environment_assignments
        or prefer_failure_facts
        or any(marker in question for marker in ("子模块", "字段", "属性", "方法"))
    )
    prefer_code_modules = (
        "文件夹" in question
        or "子模块" in question
        or "module" in question.lower()
        or any(
            marker in question
            for marker in ("模块主要", "模块负责", "模块作用", "模块包含", "模块有哪些")
        )
    )
    trace: list[TraceStep] = []
    if not question_terms:
        return _unknown_payload(debug, trace)

    raw_matches: list[tuple[frozenset[str], bool, str, CitationPayload]] = []
    raw_candidate_matches: list[tuple[frozenset[str], bool, str, CitationPayload]] = []

    for page_rank, page in enumerate(
        _candidate_pages(
            workspace_root,
            question,
            question_terms,
            max_pages=max_pages,
            trace=trace,
            repository_id=repository_id,
            prefer_index_routes=max_citations > 1 or _has_many_index_routes(workspace_root),
        )
    ):
        content = page.read_text(encoding="utf-8")
        trace.append({"level": "L1", "artifact": str(page.relative_to(workspace_root))})
        for index, citation in enumerate(_page_citations(content)):
            overlap = (
                _section_matching_terms(question_terms, citation)
                if use_section_routes
                else _matching_terms(question_terms, citation)
            )
            page_path = str(page.relative_to(workspace_root))
            raw_candidate_matches.append((frozenset(overlap), index == 0, page_path, citation))
            has_cjk_terms = any(_CJK.fullmatch(term) for term in question_terms)
            required_overlap = 1 if len(question_terms) == 1 else 2
            if has_cjk_terms:
                required_overlap = min(3, len(question_terms))
                aligned_negation = any(cue in question for cue in _NEGATION_CUES) and any(
                    cue in citation["quote"] for cue in _NEGATION_CUES
                )
                if page_rank == 0 and index == 0 and aligned_negation:
                    required_overlap = min(required_overlap, 2)
                if len(overlap) >= 2 and any(not _CJK.fullmatch(term) for term in overlap):
                    required_overlap = 2
            sufficient_match = len(overlap) >= required_overlap
            if "字段" in base_question_terms and overlap & focus_terms:
                sufficient_match = True
            if "方法" in base_question_terms and overlap & identifier_terms:
                sufficient_match = True
            if (
                identifier_terms
                and not overlap & identifier_terms
                and not (
                    ("字段" in base_question_terms and overlap & focus_terms)
                    or ("方法" in base_question_terms and overlap & identifier_terms)
                )
            ):
                sufficient_match = False
            if sufficient_match:
                raw_matches.append((frozenset(overlap), index == 0, page_path, citation))

    if "方法" in base_question_terms and identifier_terms:
        method_symbol = max(identifier_terms, key=len)
        raw_matches = [match for match in raw_matches if method_symbol in _citation_terms(match[3])]
        raw_candidate_matches = [
            match for match in raw_candidate_matches if method_symbol in _citation_terms(match[3])
        ]

    matches = _rank_matches(
        raw_matches,
        question_terms=question_terms,
        focus_terms=focus_terms,
        prioritize_focus=bool(yes_no_focus_terms and {"依赖", "外部"} <= base_question_terms),
        prefer_environment_assignments=prefer_environment_assignments,
        prefer_failure_facts=prefer_failure_facts,
        prefer_code_modules=prefer_code_modules,
    )
    candidate_matches = _rank_matches(
        raw_candidate_matches,
        question_terms=question_terms,
        focus_terms=focus_terms,
        prioritize_focus=bool(yes_no_focus_terms and {"依赖", "外部"} <= base_question_terms),
        prefer_environment_assignments=prefer_environment_assignments,
        prefer_failure_facts=prefer_failure_facts,
        prefer_code_modules=prefer_code_modules,
    )
    model_candidates = [
        match for match in candidate_matches if _has_direct_evidence(question_terms, match[2])
    ]

    if not matches and (provider is None or not model_candidates):
        return _unknown_payload(debug, trace)

    model_status: Literal["used", "fallback"] | None = None
    if provider is None:
        selected = _top_matches(matches, answer_citation_limit, question_terms=question_terms)
        answer = (
            _fallback_answer(question, selected)
            if "方法" in question
            else " ".join(citation["quote"] for _, citation in selected)
        )
    else:
        try:
            generated = _model_answer(
                workspace_root,
                question,
                matches or model_candidates,
                provider,
                allow_local=allow_local,
                conversation_context=conversation_context,
            )
        except ProviderUnavailableError:
            module_fallbacks = [
                match
                for match in model_candidates
                if match[2]["quote"].startswith("Main exported operations in `")
            ]
            fallback_matches = _usable_matches(
                workspace_root,
                module_fallbacks or matches,
                allow_local=allow_local,
            )
            if not fallback_matches:
                return _unknown_payload(debug, trace)
            selected = _top_matches(
                fallback_matches,
                answer_citation_limit,
                question_terms=question_terms,
            )
            answer = _fallback_answer(question, selected)
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
    conversation_context: str,
) -> tuple[str, list[tuple[str, CitationPayload]]] | None:
    usable_matches = [
        (page_path, citation)
        for _, page_path, citation in _usable_matches(
            workspace_root, matches, allow_local=allow_local
        )
    ][:12]
    if not usable_matches:
        raise ValueError("LLM answers require public source evidence")

    answer, indexes = provider.answer_with_evidence(
        _answer_messages(question, usable_matches, conversation_context)
    )
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
    conversation_context: str = "",
) -> list[dict[str, str]]:
    facts = [
        {
            "index": index,
            "quote": citation["quote"],
            **({"section": citation["section_path"]} if "section_path" in citation else {}),
        }
        for index, (_, citation) in enumerate(matches)
    ]
    user_payload: dict[str, object] = {"question": question, "facts": facts}
    if conversation_context:
        user_payload["conversation_context"] = conversation_context
    return [
        {
            "role": "system",
            "content": (
                "Answer only from the candidate facts. Return JSON with answer and "
                "citation_indexes. citation_indexes must contain the zero-based indexes of "
                "facts that support the answer. If the facts do not answer the question, "
                'return {"answer":"不知道","citation_indexes":[]}. Reply in the question language.'
                " You may restate an explicit contrast in direct language, but do not add details "
                "beyond the facts. When asked what a code module does, treat its directory paths "
                "and exported code symbol names as answerable evidence: translate and summarize "
                "those names into a concise capability list instead of returning unknown. Do not "
                "invent implementation details. A section named Identity only describes the "
                "module path and aliases; it never means authentication. Prefer facts that answer "
                "the question's condition "
                "or conclusion "
                "over broad background facts that only share the same topic. For a yes-or-no "
                "question, answer directly and do not return a Markdown table when a concise "
                "fact states the conclusion."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]


def _fallback_answer(
    question: str,
    selected: list[tuple[str, CitationPayload]],
) -> str:
    quote = selected[0][1]["quote"]
    if "方法" in _terms(question):
        signature = next(
            (
                citation["quote"]
                for _, citation in selected
                if citation["quote"].startswith("func ")
            ),
            quote,
        )
        body_lines = [
            citation["quote"].strip().rstrip("{").strip()
            for _, citation in selected
            if not citation["quote"].startswith("func ")
        ]
        if body_lines:
            return f"{signature} 的关键代码逻辑是：" + "；".join(body_lines) + "。"
    if len(selected) == 1 and quote.startswith("Main exported operations in `"):
        module = quote.split("`", maxsplit=2)[1]
        operations = quote.partition(": ")[2]
        if any(_CJK.fullmatch(term) for term in _terms(question)):
            return f"{module} 模块主要导出这些操作：{operations}。"
    return " ".join(citation["quote"] for _, citation in selected)


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
    question: str,
    question_terms: set[str],
    *,
    max_pages: int,
    trace: list[TraceStep],
    repository_id: str | None,
    prefer_index_routes: bool = False,
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
                module_path = _title_module_path(title)
                question_identifiers = {term for term in question_terms if not _CJK.fullmatch(term)}
                extra_module_parts = (
                    len(set(module_path.split("/")) - question_identifiers) if module_path else 0
                )
                scored.append(
                    (
                        (
                            int(module_path is not None),
                            -extra_module_parts if module_path is not None else 0,
                            sum(not _CJK.fullmatch(term) for term in overlap),
                            len(overlap),
                            sum(len(term) for term in overlap),
                        ),
                        page,
                    )
                )
    strict_fts_paths: tuple[str, ...] = ()
    relaxed_fts_paths: tuple[str, ...] = ()
    index_path = workspace_root / ".memoryforge" / "index.sqlite"
    if safe_index is None or (index_path.is_file() and not index_path.is_symlink()):
        strict_fts_paths = find_applied_page_paths(
            workspace_root,
            question,
            limit=max_pages,
            repository_id=repository_id,
        )
        if len(strict_fts_paths) < max_pages:
            relaxed_fts_paths = find_applied_page_paths(
                workspace_root,
                question,
                limit=max_pages,
                repository_id=repository_id,
                require_all_terms=False,
            )
    if strict_fts_paths or relaxed_fts_paths:
        trace.append({"level": "L0", "artifact": "SQLite FTS5 applied-source index"})
    strict_pages = [
        page
        for path in strict_fts_paths
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
    ]
    ranked_index = sorted(
        scored,
        key=lambda candidate: (
            -candidate[0][0],
            -candidate[0][1],
            -candidate[0][2],
            -candidate[0][3],
            -candidate[0][4],
            str(candidate[1].relative_to(workspace_root)),
        ),
    )
    module_pages = [page for score, page in ranked_index if score[0]]
    index_pages = [page for score, page in ranked_index if not score[0]]
    relaxed_pages = [
        page
        for path in relaxed_fts_paths
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
    ]
    ordered_pages = (*module_pages, *strict_pages)
    exact_code_pages = _exact_code_pages(
        workspace_root,
        question,
        max_pages=max_pages,
        repository_id=repository_id,
    )
    ordered_pages = (*exact_code_pages, *ordered_pages)
    if exact_code_pages and any(marker in question for marker in ("方法", "字段", "属性", "函数")):
        return list(exact_code_pages[:max_pages])
    ordered_pages += (
        (*index_pages, *relaxed_pages) if prefer_index_routes else (*relaxed_pages, *index_pages)
    )
    candidates: list[Path] = []
    for page in ordered_pages:
        if page not in candidates:
            candidates.append(page)
    return candidates[:max_pages]


def _exact_code_pages(
    workspace_root: Path,
    question: str,
    *,
    max_pages: int,
    repository_id: str | None,
) -> tuple[Path, ...]:
    """Route explicit CamelCase symbols and code paths to code pages first."""
    if not any(marker in question for marker in ("方法", "字段", "属性", "函数")):
        return ()
    if not (workspace_root / ".memoryforge" / "index.sqlite").is_file():
        return ()
    identifiers = tuple(
        dict.fromkeys(
            match.group()
            for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_./-]*", question)
            if any(character.isupper() for character in match.group())
            or "/" in match.group()
            or "_" in match.group()
            or ".go" in match.group()
            or ".py" in match.group()
        )
    )
    pages: list[Path] = []
    for identifier in identifiers[:3]:
        symbol_pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])")
        for path in find_applied_page_paths(
            workspace_root,
            identifier,
            limit=max_pages,
            repository_id=repository_id,
            require_all_terms=False,
        ):
            page = _safe_wiki_page(workspace_root, workspace_root / path)
            if page is None or not _is_code_file_page(page):
                continue
            if (
                symbol_pattern.search(_code_fact_text(page.read_text(encoding="utf-8")))
                and page not in pages
            ):
                pages.append(page)
        for file_path in sorted((workspace_root / "wiki" / "pages").rglob("*.md")):
            page = _safe_wiki_page(workspace_root, file_path)
            if page is None or not _is_code_file_page(page) or page in pages:
                continue
            if symbol_pattern.search(_code_fact_text(page.read_text(encoding="utf-8"))):
                pages.append(page)
                if len(pages) >= max_pages:
                    break
        if len(pages) >= max_pages:
            break
    return tuple(pages)


def _is_code_page(page: Path) -> bool:
    prefix = page.read_text(encoding="utf-8")[:400]
    return (
        'title: "Code:' in prefix
        or 'title: "Code module:' in prefix
        or "generated: code_wiki" in prefix
        or "generated: code_module_overview" in prefix
    )


def _is_code_file_page(page: Path) -> bool:
    prefix = page.read_text(encoding="utf-8")[:400]
    return (
        'title: "Code: ' in prefix and 'title: "Code module:' not in prefix
    ) or "generated: code_wiki" in prefix


def _code_fact_text(content: str) -> str:
    return "\n".join(line for line in content.splitlines() if line.startswith("- "))


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


def _answer_citation_limit(question: str, max_citations: int) -> int:
    """Give a two-part question room for two complementary source facts."""
    if max_citations == 1 and "方法" in question:
        return 8
    if max_citations == 1 and any(marker in question for marker in ("子模块", "字段", "属性")):
        return 6
    if max_citations == 1 and any(
        marker in question for marker in ("分别", "以及", "、", "，", "什么时候")
    ):
        return 2
    return max_citations


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
                    len(_matching_terms(question_terms, remaining[index][2]) - covered_terms),
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
        covered_terms.update(_matching_terms(question_terms, citation))
    return selected


def _rank_matches(
    matches: list[tuple[frozenset[str], bool, str, CitationPayload]],
    *,
    question_terms: set[str],
    focus_terms: set[str] | None = None,
    prioritize_focus: bool = False,
    prefer_environment_assignments: bool = False,
    prefer_failure_facts: bool = False,
    prefer_code_modules: bool = False,
) -> list[tuple[tuple[int, ...], str, CitationPayload]]:
    focus_terms = focus_terms or set()
    frequencies = Counter(
        term
        for overlap, _summary, _page_path, _citation in matches
        for term in overlap - _RANKING_STOP_WORDS
    )
    ranked = []
    for overlap, summary, page_path, citation in matches:
        ranking_overlap = overlap - _RANKING_STOP_WORDS
        focus_overlap = ranking_overlap & focus_terms
        non_cjk_terms = [term for term in ranking_overlap if not _CJK.fullmatch(term)]
        distinctive_non_cjk_terms = [
            term for term in non_cjk_terms if frequencies[term] <= max(1, len(matches) // 2)
        ]
        direct_overlap = _direct_matching_terms(question_terms, citation)
        module_path = _citation_module_path(citation)
        module_section_score = (
            3
            if "child" in focus_terms and " / Child modules" in citation.get("section_path", "")
            else (
                2
                if " / Responsibilities" in citation.get("section_path", "")
                else int(" / Child modules" in citation.get("section_path", ""))
            )
        )
        question_identifiers = {term for term in question_terms if not _CJK.fullmatch(term)}
        extra_module_parts = (
            len(set(module_path.split("/")) - question_identifiers) if module_path else 0
        )
        score = (
            int(prefer_code_modules and module_path is not None),
            -extra_module_parts if prefer_code_modules and module_path is not None else 0,
            module_section_score if prefer_code_modules else 0,
            int(
                prefer_environment_assignments
                and bool(_ENVIRONMENT_ASSIGNMENT.search(citation["quote"]))
            ),
            int(
                prioritize_focus
                and bool(focus_overlap)
                and not citation["quote"].lstrip().startswith("|")
            ),
            len(focus_overlap) if prioritize_focus else 0,
            int(
                summary
                and not (
                    prefer_environment_assignments or prefer_code_modules or prefer_failure_facts
                )
            ),
            max((len(term) for term in distinctive_non_cjk_terms), default=0),
            len(distinctive_non_cjk_terms),
            int(
                not prioritize_focus
                and bool(focus_overlap)
                and not citation["quote"].lstrip().startswith("|")
            ),
            len(focus_overlap) if not prioritize_focus else 0,
            sum(1000 // frequencies[term] for term in ranking_overlap),
            int(bool(direct_overlap)),
            len(direct_overlap),
            len(ranking_overlap),
            sum(len(term) for term in ranking_overlap),
        )
        ranked.append((score, page_path, citation))
    return sorted(ranked, key=lambda match: (match[0], match[1]), reverse=True)


def _citation_module_path(citation: CitationPayload) -> str | None:
    section = citation.get("section_path", "")
    if not section.startswith("Code module: "):
        return None
    return section.removeprefix("Code module: ").partition(" / ")[0]


def _title_module_path(title: str) -> str | None:
    if not title.lower().startswith("code module: "):
        return None
    return title.partition(": ")[2].lower()


def _yes_no_focus_terms(question: str) -> set[str]:
    """Return the condition half of one compact Chinese yes-or-no question."""
    for match in _WORDS.finditer(question):
        token = match.group().lower()
        if _CJK.fullmatch(token) and token.endswith("吗"):
            # ponytail: only explicit yes/no questions need this.
            # General tail weighting regressed recall.
            return _terms(token[len(token) // 2 :])
    return set()


def _question_focus_terms(question: str) -> set[str]:
    """Treat the suffix after a Chinese possessive as the question's subject.

    Repository names are commonly placed before ``的``. They identify where to
    search, while the suffix identifies which setting or behaviour to answer.
    """
    for match in _WORDS.finditer(question):
        token = match.group()
        if _CJK.fullmatch(token) and "的" in token:
            return _terms(token.rsplit("的", maxsplit=1)[1])
    return set()


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


def _has_many_index_routes(workspace_root: Path) -> bool:
    """Use index-first routing for a compiled Wiki rather than tiny fixtures."""
    index = workspace_root / "wiki" / "INDEX.md"
    if not index.is_file() or index.is_symlink():
        return False
    return len(_INDEX_ENTRY.findall(index.read_text(encoding="utf-8"))) > 3


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
        r"^## (?:Verified facts|Verified symbols)\s*$\n(?P<section>.*?)(?=^## |\Z)",
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
        citation: CitationPayload = {
            "source_id": footnote["source_id"],
            "source_version": int(footnote["source_version"]),
            "locator": footnote["locator"],
            "quote": fact.group("quote"),
        }
        section = next(
            (
                match.group("section")
                for match in reversed(list(_FACT_SECTION.finditer(section_match.group("section"))))
                if match.start() <= fact.start()
            ),
            "",
        )
        if section:
            citation["section_path"] = section
        citations.append(citation)
    return citations


def _citation_terms(citation: CitationPayload) -> set[str]:
    return _terms(f"{citation.get('section_path', '')} {citation['quote']}")


def _matching_terms(question_terms: set[str], citation: CitationPayload) -> set[str]:
    # Keep ranking grounded in fact text instead of treating a section heading
    # as if it were an answer.
    return (question_terms & _terms(citation["quote"])) - _QUESTION_NOISE_TERMS


def _section_matching_terms(question_terms: set[str], citation: CitationPayload) -> set[str]:
    """Use headings for the few queries whose answer is defined by a section route."""
    return (question_terms & _citation_terms(citation)) - _QUESTION_NOISE_TERMS


def _direct_matching_terms(question_terms: set[str], citation: CitationPayload) -> set[str]:
    return (question_terms & _terms(citation["quote"])) - _QUESTION_NOISE_TERMS


def _has_direct_evidence(question_terms: set[str], citation: CitationPayload) -> bool:
    """Keep heading-only routes out of the model context.

    A heading is useful to find a fact whose wording differs from the question,
    but it is not evidence by itself. The model may see a weak candidate only
    when the fact text itself shares a term with the question.
    """
    direct_overlap = _direct_matching_terms(question_terms, citation)
    if any(_CJK.fullmatch(term) for term in question_terms):
        return (
            any(_CJK.fullmatch(term) for term in direct_overlap)
            or (
                bool(_CODE_FACT.match(citation["quote"]))
                and any(not _CJK.fullmatch(term) for term in direct_overlap)
            )
            or (
                citation.get("section_path", "").startswith("Code module:")
                and any(
                    not _CJK.fullmatch(term) for term in _matching_terms(question_terms, citation)
                )
            )
        )
    return bool(direct_overlap)


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
        raw_token = match.group()
        token = raw_token.lower()
        if _CJK.fullmatch(token):
            if token in _STOP_WORDS:
                continue
            if len(token) == 1:
                terms.add(token)
            else:
                terms.update(token[index : index + 2] for index in range(len(token) - 1))
        elif token not in _STOP_WORDS:
            terms.add(token)
            parts = _CAMEL_CASE_PARTS.findall(raw_token)
            if len(parts) > 1:
                terms.update("".join(parts[index:]).lower() for index in range(1, len(parts) - 1))
    return terms


def _expanded_question_terms(question_terms: set[str]) -> set[str]:
    expanded = set(question_terms)
    for term, expansions in _CODE_QUERY_EXPANSIONS.items():
        if term in question_terms:
            expanded.update(expansions)
    if {"不可", "可用"} & question_terms:
        expanded.update(_FAILURE_TERM_EXPANSIONS)
    return expanded
