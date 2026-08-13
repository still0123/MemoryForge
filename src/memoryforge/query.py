"""Deterministic progressive queries over applied Wiki pages."""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import uuid
from collections import Counter
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from memoryforge.egress_models import EgressRequest
from memoryforge.egress_policy import decide_egress, filter_visible_sources, record_disclosure
from memoryforge.egress_policy import rule_sha256 as _egress_rule_sha256
from memoryforge.freshness import FreshnessState, page_freshness
from memoryforge.provider import OpenAICompatibleProvider, ProviderUnavailableError
from memoryforge.redaction import redact_for_model
from memoryforge.wiki_facts import (
    AppliedCodeSymbolMatch,
    CitationPayload,
    is_conversation_process_note,
)
from memoryforge.wiki_facts import parse_page_citations as _page_citations
from memoryforge.workspace import (
    DATABASE_RELATIVE_PATH,
    _connect,
    find_applied_code_symbol_facts,
    find_applied_page_paths,
    find_applied_wiki_fact_page_paths,
    is_applied_source_version,
    is_public_source_version,
    read_source_excerpt,
    repository_page_paths,
)

if TYPE_CHECKING:
    from memoryforge.retrieval_models import RetrievalResult
    from memoryforge.retrieval_v2 import retrieve_candidates as _retrieve_candidates_fn
    from memoryforge.semantic_index import SemanticIndex

_INDEX_ENTRY = re.compile(
    r"^- \[(?P<title>(?:\\.|[^\]])+)\]\((?P<path>[^)]+)\) — (?P<summary>.+)$",
    re.MULTILINE,
)
_PAGE_TITLE = re.compile(r"^title: (?P<title>.+)$", re.MULTILINE)
_WORDS = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_CAMEL_CASE_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+")
_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_EXPLICIT_CODE_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*")
_SYMBOL_FACT_KIND = re.compile(r"^`[^`]+` \((?P<kind>[a-z_]+)\):")
_REPOSITORY_OVERVIEW_LINK = re.compile(r"^pages/repository-[a-f0-9]{12}\.md$")
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
_ENVIRONMENT_ASSIGNMENT = re.compile(r"\b(?:export\s+)?[A-Z][A-Z0-9_]{2,}=")
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


class EvidencePayload(CitationPayload):
    text: str


class SupportComponents(TypedDict):
    exact_identifier_coverage: float
    core_term_coverage: float
    fact_co_location: float
    negation_alignment: float
    multi_source_coverage: float
    current_source_versions: float


class SupportPayload(TypedDict):
    score: float
    threshold: float
    sufficient: bool
    enforced: bool
    components: SupportComponents
    failed_hard_gates: list[str]


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
    support: NotRequired[SupportPayload]
    _retrieval_debug: NotRequired[dict[str, Any]]


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
    min_source_count: int = 1,
    provider: OpenAICompatibleProvider | None = None,
    allow_local: bool = False,
    public_only: bool = False,
    repository_id: str | None = None,
    conversation_context: str = "",
) -> AskPayload:
    """Answer from a bounded set of Wiki pages, expanding raw evidence only on request.

    ``public_only`` applies the sensitivity gate to the non-LLM path (and to
    code Symbol matches) *before* Support scoring and answer assembly, so
    ``Sensitivity.LOCAL_ONLY`` facts can never influence page selection,
    Support or the answer. The CLI keeps its current behavior by default.
    """
    _validate_max_pages(max_pages)
    _validate_max_citations(max_citations)
    _validate_min_source_count(min_source_count)
    base_question_terms = _terms(question)
    question_terms = _expanded_question_terms(base_question_terms)
    identifier_terms = {term for term in base_question_terms if not _CJK.fullmatch(term)}
    definition_question = _is_definition_question(question)
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
    prefer_code_assignments = any(marker in question for marker in ("换算", "计算", "转换", "转成"))
    prefer_failure_facts = bool({"不可", "可用", "失败", "超时"} & base_question_terms)
    use_section_routes = (
        prefer_environment_assignments
        or prefer_failure_facts
        or any(marker in question for marker in ("子模块", "字段", "属性", "方法", "作用"))
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
    prefer_capability_facts = any(
        marker in question for marker in ("核心功能", "功能", "能力", "做什么", "用途")
    )
    prefer_cleanup_conclusion = "清理" in question and any(
        marker in question for marker in ("自动", "失败后")
    )
    trace: list[TraceStep] = []
    if not question_terms:
        return _unknown_payload(debug, trace)
    symbol_matches = _applied_code_symbol_matches(
        workspace_root,
        question,
        repository_id=repository_id,
    )
    if public_only:
        symbol_matches = tuple(
            match
            for match in symbol_matches
            if is_public_source_version(
                workspace_root,
                source_id=match.source_id,
                source_version=match.source_version,
            )
        )
    exact_symbol_fact_keys = {
        _citation_fact_key(match.page_path, match) for match in symbol_matches
    }
    exact_symbol_page_paths = tuple(dict.fromkeys(match.page_path for match in symbol_matches))
    if symbol_matches:
        trace.append(
            {
                "level": "L0",
                "artifact": (
                    "Applied Code Index Symbol projection: "
                    + ", ".join(dict.fromkeys(match.identifier for match in symbol_matches))
                ),
            }
        )

    retrieval_debug: dict[str, Any] = {}
    egress_request: EgressRequest | None = None
    try:
        safe_repo = repository_id if repository_id else "default"
        egress_request = EgressRequest(
            request_id=str(uuid.uuid4()),
            host_id="local-cli",
            repository_id=safe_repo,
            purpose="context",
            max_characters=32000,
        )
    except Exception as exc:  # noqa: BLE001
        logging.debug("egress request build skipped: %s", exc)

    retrieval_v2_result: "RetrievalResult | None" = None
    if egress_request is not None and repository_id is not None:
        try:
            from memoryforge.retrieval_v2 import retrieve_candidates
            from memoryforge.retrieval_models import VisibleSource

            def _visible_source(source_id: str, source_version: int) -> bool:
                if public_only:
                    return is_public_source_version(
                        workspace_root, source_id=source_id, source_version=source_version
                    )
                if allow_local:
                    return True
                return is_public_source_version(
                    workspace_root, source_id=source_id, source_version=source_version
                )

            visible: VisibleSource = _visible_source
            applied_wiki_facts_list: list[dict[str, Any]] = []
            code_index_snapshot_symbols: list[dict[str, Any]] = []
            code_index_snapshot_relations: list[dict[str, Any]] = []
            try:
                retrieval_v2_result = retrieve_candidates(
                    workspace_root,
                    question,
                    repository_id=repository_id,
                    visible_source=visible,
                    max_pages=max_pages,
                    semantic_index=None,
                    wiki_facts=applied_wiki_facts_list,
                    code_symbols=code_index_snapshot_symbols,
                    code_relations=code_index_snapshot_relations,
                )
                retrieval_debug["routes"] = list(retrieval_v2_result.routes)
                retrieval_debug["semantic_status"] = retrieval_v2_result.semantic_status
            except Exception as exc:  # noqa: BLE001
                logging.debug("retrieval_v2 unavailable, fallback to legacy: %s", exc)
                retrieval_v2_result = None
        except Exception as exc:  # noqa: BLE001
            logging.debug("retrieval_v2 import failed: %s", exc)
            retrieval_v2_result = None

    stale_page_penalty: dict[str, float] = {}

    raw_matches: list[tuple[frozenset[str], bool, str, CitationPayload]] = []
    raw_candidate_matches: list[tuple[frozenset[str], bool, str, CitationPayload]] = []
    page_ranks: dict[str, int] = {}
    local_morphology_pages: set[str] = set()
    code_page_paths: set[str] = set()
    conversation_page_paths: set[str] = set()
    code_page_identifiers: dict[str, set[str]] = {}

    for page_rank, page in enumerate(
        _candidate_pages(
            workspace_root,
            question,
            question_terms,
            max_pages=max_pages,
            trace=trace,
            repository_id=repository_id,
            prefer_index_routes=max_citations > 1 or _has_many_index_routes(workspace_root),
            exact_symbol_page_paths=exact_symbol_page_paths,
        )
    ):
        content = page.read_text(encoding="utf-8")
        page_path = str(page.relative_to(workspace_root))
        page_ranks[page_path] = page_rank
        prefix = content[:400]
        frontmatter_end = content.find("\n---\n", 4)
        frontmatter = content[: frontmatter_end + 5] if frontmatter_end >= 0 else prefix
        code_page = (
            'title: "Code:' in prefix
            or 'title: "Code module:' in prefix
            or "generated: code_wiki" in prefix
            or "generated: code_module_overview" in prefix
        )
        if '"conversation"' in frontmatter:
            conversation_page_paths.add(page_path)
        title_match = _PAGE_TITLE.search(prefix)
        conversation_title = ""
        if page_path in conversation_page_paths and title_match is not None:
            with suppress(json.JSONDecodeError):
                parsed_title = json.loads(title_match.group("title"))
                if isinstance(parsed_title, str):
                    conversation_title = parsed_title
        if code_page and _is_code_file_content(content):
            code_page_paths.add(page_path)
            code_page_identifiers[page_path] = _code_identifier_tokens(_code_fact_text(content))
        if not any(_CJK.fullmatch(term) for term in question_terms) and not code_page:
            local_morphology_pages.add(page_path)
        trace.append({"level": "L1", "artifact": page_path})
        for citation in _page_citations(content):
            if _is_conversation_search_clue(citation):
                continue
            if (
                prefer_cleanup_conclusion
                and page_path in conversation_page_paths
                and not (
                    any(marker in citation["quote"] for marker in _CLEANUP_RESULT_MARKERS)
                    and any(marker in citation["quote"] for marker in _CLEANUP_OBJECT_MARKERS)
                )
            ):
                continue
            if conversation_title:
                citation = {
                    **citation,
                    "routing_text": " ".join(
                        filter(None, (citation.get("routing_text", ""), conversation_title))
                    ),
                }
            exact_overlap = (
                _section_matching_terms(question_terms, citation)
                if use_section_routes
                else _matching_terms(question_terms, citation)
            )
            overlap = _local_english_matching_terms(
                question_terms,
                citation,
                include_section=use_section_routes,
                enabled=page_path in local_morphology_pages,
            )
            is_summary = citation.get("is_summary", False)
            raw_candidate_matches.append((frozenset(overlap), is_summary, page_path, citation))
            has_cjk_terms = any(_CJK.fullmatch(term) for term in question_terms)
            required_overlap = 1 if len(question_terms) == 1 else 2
            if has_cjk_terms:
                required_overlap = min(3, len(question_terms))
                aligned_negation = any(cue in question for cue in _NEGATION_CUES) and any(
                    cue in citation["quote"] for cue in _NEGATION_CUES
                )
                if page_rank == 0 and is_summary and aligned_negation:
                    required_overlap = min(required_overlap, 2)
                if len(overlap) >= 2 and any(not _CJK.fullmatch(term) for term in overlap):
                    required_overlap = 2
            sufficient_match = len(overlap) >= required_overlap
            if page_path in local_morphology_pages and overlap - exact_overlap:
                sufficient_match = True
            if definition_question and overlap & identifier_terms:
                sufficient_match = True
            if "字段" in base_question_terms and overlap & focus_terms:
                sufficient_match = True
            if "方法" in base_question_terms and overlap & identifier_terms:
                sufficient_match = True
            if (
                code_page
                and page_rank == 0
                and len(identifier_terms) >= 2
                and overlap & identifier_terms
            ):
                sufficient_match = True
            if (
                prefer_capability_facts
                and page_path in conversation_page_paths
                and is_summary
                and overlap & identifier_terms
                and any(marker in citation["quote"] for marker in _CAPABILITY_MARKERS)
            ):
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
            if _citation_fact_key(page_path, citation) in exact_symbol_fact_keys:
                sufficient_match = True
            if sufficient_match:
                raw_matches.append((frozenset(overlap), is_summary, page_path, citation))

    if "方法" in base_question_terms and identifier_terms:
        method_symbol = max(identifier_terms, key=len)
        raw_matches = [match for match in raw_matches if method_symbol in _citation_terms(match[3])]
        raw_candidate_matches = [
            match for match in raw_candidate_matches if method_symbol in _citation_terms(match[3])
        ]

    matches = _rank_matches(
        raw_matches,
        question_terms=question_terms,
        page_ranks=page_ranks,
        local_morphology_pages=local_morphology_pages,
        focus_terms=focus_terms,
        prioritize_focus=bool(yes_no_focus_terms and {"依赖", "外部"} <= base_question_terms),
        prefer_environment_assignments=prefer_environment_assignments,
        prefer_code_assignments=prefer_code_assignments,
        prefer_failure_facts=prefer_failure_facts,
        prefer_code_modules=prefer_code_modules,
        exact_symbol_fact_keys=exact_symbol_fact_keys,
        conversation_page_paths=conversation_page_paths,
        prefer_capability_facts=prefer_capability_facts,
        prefer_cleanup_conclusion=prefer_cleanup_conclusion,
        prefer_exact_identifiers=identifier_terms,
    )
    candidate_matches = _rank_matches(
        raw_candidate_matches,
        question_terms=question_terms,
        page_ranks=page_ranks,
        local_morphology_pages=local_morphology_pages,
        focus_terms=focus_terms,
        prioritize_focus=bool(yes_no_focus_terms and {"依赖", "外部"} <= base_question_terms),
        prefer_environment_assignments=prefer_environment_assignments,
        prefer_code_assignments=prefer_code_assignments,
        prefer_failure_facts=prefer_failure_facts,
        prefer_code_modules=prefer_code_modules,
        exact_symbol_fact_keys=exact_symbol_fact_keys,
        conversation_page_paths=conversation_page_paths,
        prefer_capability_facts=prefer_capability_facts,
        prefer_cleanup_conclusion=prefer_cleanup_conclusion,
        prefer_exact_identifiers=identifier_terms,
    )
    model_candidates = [
        match for match in candidate_matches if _has_direct_evidence(question_terms, match[2])
    ]

    if not matches and (provider is None or not model_candidates):
        return _unknown_payload(debug, trace)

    model_status: Literal["used", "fallback"] | None = None
    if provider is None:
        if public_only:
            matches = _usable_matches(workspace_root, matches, allow_local=False)
            if not matches:
                return _unknown_payload(debug, trace)
        selected = _top_matches(
            matches,
            answer_citation_limit,
            question_terms=question_terms,
            required_sources=min_source_count,
        )
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
                egress_request=egress_request,
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
                required_sources=min_source_count,
            )
            answer = _fallback_answer(question, selected)
            model_status = "fallback"
        else:
            if generated is None:
                return _unknown_payload(debug, trace)
            answer, selected = generated
            model_status = "used"

    page_freshness_warnings: list[str] = []
    selected_pages = list(dict.fromkeys(page_path for page_path, _ in selected))
    applied_map: dict[str, int] = {}
    current_map: dict[str, int] = {}
    try:
        db_path = workspace_root / DATABASE_RELATIVE_PATH
        if db_path.is_file():
            with _connect(db_path) as conn:
                for sid, svid in conn.execute(
                    "SELECT source_id, source_version_id FROM applied_source_versions"
                ).fetchall():
                    applied_map[str(sid)] = int(svid)
                for row in conn.execute(
                    "SELECT s.source_id, v.id FROM sources AS s "
                    "JOIN source_versions AS v ON v.source_id = s.id WHERE v.is_current = 1"
                ).fetchall():
                    current_map[str(row[0])] = int(row[1])
            base_commit = ""
            cur_commit = ""
            try:
                from memoryforge.workspace import Workspace
                ws = Workspace(workspace_root)
                try:
                    cur_commit = ws.current_commit()
                    base_commit = cur_commit
                except Exception:
                    base_commit = cur_commit = ""
            except Exception:
                base_commit = cur_commit = ""
            filtered_selected: list[tuple[str, CitationPayload]] = []
            for page_path, citation in selected:
                try:
                    report = page_freshness(
                        workspace_root,
                        page_path,
                        repository_id=repository_id,
                        applied_source_versions=applied_map,
                        current_source_versions=current_map,
                        workspace_base_commit=base_commit,
                        workspace_current_commit=cur_commit,
                        open_conflicts=(),
                        claims=(),
                    )
                    if report.state in (FreshnessState.CONFLICTED, FreshnessState.SUPERSEDED):
                        stale_page_penalty[page_path] = 1.0
                        page_freshness_warnings.append(
                            f"{page_path}:{report.state.value} dropped"
                        )
                        continue
                    if report.state == FreshnessState.STALE:
                        stale_page_penalty[page_path] = 0.5
                        page_freshness_warnings.append(
                            f"{page_path}:stale support_score -0.5"
                        )
                except Exception as exc:  # noqa: BLE001
                    logging.debug("page_freshness skipped for %s: %s", page_path, exc)
                filtered_selected.append((page_path, citation))
            selected = filtered_selected or selected
        if stale_page_penalty:
            retrieval_debug["freshness_warnings"] = list(page_freshness_warnings)
    except Exception as exc:  # noqa: BLE001
        logging.debug("freshness integration skipped: %s", exc)

    if not selected:
        return _unknown_payload(debug, trace)

    support = _support_score(
        workspace_root,
        question,
        question_terms,
        selected,
        symbol_matches=symbol_matches,
        exact_symbol_fact_keys=exact_symbol_fact_keys,
        required_sources=min_source_count,
        code_page_paths=code_page_paths,
        code_page_identifiers=code_page_identifiers,
    )
    if stale_page_penalty:
        total_penalty = min(sum(stale_page_penalty.values()), support["score"])
        support["score"] = max(0.0, support["score"] - total_penalty)
        support["sufficient"] = support["score"] >= support["threshold"]
        if "stale_sources" not in support["failed_hard_gates"] and total_penalty > 0:
            support["failed_hard_gates"].append("stale_sources")
    if not support["sufficient"]:
        return _unknown_payload(debug, trace, support=support)

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
        "support": support,
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
        if retrieval_debug:
            result["_retrieval_debug"] = retrieval_debug
    return result


def _model_answer(
    workspace_root: Path,
    question: str,
    matches: list[tuple[tuple[int, ...], str, CitationPayload]],
    provider: OpenAICompatibleProvider,
    *,
    allow_local: bool,
    conversation_context: str,
    egress_request: EgressRequest | None = None,
) -> tuple[str, list[tuple[str, CitationPayload]]] | None:
    usable_matches = [
        (page_path, citation)
        for _, page_path, citation in _usable_matches(
            workspace_root, matches, allow_local=allow_local
        )
    ][:12]
    if not usable_matches:
        raise ValueError("LLM answers require public source evidence")

    redacted_matches: list[tuple[str, CitationPayload]] = []
    last_redaction = None
    source_refs: list[tuple[str, int]] = []
    for page_path, citation in usable_matches:
        redacted_citation = dict(citation)
        try:
            redaction_result = redact_for_model(citation["quote"])
            last_redaction = redaction_result
            redacted_citation["quote"] = redaction_result.redacted_text
        except Exception as exc:  # noqa: BLE001
            logging.debug("redact_for_model skipped: %s", exc)
        redacted_matches.append((page_path, redacted_citation))  # type: ignore[arg-type]
        source_refs.append((str(citation["source_id"]), int(citation["source_version"])))

    try:
        if egress_request is not None and last_redaction is not None:
            db_path = workspace_root / DATABASE_RELATIVE_PATH
            if db_path.is_file():
                from memoryforge.models import Sensitivity
                policy_sha = "0" * 64
                with _connect(db_path) as conn:
                    all_allowed = True
                    for source_id, source_version in source_refs:
                        try:
                            sensitivity = Sensitivity.PUBLIC
                            row = conn.execute(
                                "SELECT sensitivity FROM source_versions WHERE id = ?",
                                (source_version,),
                            ).fetchone()
                            if row is not None:
                                sensitivity = Sensitivity(str(row[0]))
                            decision = decide_egress(
                                conn,
                                request=egress_request,
                                source_id=source_id,
                                source_version=source_version,
                                sensitivity=sensitivity,
                            )
                            if not decision.allowed:
                                all_allowed = False
                                break
                        except Exception:
                            continue
                    if all_allowed and egress_request.purpose in ("context", "evidence"):
                        try:
                            combined_text = "\n".join(
                                q for _, c in redacted_matches for q in [str(c.get("quote", ""))]
                            )
                            policy_sha = _egress_policy_digest(conn)
                            record_disclosure(
                                conn,
                                request=egress_request,
                                text=combined_text,
                                source_refs=tuple(source_refs),
                                redaction=last_redaction,
                                policy_sha256=policy_sha,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logging.debug("record_disclosure skipped: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logging.debug("egress integration in _model_answer skipped: %s", exc)

    answer, indexes = provider.answer_with_evidence(
        _answer_messages(question, redacted_matches, conversation_context)
    )
    selected: list[tuple[str, CitationPayload]] = []
    seen: set[tuple[str, int, str]] = set()
    for index in indexes:
        if isinstance(index, bool) or not 0 <= index < len(redacted_matches):
            continue
        page_path, citation = redacted_matches[index]
        key = (citation["source_id"], citation["source_version"], citation["locator"])
        if key not in seen:
            seen.add(key)
            selected.append((page_path, citation))
    if not answer.strip() or answer.strip() == "不知道" or not selected:
        return None
    return answer.strip(), selected


def _egress_policy_digest(connection: sqlite3.Connection) -> str:
    try:
        rows = connection.execute(
            "SELECT source_id, egress_class, allowed_hosts FROM source_egress_rules ORDER BY source_id"
        ).fetchall()
        payload = json.dumps(
            [list(r) for r in rows], sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        import hashlib

        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        return "0" * 64


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
                "module path and aliases; it never means authentication. "
                "When asked what a code symbol does, summarize direct conditions, assignments, "
                "return expressions, and calls listed under that symbol's section; a prose "
                "description is not required. Prefer facts that answer the question's condition "
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


def _unknown_payload(
    debug: bool,
    trace: list[TraceStep],
    *,
    support: SupportPayload | None = None,
) -> AskPayload:
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
    if support is not None:
        result["support"] = support
    return result


def _applied_code_symbol_matches(
    workspace_root: Path,
    question: str,
    *,
    repository_id: str | None,
) -> tuple[AppliedCodeSymbolMatch, ...]:
    identifiers = _explicit_code_identifiers(question)
    index_path = workspace_root / ".memoryforge" / "index.sqlite"
    if (
        not identifiers
        or _is_code_relation_question(question)
        or not (workspace_root / "raw").is_dir()
        or not index_path.is_file()
        or index_path.is_symlink()
    ):
        return ()
    matches = find_applied_code_symbol_facts(
        workspace_root,
        identifiers,
        repository_id=repository_id,
    )
    requested_kinds = _requested_symbol_kinds(question)
    if requested_kinds:
        matches = tuple(
            match
            for match in matches
            if (kind_match := _SYMBOL_FACT_KIND.match(match.quote)) is not None
            and kind_match["kind"] in requested_kinds
        )
    repository_ids_by_identifier: dict[str, set[str | None]] = {}
    for match in matches:
        repository_ids_by_identifier.setdefault(match.identifier, set()).add(match.repository_id)
    unambiguous = tuple(
        match for match in matches if len(repository_ids_by_identifier[match.identifier]) == 1
    )
    contextualized: list[AppliedCodeSymbolMatch] = []
    for identifier in dict.fromkeys(match.identifier for match in unambiguous):
        group = [match for match in unambiguous if match.identifier == identifier]
        context_identifiers = [candidate for candidate in identifiers if candidate != identifier]
        contextual = [
            match
            for match in group
            if any(context in (match.symbol or "").split(".") for context in context_identifiers)
        ]
        contextualized.extend(contextual or group)
    return tuple(contextualized)


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


def _requested_symbol_kinds(question: str) -> set[str]:
    lowered = question.lower()
    english_terms = set(re.findall(r"[a-z_]+", lowered))
    kinds = {
        kind
        for marker, kind in (
            ("class", "class"),
            ("interface", "interface"),
            ("method", "method"),
            ("function", "function"),
            ("struct", "struct"),
        )
        if marker in english_terms
    }
    if "type alias" in lowered:
        kinds.add("type_alias")
    for marker, kind in (
        ("类", "class"),
        ("接口", "interface"),
        ("方法", "method"),
        ("函数", "function"),
        ("结构体", "struct"),
    ):
        if marker in question:
            kinds.add(kind)
    if kinds & {"function", "method"}:
        kinds.update({"function", "method"})
    return kinds


def _is_code_relation_question(question: str) -> bool:
    english_terms = set(re.findall(r"[a-z_]+", question.lower()))
    return bool(
        english_terms
        & {
            "call",
            "calls",
            "depend",
            "dependency",
            "dependencies",
            "extend",
            "extends",
            "implement",
            "implements",
            "import",
            "imports",
        }
    ) or any(marker in question for marker in ("依赖", "导入", "调用", "继承", "实现"))


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
    for _page_path, citation in selected:
        selected_identifiers.update(
            _code_identifier_tokens(
                f"{citation.get('section_path', '')} "
                f"{citation.get('routing_text', '')} {citation['quote']}"
            )
        )
        matching = _local_english_matching_terms(
            core_terms,
            citation,
            include_section=_page_path in code_page_paths,
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
    current_source_versions = float(
        not real_workspace
        or all(
            is_applied_source_version(
                workspace_root,
                source_id=citation["source_id"],
                source_version=citation["source_version"],
            )
            for _, citation in selected
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


def _candidate_pages(
    workspace_root: Path,
    question: str,
    question_terms: set[str],
    *,
    max_pages: int,
    trace: list[TraceStep],
    repository_id: str | None,
    prefer_index_routes: bool = False,
    exact_symbol_page_paths: tuple[str, ...] = (),
) -> list[Path]:
    wiki_root = workspace_root / "wiki"
    index = wiki_root / "INDEX.md"
    scored: list[tuple[tuple[int, ...], bool, Path]] = []
    definition_question = _is_definition_question(question)
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
                            _definition_title_score(title) if definition_question else 0,
                            int(definition_question and _is_definition_title(title, question)),
                            -extra_module_parts if module_path is not None else 0,
                            sum(not _CJK.fullmatch(term) for term in overlap),
                            len(overlap),
                            sum(len(term) for term in overlap),
                        ),
                        _is_code_index_title(title),
                        page,
                    )
                )
    strict_fts_paths: tuple[str, ...] = ()
    relaxed_fts_paths: tuple[str, ...] = ()
    fact_paths: tuple[str, ...] = ()
    index_path = workspace_root / ".memoryforge" / "index.sqlite"
    if safe_index is None or (index_path.is_file() and not index_path.is_symlink()):
        cjk_terms = sorted(
            term for term in question_terms if _CJK.fullmatch(term) and len(term) > 1
        )
        if (workspace_root / "raw").is_dir():
            fact_paths = find_applied_wiki_fact_page_paths(
                workspace_root,
                cjk_terms,
                limit=max_pages,
                repository_id=repository_id,
            )
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
    if fact_paths:
        trace.append({"level": "L0", "artifact": "Applied Wiki fact index"})
    if strict_fts_paths or relaxed_fts_paths:
        trace.append({"level": "L0", "artifact": "SQLite FTS5 applied-source index"})
    strict_pages = [
        page
        for path in strict_fts_paths
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
    ]
    fact_pages = [
        page
        for path in fact_paths
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
            -candidate[0][5],
            -candidate[0][6],
            str(candidate[2].relative_to(workspace_root)),
        ),
    )
    module_pages = [page for score, _, page in ranked_index if score[0]]
    index_pages = [page for score, _, page in ranked_index if not score[0]]
    explanatory_pages = [
        page for score, code_title, page in ranked_index if not score[0] and not code_title
    ]
    has_explanatory_definition = any(
        not code_title and (score[1] or score[2]) for score, code_title, _ in ranked_index
    )
    code_index_pages = [
        page for score, code_title, page in ranked_index if not score[0] and code_title
    ]
    relaxed_pages = [
        page
        for path in relaxed_fts_paths
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
    ]
    exact_symbol_pages = [
        page
        for path in exact_symbol_page_paths
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
    ]
    if definition_question:
        ordered_pages = (
            *explanatory_pages,
            *fact_pages,
            *strict_pages,
            *module_pages,
            *code_index_pages,
        )
    else:
        ordered_pages = (*module_pages, *fact_pages, *strict_pages)
    exact_code_pages: tuple[WikiPage, ...] = ()
    if (
        (not definition_question or not has_explanatory_definition)
        and (ranked_index or exact_symbol_page_paths or relaxed_fts_paths)
    ):
        exact_code_pages = _exact_code_pages(
            workspace_root,
            question,
            max_pages=max_pages,
            repository_id=repository_id,
        )
    ordered_pages = (*exact_symbol_pages, *exact_code_pages, *ordered_pages)
    if (exact_symbol_pages or exact_code_pages) and (
        not _is_code_relation_question(question)
        or any(marker in question for marker in ("方法", "字段", "属性", "函数"))
    ):
        return list(dict.fromkeys((*exact_symbol_pages, *exact_code_pages)))[:max_pages]
    if prefer_index_routes and not any(_CJK.fullmatch(term) for term in question_terms):
        ordered_pages += tuple(
            _document_frequency_pages(
                workspace_root,
                question_terms,
                max_pages=max_pages,
                allowed_paths=allowed_paths,
            )
        )
    ordered_pages += (
        (*index_pages, *relaxed_pages) if prefer_index_routes else (*relaxed_pages, *index_pages)
    )
    candidates: list[Path] = []
    for page in ordered_pages:
        if page not in candidates:
            candidates.append(page)
    return candidates[:max_pages]


def _document_frequency_pages(
    workspace_root: Path,
    question_terms: set[str],
    *,
    max_pages: int,
    allowed_paths: set[str] | None,
) -> list[Path]:
    pages_root = workspace_root / "wiki/pages"
    pages = [
        page
        for path in sorted(pages_root.rglob("*.md"))
        if (page := _safe_wiki_page(workspace_root, path)) is not None
        and not _is_code_page(page)
        and (allowed_paths is None or str(page.relative_to(workspace_root)) in allowed_paths)
    ]
    page_terms = [(page, _terms(page.read_text(encoding="utf-8"))) for page in pages]
    frequencies = Counter(term for _, terms in page_terms for term in terms)
    scores = {
        page: sum(
            math.log((len(pages) + 1) / (frequencies[term] + 1)) + 1
            for term in question_terms & terms
        )
        for page, terms in page_terms
    }
    return sorted(
        (page for page in pages if scores[page] > 0),
        key=lambda page: (-scores[page], str(page)),
    )[:max_pages]


def _exact_code_pages(
    workspace_root: Path,
    question: str,
    *,
    max_pages: int,
    repository_id: str | None,
) -> tuple[Path, ...]:
    """Route explicit CamelCase symbols and code paths to code pages first."""
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
    use_index = any(marker in question for marker in ("方法", "字段", "属性", "函数"))
    allowed_paths = (
        set(repository_page_paths(workspace_root, repository_id))
        if repository_id is not None
        else None
    )
    for identifier in identifiers[:3]:
        symbol_pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])")
        if use_index:
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
            if (
                allowed_paths is not None
                and str(page.relative_to(workspace_root)) not in allowed_paths
            ):
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
    return _is_code_file_content(page.read_text(encoding="utf-8"))


def _is_code_file_content(content: str) -> bool:
    prefix = content[:400]
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


def _validate_min_source_count(min_source_count: int) -> None:
    if (
        isinstance(min_source_count, bool)
        or not isinstance(min_source_count, int)
        or not 1 <= min_source_count <= 10
    ):
        raise ValueError("min_source_count must be an integer between 1 and 10")


def _answer_citation_limit(question: str, max_citations: int) -> int:
    """Give a two-part question room for two complementary source facts."""
    if max_citations == 1 and "方法" in question:
        return 8
    if max_citations == 1 and any(marker in question for marker in ("子模块", "字段", "属性")):
        return 6
    if max_citations == 1 and any(
        marker in question for marker in ("分别", "以及", "与", "、", "，", "什么时候")
    ):
        return 2
    return max_citations


def _top_matches(
    matches: list[tuple[tuple[int, ...], str, CitationPayload]],
    max_citations: int,
    *,
    question_terms: set[str],
    required_sources: int = 1,
) -> list[tuple[str, CitationPayload]]:
    # ponytail: greedy page-level coverage is O(n²), sufficient for the 6-citation budget.
    selected: list[tuple[str, CitationPayload]] = []
    selected_sources: set[tuple[str, int]] = set()
    remaining: list[tuple[tuple[int, ...], str, CitationPayload]] = []
    seen_citations: set[tuple[str, int, str]] = set()
    for match in sorted(matches, key=lambda candidate: candidate[0], reverse=True):
        citation = match[2]
        citation_key = (
            citation["source_id"],
            citation["source_version"],
            citation["locator"],
        )
        if citation_key not in seen_citations:
            seen_citations.add(citation_key)
            remaining.append(match)
    covered_terms: set[str] = set()
    while remaining and len(selected) < max_citations:
        if not selected:
            selected_index = 0
        else:
            selected_index = max(
                range(len(remaining)),
                key=lambda index: (
                    int(
                        len(selected_sources) < required_sources
                        and (
                            remaining[index][2]["source_id"],
                            remaining[index][2]["source_version"],
                        )
                        not in selected_sources
                    ),
                    len(_matching_terms(question_terms, remaining[index][2]) - covered_terms),
                    remaining[index][0],
                ),
            )
        _, page_path, citation = remaining.pop(selected_index)
        selected.append((page_path, citation))
        selected_sources.add((citation["source_id"], citation["source_version"]))
        covered_terms.update(_matching_terms(question_terms, citation))
    return selected


def _rank_matches(
    matches: list[tuple[frozenset[str], bool, str, CitationPayload]],
    *,
    question_terms: set[str],
    page_ranks: dict[str, int] | None = None,
    local_morphology_pages: set[str] | None = None,
    focus_terms: set[str] | None = None,
    prioritize_focus: bool = False,
    prefer_environment_assignments: bool = False,
    prefer_code_assignments: bool = False,
    prefer_failure_facts: bool = False,
    prefer_code_modules: bool = False,
    exact_symbol_fact_keys: set[tuple[str, str, int, str, str]] | None = None,
    conversation_page_paths: set[str] | None = None,
    prefer_capability_facts: bool = False,
    prefer_cleanup_conclusion: bool = False,
    prefer_exact_identifiers: set[str] | None = None,
) -> list[tuple[tuple[int, ...], str, CitationPayload]]:
    page_ranks = page_ranks or {}
    local_morphology_pages = local_morphology_pages or set()
    focus_terms = focus_terms or set()
    exact_symbol_fact_keys = exact_symbol_fact_keys or set()
    conversation_page_paths = conversation_page_paths or set()
    prefer_exact_identifiers = prefer_exact_identifiers or set()
    page_aware = bool(page_ranks) and not any(_CJK.fullmatch(term) for term in question_terms)
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
        direct_overlap = _local_english_matching_terms(
            question_terms,
            citation,
            enabled=page_path in local_morphology_pages,
        )
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
            int(_citation_fact_key(page_path, citation) in exact_symbol_fact_keys),
            int(
                bool(prefer_exact_identifiers)
                and prefer_exact_identifiers <= _citation_terms(citation)
            ),
            int(
                prefer_capability_facts
                and summary
                and any(marker in citation["quote"] for marker in _CAPABILITY_MARKERS)
            ),
            int(page_path in conversation_page_paths and page_ranks.get(page_path) == 0),
            int(
                prefer_cleanup_conclusion
                and any(marker in citation["quote"] for marker in _CLEANUP_RESULT_MARKERS)
                and any(marker in citation["quote"] for marker in _CLEANUP_OBJECT_MARKERS)
            ),
            int(prefer_code_modules and module_path is not None),
            -extra_module_parts if prefer_code_modules and module_path is not None else 0,
            module_section_score if prefer_code_modules else 0,
            int(
                prefer_environment_assignments
                and bool(_ENVIRONMENT_ASSIGNMENT.search(citation["quote"]))
            ),
            int(prefer_code_assignments and "=" in citation["quote"]),
            int(
                prioritize_focus
                and bool(focus_overlap)
                and not citation["quote"].lstrip().startswith("|")
            ),
            len(focus_overlap) if prioritize_focus else 0,
            len(direct_overlap) if page_aware else 0,
            -page_ranks.get(page_path, len(page_ranks)) if page_aware else 0,
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
    # Keep ranking grounded in fact text instead of treating a section heading
    # as if it were an answer.
    return (
        question_terms & _terms(f"{citation.get('routing_text', '')} {citation['quote']}")
    ) - _QUESTION_NOISE_TERMS


def _section_matching_terms(question_terms: set[str], citation: CitationPayload) -> set[str]:
    """Use headings for the few queries whose answer is defined by a section route."""
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


def _is_definition_question(question: str) -> bool:
    return any(marker in question for marker in ("是什么", "什么是", "是啥", "简介", "介绍一下"))


def _is_definition_title(title: str, question: str) -> bool:
    identifiers = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", question)
    folded_title = title.casefold()
    return any(
        len(identifier) > 1 and folded_title.startswith(identifier.casefold())
        for identifier in identifiers
    )


def _definition_title_score(title: str) -> int:
    if "官方定义" in title:
        return 2
    return int(any(marker in title for marker in ("定义", "概览", "总览", "一句话", "简介")))


def _is_code_index_title(title: str) -> bool:
    return title.lower().startswith(("code: ", "code module: "))
