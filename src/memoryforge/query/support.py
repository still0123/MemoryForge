"""Lexical matching and evidence support scoring for Wiki queries."""

from __future__ import annotations

import re
from pathlib import Path

from memoryforge.compiler.wiki_facts import (
    AppliedCodeSymbolMatch,
    CitationPayload,
    is_conversation_process_note,
)
from memoryforge.query.contracts import SupportComponents, SupportPayload
from memoryforge.storage.database import connect_readonly as _connect_readonly
from memoryforge.storage.workspace import DATABASE_RELATIVE_PATH

_WORDS = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_CAMEL_CASE_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+")
_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_EXPLICIT_CODE_IDENTIFIER = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*"
)
_NEGATION_CUES = ("不", "无", "未", "没", "避免", "拒绝")
_ENGLISH_NEGATION = re.compile(
    r"\b(?:cannot|can['’]t|do(?:es)?n['’]t|didn['’]t|isn['’]t|aren['’]t|"
    r"wasn['’]t|weren['’]t|won['’]t|hasn['’]t|haven['’]t|hadn['’]t|"
    r"shouldn['’]t|wouldn['’]t|couldn['’]t|mustn['’]t)\b",
    re.IGNORECASE,
)
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
_CAPABILITY_MARKERS = ("支持", "提供", "包括", "用于", "负责")
_CLEANUP_RESULT_MARKERS = ("一旦", "不会", "不是", "自动", "skip")
_CLEANUP_OBJECT_MARKERS = ("清理", "删除", "delete", "skip")
_CODE_QUERY_EXPANSIONS = {
    "连接": {"登录", "链路"},
    "模块": {"module", "modules"},
    "子模": {"child", "children"},
    "入口": {"entry", "entries", "point", "points", "handler", "handlers"},
    "依赖": {"depend", "dependency", "dependencies", "import", "imports"},
    "操作": {"operation", "operations"},
    "职责": {"responsibility", "responsibilities"},
    "方法": {"method", "methods", "func", "function", "functions"},
    "字段": {"field", "fields", "struct", "attribute", "attributes"},
}
_CODE_FACT = re.compile(r"^(?:package|type|func|class|def)\b")
_SUPPORT_THRESHOLD = 75.0
_SUPPORT_CODE_KIND_TERMS = {
    "class",
    "constant",
    "function",
    "interface",
    "method",
    "module",
    "package",
    "struct",
    "type",
}


def _explicit_code_identifiers(question: str) -> tuple[str, ...]:
    return _all_explicit_code_identifiers(question)[:8]


def _all_explicit_code_identifiers(question: str) -> tuple[str, ...]:
    backticked = {
        match.group("identifier")
        for match in re.finditer(
            r"`(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)`",
            question,
        )
    }
    identifiers = []
    for match in _EXPLICIT_CODE_IDENTIFIER.finditer(question):
        identifier = match.group()
        if (
            identifier in backticked
            or "." in identifier
            or "_" in identifier
            or "$" in identifier
            or (
                any(character.islower() for character in identifier)
                and any(character.isupper() for character in identifier[1:])
            )
        ):
            identifiers.append(identifier)
    return tuple(dict.fromkeys(identifiers))


def _support_identifiers(question: str) -> tuple[str, ...]:
    return tuple(
        identifier
        for identifier in _all_explicit_code_identifiers(question)
        if re.search(
            rf"\bin\s+`?{re.escape(identifier)}`?(?:\W|$)",
            question,
            re.IGNORECASE,
        )
        is None
        and re.search(rf"`?{re.escape(identifier)}`?\s*(?:中|内)", question) is None
    )


def _citation_fact_key(
    page_path: str,
    citation: CitationPayload | AppliedCodeSymbolMatch,
) -> tuple[str, str, int, str, str]:
    return (
        page_path,
        citation["source_id"] if isinstance(citation, dict) else citation.source_id,
        citation["source_version"] if isinstance(citation, dict) else citation.source_version,
        citation["locator"] if isinstance(citation, dict) else citation.locator,
        citation["quote"] if isinstance(citation, dict) else citation.quote,
    )


def _support_score(
    workspace_root: Path,
    question: str,
    question_terms: set[str],
    selected: list[tuple[str, CitationPayload]],
    *,
    symbol_matches: tuple[AppliedCodeSymbolMatch, ...],
    exact_symbol_fact_keys: set[tuple[str, str, int, str, str]],
    required_sources: int,
    code_page_paths: set[str],
    code_page_identifiers: dict[str, set[str]] | None = None,
) -> SupportPayload:
    core_terms = (
        question_terms - _SUPPORT_CODE_KIND_TERMS - _RANKING_STOP_WORDS - _QUESTION_NOISE_TERMS
    )
    code_anchor_terms = {term for term in core_terms if not _CJK.fullmatch(term)}
    if len(code_anchor_terms) >= 2 and any(
        page_path in code_page_paths for page_path, _ in selected
    ):
        core_terms = code_anchor_terms
    explicit_identifiers = _support_identifiers(question)
    selected_page_paths = {page_path for page_path, _ in selected}
    selected_are_fields = bool(selected) and all(
        citation["quote"].startswith("Field ") for _, citation in selected
    )
    page_symbol_identifiers = (
        {match.identifier for match in symbol_matches if match.page_path in selected_page_paths}
        | {
            identifier
            for page_path in selected_page_paths
            for identifier in (code_page_identifiers or {}).get(page_path, set())
        }
        if selected_are_fields
        else set()
    )
    covered_terms: set[str] = set()
    selected_identifiers: set[str] = set()
    per_fact_matches: list[set[str]] = []
    for page_path, citation in selected:
        selected_identifiers.update(
            _code_identifier_tokens(
                f"{citation.get('section_path', '')} "
                f"{citation.get('routing_text', '')} {citation['quote']}"
            )
        )
        matching = _local_english_matching_terms(
            core_terms,
            citation,
            include_section=page_path in code_page_paths,
            enabled=True,
        )
        covered_terms.update(matching)
        per_fact_matches.append(matching)
    identifier_terms = {term for identifier in explicit_identifiers for term in _terms(identifier)}
    page_symbol_terms = {
        term for identifier in page_symbol_identifiers for term in _terms(identifier)
    }
    covered_terms.update(core_terms & identifier_terms & page_symbol_terms)
    core_coverage = len(covered_terms) / len(core_terms) if core_terms else 1.0

    selected_fact_keys = {
        _citation_fact_key(page_path, citation) for page_path, citation in selected
    }
    selected_identifiers.update(
        match.identifier
        for match in symbol_matches
        if _citation_fact_key(match.page_path, match) in selected_fact_keys
    )
    selected_identifiers.update(page_symbol_identifiers)
    covered_identifier_count = sum(
        any(
            identifier == candidate or identifier in candidate.split(".")
            for candidate in selected_identifiers
        )
        for identifier in explicit_identifiers
    )
    exact_identifier_coverage = (
        covered_identifier_count / len(explicit_identifiers) if explicit_identifiers else 1.0
    )
    capability_summary = (
        len(selected) == 1
        and selected[0][1].get("is_summary", False)
        and any(marker in question for marker in ("功能", "能力", "做什么", "用途"))
        and any(marker in selected[0][1]["quote"] for marker in _CAPABILITY_MARKERS)
        and exact_identifier_coverage == 1.0
    )
    if capability_summary:
        core_coverage = max(core_coverage, 0.75)
    conditional = _has_support_condition(question)
    if conditional:
        co_location_terms = core_terms - identifier_terms
        fact_co_location = float(
            not co_location_terms
            or any(co_location_terms <= matching for matching in per_fact_matches)
        )
    else:
        fact_co_location = float(
            not core_terms
            or max((len(matching) for matching in per_fact_matches), default=0)
            >= min(2, len(core_terms))
        )
    if capability_summary:
        fact_co_location = 1.0
    if "失败后" in question and any(
        marker in citation["quote"]
        for _, citation in selected
        for marker in ("一旦", "失败后", "失败时")
    ):
        fact_co_location = 1.0
    question_has_negation = _has_support_negation(question)
    negation_alignment = float(
        not question_has_negation
        or any(_has_support_negation(citation["quote"]) for _, citation in selected)
    )
    if "清理" in question and any("skip" in citation["quote"] for _, citation in selected):
        negation_alignment = 1.0
    citation_sources = {
        (citation["source_id"], citation["source_version"]) for _, citation in selected
    }
    multi_source_coverage = (
        min(1.0, len(citation_sources) / required_sources) if required_sources > 1 else 1.0
    )
    real_workspace = (workspace_root / "raw").is_dir() and (
        workspace_root / ".memoryforge" / "index.sqlite"
    ).is_file()
    current_source_versions = 1.0
    if real_workspace:
        database = workspace_root / DATABASE_RELATIVE_PATH
        source_versions = {
            (citation["source_id"], citation["source_version"]) for _, citation in selected
        }
        with _connect_readonly(database) as connection:
            current_source_versions = float(
                all(
                    connection.execute(
                        "SELECT 1 FROM applied_source_versions "
                        "WHERE source_id = ? AND source_version_id = ?",
                        source_version,
                    ).fetchone()
                    is not None
                    for source_version in source_versions
                )
            )
    components: SupportComponents = {
        "exact_identifier_coverage": round(exact_identifier_coverage, 4),
        "core_term_coverage": round(core_coverage, 4),
        "fact_co_location": fact_co_location,
        "negation_alignment": negation_alignment,
        "multi_source_coverage": round(multi_source_coverage, 4),
        "current_source_versions": current_source_versions,
    }
    score = round(
        100
        * (
            0.20 * exact_identifier_coverage
            + 0.35 * core_coverage
            + 0.15 * fact_co_location
            + 0.10 * negation_alignment
            + 0.10 * multi_source_coverage
            + 0.10 * current_source_versions
        ),
        1,
    )
    code_enforced = any(page_path in code_page_paths for page_path, _ in selected)
    enforced = code_enforced or any(
        "assistant conclusions" in citation.get("section_path", "").lower()
        or "assistant message" in citation.get("section_path", "").lower()
        for _, citation in selected
    )
    failed_hard_gates = []
    if code_enforced and explicit_identifiers and exact_identifier_coverage < 1:
        failed_hard_gates.append("exact_identifier_not_covered")
    if enforced and score < _SUPPORT_THRESHOLD:
        failed_hard_gates.append("score_below_threshold")
    if code_enforced:
        if conditional and not fact_co_location:
            failed_hard_gates.append("condition_not_co_located")
        if question_has_negation and not negation_alignment:
            failed_hard_gates.append("negation_not_aligned")
        if required_sources > 1 and multi_source_coverage < 1:
            failed_hard_gates.append("multi_source_incomplete")
        if not current_source_versions:
            failed_hard_gates.append("citation_not_current")
    return {
        "score": score,
        "threshold": _SUPPORT_THRESHOLD,
        "sufficient": not failed_hard_gates,
        "enforced": enforced,
        "components": components,
        "failed_hard_gates": failed_hard_gates,
    }


def _has_support_negation(text: str) -> bool:
    terms = set(re.findall(r"[a-z]+", text.lower()))
    return (
        bool(terms & {"never", "no", "not", "without"})
        or _ENGLISH_NEGATION.search(text) is not None
        or any(cue in text for cue in _NEGATION_CUES)
    )


def _has_support_condition(text: str) -> bool:
    terms = set(re.findall(r"[a-z]+", text.lower()))
    return bool(terms & {"after", "before", "if", "unless", "when", "without"}) or any(
        marker in text for marker in ("当", "如果", "条件", "没有")
    )


def _code_identifier_tokens(text: str) -> set[str]:
    return {match.group() for match in _EXPLICIT_CODE_IDENTIFIER.finditer(text)}


def _citation_terms(citation: CitationPayload) -> set[str]:
    return _terms(
        f"{citation.get('section_path', '')} {citation.get('routing_text', '')} {citation['quote']}"
    )


def _is_conversation_search_clue(citation: CitationPayload) -> bool:
    section = citation.get("section_path", "").lower()
    return (
        "user prompts (search only)" in section
        or "user message" in section
        or is_conversation_process_note(citation["quote"])
    )


def answer_is_supported(answer: str, citations: list[CitationPayload]) -> bool:
    """Return whether every answer clause is supported by one cited Fact."""
    clauses = [
        clause.strip()
        for clause in re.split(
            r"[.!?。！？;\n]+",
            answer,
            flags=re.IGNORECASE,
        )
        if clause.strip()
    ]
    return bool(clauses) and all(
        any(_answer_clause_is_supported(clause, citation["quote"]) for citation in citations)
        for clause in clauses
    )


def _answer_clause_is_supported(clause: str, evidence: str) -> bool:
    normalised_clause = _normalise_support_text(clause)
    return bool(normalised_clause) and any(
        normalised_clause == _normalise_support_text(segment)
        and _has_support_negation(clause) == _has_support_negation(segment)
        for segment in (
            evidence,
            *re.split(
                r"[.!?。！？;,\n，]+|\b(?:and|or|but|while|whereas)\b|以及|并且|但是|而",
                evidence,
                flags=re.IGNORECASE,
            ),
        )
    )


def _normalise_support_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t.!?。！？;,，").casefold()


def _matching_terms(question_terms: set[str], citation: CitationPayload) -> set[str]:
    return (
        question_terms & _terms(f"{citation.get('routing_text', '')} {citation['quote']}")
    ) - _QUESTION_NOISE_TERMS


def _section_matching_terms(question_terms: set[str], citation: CitationPayload) -> set[str]:
    return (question_terms & _citation_terms(citation)) - _QUESTION_NOISE_TERMS


def _direct_matching_terms(question_terms: set[str], citation: CitationPayload) -> set[str]:
    return (
        question_terms & _terms(f"{citation.get('routing_text', '')} {citation['quote']}")
    ) - _QUESTION_NOISE_TERMS


def _local_english_matching_terms(
    question_terms: set[str],
    citation: CitationPayload,
    *,
    include_section: bool = False,
    enabled: bool,
) -> set[str]:
    citation_terms = (
        _citation_terms(citation)
        if include_section
        else _terms(f"{citation.get('routing_text', '')} {citation['quote']}")
    )
    exact = (question_terms & citation_terms) - _QUESTION_NOISE_TERMS
    if not enabled:
        return exact
    citation_forms = {form for term in citation_terms for form in _local_english_forms(term)}
    return exact | {
        term
        for term in question_terms - _QUESTION_NOISE_TERMS
        if _local_english_forms(term) & citation_forms
    }


def _local_english_forms(term: str) -> set[str]:
    if not term.isascii() or not term.isalpha():
        return {term}
    forms = {term}
    if len(term) > 4 and term.endswith("ies"):
        forms.add(f"{term[:-3]}y")
    elif len(term) > 4 and term.endswith("es"):
        forms.update((term[:-1], term[:-2]))
    elif len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        forms.add(term[:-1])
    if len(term) > 4 and term.endswith("ed"):
        forms.update((term[:-2], term[:-1]))
    if len(term) > 5 and term.endswith("ing"):
        stem = term[:-3]
        forms.update((stem, f"{stem}e"))
        if len(stem) > 2 and stem[-1] == stem[-2]:
            forms.add(stem[:-1])
    return forms


def _has_direct_evidence(question_terms: set[str], citation: CitationPayload) -> bool:
    """Keep heading-only routes out of the model context."""
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
