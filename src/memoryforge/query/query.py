"""Deterministic progressive queries over applied Wiki pages."""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import uuid
from collections import Counter
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from memoryforge.compiler.egress_policy import decide_egress, record_disclosure
from memoryforge.compiler.freshness import FreshnessState, page_freshness
from memoryforge.compiler.redaction import redact_for_model
from memoryforge.compiler.wiki_facts import (
    AppliedCodeSymbolMatch,
    CitationPayload,
)
from memoryforge.compiler.wiki_facts import parse_page_citations as _page_citations
from memoryforge.core.egress_models import EgressRequest
from memoryforge.query.contracts import (
    AskPayload,
    EvidencePayload,
    SourceVersionKey,
    SupportPayload,
    TraceStep,
)
from memoryforge.query.provider import EvidenceAnswerProvider, ProviderUnavailableError
from memoryforge.query.support import (
    _CAPABILITY_MARKERS,
    _CJK,
    _CLEANUP_OBJECT_MARKERS,
    _CLEANUP_RESULT_MARKERS,
    _NEGATION_CUES,
    _RANKING_STOP_WORDS,
    _WORDS,
    _citation_fact_key,
    _citation_terms,
    _code_identifier_tokens,
    _content_question_terms,
    _expanded_question_terms,
    _explicit_code_identifiers,
    _has_direct_evidence,
    _is_conversation_search_clue,
    _local_english_matching_terms,
    _matching_terms,
    _section_matching_terms,
    _support_score,
    _terms,
)
from memoryforge.query.support import (
    _direct_matching_terms as _direct_matching_terms,
)
from memoryforge.query.support import (
    answer_is_supported as answer_is_supported,
)
from memoryforge.storage.database import connect as _connect
from memoryforge.storage.database import connect_readonly as _connect_readonly
from memoryforge.storage.workspace import (
    DATABASE_RELATIVE_PATH,
    _wiki_fact_fts_query,
    find_applied_code_symbol_facts,
    find_applied_page_paths,
    find_applied_wiki_fact_page_paths,
    is_public_source_version,
    read_source_excerpt,
    repository_page_paths,
)

if TYPE_CHECKING:
    from memoryforge.core.retrieval_models import RetrievalResult

_FTS_SAFE = re.compile(r"[a-z0-9_]+")

_INDEX_ENTRY = re.compile(
    r"^- \[(?P<title>(?:\\.|[^\]])+)\]\((?P<path>[^)]+)\) — (?P<summary>.+)$",
    re.MULTILINE,
)
_PAGE_TITLE = re.compile(r"^title: (?P<title>.+)$", re.MULTILINE)
_EXPLICIT_TITLE = re.compile(r"《(?P<title>[^》]+)》")
_SYMBOL_FACT_KIND = re.compile(r"^`(?P<symbol>[^`]+)` \((?P<kind>[a-z_]+)\):")
_REPOSITORY_OVERVIEW_LINK = re.compile(r"^pages/repository-[a-f0-9]{12}\.md$")
_ENVIRONMENT_ASSIGNMENT = re.compile(r"\b(?:export\s+)?[A-Z][A-Z0-9_]{2,}=")

DEFAULT_QUERY_MAX_PAGES = 3
DEFAULT_QUERY_MAX_CITATIONS = 6


def answer_question(
    workspace_root: Path,
    question: str,
    *,
    debug: bool = False,
    verify: bool = False,
    max_pages: int = DEFAULT_QUERY_MAX_PAGES,
    max_citations: int = DEFAULT_QUERY_MAX_CITATIONS,
    min_source_count: int = 1,
    provider: EvidenceAnswerProvider | None = None,
    allow_local: bool = False,
    public_only: bool = False,
    repository_id: str | None = None,
    preferred_repository_id: str | None = None,
    conversation_context: str = "",
    page_paths: tuple[str, ...] | None = None,
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
    explicit_titles = _explicit_titles(question)
    explicit_routing_base_terms = _terms(
        " ".join((*explicit_titles, "飞书文档", "飞书资料", "AI", "Codex", "Claude", "会话"))
    )
    content_base_question_terms = (
        _content_question_terms(question) - explicit_routing_base_terms
        if explicit_titles
        else _content_question_terms(question)
    )
    content_question_terms = _expanded_question_terms(content_base_question_terms)
    identifier_terms = {term for term in content_base_question_terms if not _CJK.fullmatch(term)}
    definition_question = _is_definition_question(question)
    definition_subject = _definition_subject(question)
    yes_no_focus_terms = _yes_no_focus_terms(question)
    focus_terms = _question_focus_terms(question) | yes_no_focus_terms
    if "子模" in base_question_terms:
        focus_terms.update({"child", "children", "modules"})
    if "方法" in base_question_terms:
        focus_terms.update({"method", "methods", "func", "function", "functions"})
    if "字段" in base_question_terms:
        focus_terms.update({"field", "fields", "struct", "attribute", "attributes"})
    answer_citation_limit = _answer_citation_limit(question, max_citations)
    citation_question_terms = content_question_terms - _repository_name_terms(
        workspace_root,
        repository_id,
    )
    prefer_environment_assignments = "环境变量" in question
    prefer_code_assignments = any(marker in question for marker in ("换算", "计算", "转换", "转成"))
    prefer_failure_facts = bool({"不可", "可用", "失败", "超时"} & base_question_terms)
    prefer_feishu_operations = any(
        marker in question.lower()
        for marker in (
            "飞书",
            "步骤",
            "操作",
            "流程",
            "配置",
            "开通",
            "申请",
            "设置",
            "how to",
            "steps",
            "procedure",
            "configure",
            "setup",
        )
    )
    use_section_routes = (
        prefer_environment_assignments
        or prefer_failure_facts
        or prefer_feishu_operations
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
    strict_source_kind = _strict_source_kind(question)
    trace: list[TraceStep] = []
    if not question_terms:
        return _unknown_payload(debug, trace)
    required_source_groups = _explicit_applied_source_groups(workspace_root, question)
    answer_citation_limit = min(
        max_citations,
        max(answer_citation_limit, len(required_source_groups)),
    )
    explicit_source_keys = frozenset().union(*required_source_groups)
    explicit_source_page_paths = _explicit_source_page_paths(
        workspace_root,
        required_source_groups,
    )
    symbol_matches = _applied_code_symbol_matches(
        workspace_root,
        question,
        repository_id=repository_id,
    )
    if strict_source_kind is not None:
        symbol_matches = ()
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
    search_queries = (question,)
    rewrite_search_queries = getattr(provider, "rewrite_search_queries", None)
    if callable(rewrite_search_queries) and not explicit_titles and not symbol_matches:
        try:
            search_queries = tuple(
                dict.fromkeys((question, *rewrite_search_queries(question)))
            )
        except (ProviderUnavailableError, ValueError) as exc:
            logging.debug("query rewrite unavailable, using original question: %s", exc)
    retrieval_debug["query_variants"] = list(search_queries)
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

    retrieval_v2_result: RetrievalResult | None = None
    if egress_request is not None and not explicit_titles:
        try:
            from memoryforge.core.retrieval_models import VisibleSource
            from memoryforge.query.retrieval_v2 import retrieve_candidates

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
            applied_wiki_facts_list = _merge_retrieval_facts(
                _retrieval_wiki_facts(workspace_root, repository_id, search_query)
                for search_query in search_queries
            )
            if strict_source_kind is not None:
                applied_wiki_facts_list = [
                    fact
                    for fact in applied_wiki_facts_list
                    if fact.get("source_kind") == strict_source_kind
                ]
            if explicit_titles:
                applied_wiki_facts_list = [
                    fact
                    for fact in applied_wiki_facts_list
                    if (str(fact["source_id"]), int(fact["source_version"])) in explicit_source_keys
                ]
            try:
                retrieval_v2_result = retrieve_candidates(
                    workspace_root,
                    question,
                    repository_id=repository_id,
                    visible_source=visible,
                    max_pages=max_pages * 4,
                    wiki_facts=applied_wiki_facts_list,
                    query_variants=search_queries[1:],
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
    feishu_page_paths: set[str] = set()
    code_page_identifiers: dict[str, set[str]] = {}
    retrieval_page_paths = (
        tuple(
            dict.fromkeys(
                (
                    *explicit_source_page_paths,
                    *(
                        candidate.page_path
                        for candidate in retrieval_v2_result.candidates
                        if candidate.source_kind != "code"
                        or "source_code" in retrieval_v2_result.routes
                    ),
                )
            )
        )
        if retrieval_v2_result is not None
        else explicit_source_page_paths
    )
    retrieval_model_matches = _retrieval_candidate_matches(
        retrieval_v2_result,
        applied_wiki_facts_list if retrieval_v2_result is not None else [],
        content_question_terms,
        set().union(*(_content_question_terms(query) for query in search_queries)),
    )

    candidate_pages = (
        [
            page
            for path in explicit_source_page_paths
            if page_paths is None or path in page_paths
            if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
        ][:max_pages]
        if explicit_titles
        else _candidate_pages(
            workspace_root,
            question,
            question_terms,
            max_pages=max_pages,
            trace=trace,
            repository_id=repository_id,
            preferred_repository_id=preferred_repository_id,
            prefer_index_routes=_has_many_index_routes(workspace_root),
            exact_symbol_page_paths=exact_symbol_page_paths,
            preferred_page_paths=retrieval_page_paths,
            required_page_paths=page_paths,
        )
    )
    for page_rank, page in enumerate(candidate_pages):
        content = page.read_text(encoding="utf-8")
        page_path = str(page.relative_to(workspace_root))
        prefix = content[:400]
        frontmatter_end = content.find("\n---\n", 4)
        frontmatter = content[: frontmatter_end + 5] if frontmatter_end >= 0 else prefix
        code_page = (
            'title: "Code:' in prefix
            or 'title: "Code module:' in prefix
            or "generated: code_wiki" in prefix
            or "generated: code_module_overview" in prefix
        )
        conversation_page = '"conversation"' in frontmatter
        feishu_page = '"feishu"' in frontmatter
        page_source_kind = (
            "conversation"
            if conversation_page
            else "feishu"
            if feishu_page
            else "code"
            if code_page
            else "note"
        )
        if strict_source_kind is not None and page_source_kind != strict_source_kind:
            continue
        page_ranks[page_path] = page_rank
        if conversation_page:
            conversation_page_paths.add(page_path)
        if feishu_page:
            feishu_page_paths.add(page_path)
        title_match = _PAGE_TITLE.search(prefix)
        conversation_title = ""
        if page_path in conversation_page_paths and title_match is not None:
            with suppress(json.JSONDecodeError):
                parsed_title = json.loads(title_match.group("title"))
                if isinstance(parsed_title, str):
                    conversation_title = parsed_title
        code_file_content = code_page and _is_code_file_content(content)
        if code_file_content or "generated: code_" in prefix:
            code_page_paths.add(page_path)
        if code_file_content:
            code_page_identifiers[page_path] = _code_identifier_tokens(_code_fact_text(content))
        if not any(_CJK.fullmatch(term) for term in question_terms) and not code_page:
            local_morphology_pages.add(page_path)
        trace.append({"level": "L1", "artifact": page_path})
        for citation in _page_citations(content):
            if (
                explicit_titles
                and (
                    citation["source_id"],
                    citation["source_version"],
                )
                not in explicit_source_keys
            ):
                continue
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
                _section_matching_terms(content_question_terms, citation)
                if use_section_routes
                else _matching_terms(content_question_terms, citation)
            )
            overlap = _local_english_matching_terms(
                content_question_terms,
                citation,
                include_section=use_section_routes,
                enabled=page_path in local_morphology_pages,
            )
            is_summary = citation.get("is_summary", False)
            raw_candidate_matches.append((frozenset(overlap), is_summary, page_path, citation))
            has_cjk_terms = any(_CJK.fullmatch(term) for term in content_question_terms)
            required_overlap = 1 if len(content_question_terms) == 1 else 2
            if has_cjk_terms:
                required_overlap = min(3, len(content_question_terms))
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
                and not any(
                    not _CJK.fullmatch(term) and term.isascii() and term.isalpha()
                    for term in overlap
                )
                and not (
                    ("字段" in base_question_terms and overlap & focus_terms)
                    or ("方法" in base_question_terms and overlap & identifier_terms)
                )
            ):
                sufficient_match = False
            if _citation_fact_key(page_path, citation) in exact_symbol_fact_keys:
                sufficient_match = True
            if definition_subject and not _defines_subject(
                citation,
                definition_subject,
                code_page=code_page,
                identifier_terms=identifier_terms,
            ):
                sufficient_match = False
            if sufficient_match:
                raw_matches.append((frozenset(overlap), is_summary, page_path, citation))

    explicit_numbers = set(re.findall(r"(?<![A-Za-z0-9])\d{2,}(?![A-Za-z0-9])", question))
    if explicit_numbers:
        raw_matches = [
            match
            for match in raw_matches
            if explicit_numbers <= set(re.findall(r"\d{2,}", match[3]["quote"]))
        ]
        raw_candidate_matches = [
            match
            for match in raw_candidate_matches
            if explicit_numbers <= set(re.findall(r"\d{2,}", match[3]["quote"]))
        ]
    test_lifecycle_question = "用例" in base_question_terms and bool(
        {"运行", "清理"} & base_question_terms
    )
    if test_lifecycle_question:
        raw_matches = [match for match in raw_matches if _is_test_lifecycle_fact(match[3])]
        raw_candidate_matches = [
            match for match in raw_candidate_matches if _is_test_lifecycle_fact(match[3])
        ]
    if "方法" in base_question_terms and identifier_terms:
        method_symbol = max(identifier_terms, key=len)
        raw_matches = [match for match in raw_matches if method_symbol in _citation_terms(match[3])]
        raw_candidate_matches = [
            match for match in raw_candidate_matches if method_symbol in _citation_terms(match[3])
        ]
    if yes_no_focus_terms:
        required_focus = min(2, len(yes_no_focus_terms))
        raw_matches = [
            match
            for match in raw_matches
            if len(yes_no_focus_terms & _citation_terms(match[3])) >= required_focus
        ]
        raw_candidate_matches = [
            match
            for match in raw_candidate_matches
            if len(yes_no_focus_terms & _citation_terms(match[3])) >= required_focus
        ]

    matches = _rank_matches(
        raw_matches,
        question_terms=content_question_terms,
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
        feishu_page_paths=feishu_page_paths,
        prefer_capability_facts=prefer_capability_facts,
        prefer_cleanup_conclusion=prefer_cleanup_conclusion,
        prefer_exact_identifiers=identifier_terms,
        prefer_feishu_operations=prefer_feishu_operations,
    )
    candidate_matches = _rank_matches(
        raw_candidate_matches,
        question_terms=content_question_terms,
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
        feishu_page_paths=feishu_page_paths,
        prefer_capability_facts=prefer_capability_facts,
        prefer_cleanup_conclusion=prefer_cleanup_conclusion,
        prefer_exact_identifiers=identifier_terms,
        prefer_feishu_operations=prefer_feishu_operations,
    )
    model_candidates = [
        match
        for match in candidate_matches
        if _has_direct_evidence(content_question_terms, match[2])
    ]

    if not matches and (provider is None or not (retrieval_model_matches or model_candidates)):
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
            question_terms=citation_question_terms,
            required_sources=min_source_count,
            required_source_groups=required_source_groups,
            minimum_citations=answer_citation_limit,
        )
        answer = _fallback_answer(question, selected)
    else:
        generation_matches = _merge_model_matches(
            matches,
            retrieval_model_matches,
            model_candidates,
        )
        try:
            generated = _model_answer(
                workspace_root,
                question,
                generation_matches,
                provider,
                allow_local=allow_local,
                conversation_context=conversation_context,
                egress_request=egress_request,
            )
        except ProviderUnavailableError:
            module_fallbacks = [
                match
                for match in generation_matches
                if match[2]["quote"].startswith("Main exported operations in `")
            ]
            fallback_matches = _usable_matches(
                workspace_root,
                module_fallbacks or generation_matches,
                allow_local=allow_local,
            )
            if not fallback_matches:
                return _unknown_payload(debug, trace)
            selected = _top_matches(
                _prefer_readable_fallbacks(fallback_matches),
                answer_citation_limit,
                question_terms=citation_question_terms,
                required_sources=min_source_count,
                required_source_groups=required_source_groups,
                minimum_citations=answer_citation_limit,
            )
            answer = _fallback_answer(question, selected)
            model_status = "fallback"
        else:
            if generated is None:
                return _unknown_payload(debug, trace)
            answer, selected = generated
            model_status = "used"

    page_freshness_warnings: list[str] = []
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
                from memoryforge.storage.workspace import Workspace

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
                        page_freshness_warnings.append(f"{page_path}:{report.state.value} dropped")
                        continue
                    if report.state == FreshnessState.STALE:
                        stale_page_penalty[page_path] = 0.5
                        page_freshness_warnings.append(f"{page_path}:stale support_score -0.5")
                except Exception as exc:  # noqa: BLE001
                    logging.debug("page_freshness skipped for %s: %s", page_path, exc)
                filtered_selected.append((page_path, citation))
            selected = filtered_selected
        if stale_page_penalty:
            retrieval_debug["freshness_warnings"] = list(page_freshness_warnings)
    except Exception as exc:  # noqa: BLE001
        logging.debug("freshness integration skipped: %s", exc)

    if not selected:
        return _unknown_payload(debug, trace)

    support = _support_score(
        workspace_root,
        question,
        content_question_terms,
        selected,
        symbol_matches=symbol_matches,
        exact_symbol_fact_keys=exact_symbol_fact_keys,
        required_sources=min_source_count,
        required_source_groups=required_source_groups,
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
        selected_sources = {
            (citation["source_id"], citation["source_version"]) for _, citation in selected
        }
        missing_titles = [
            title
            for title, group in zip(explicit_titles, required_source_groups, strict=True)
            if not group & selected_sources
        ]
        return _unknown_payload(
            debug,
            trace,
            support=support,
            answer=answer,
            selected=selected,
            unsupported_aspects=[
                f"required_source_group_incomplete:{title}" for title in missing_titles
            ]
            or None,
        )

    citations = [citation for _, citation in selected]
    pages = list(dict.fromkeys(page_path for page_path, _ in selected))
    for page_path in pages:
        trace.append({"level": "L2", "artifact": page_path})
    citation = citations[0]
    result: AskPayload = {
        "status": "answered",
        "evidence_status": "grounded",
        "answer": answer,
        "supported_claims": [answer],
        "unsupported_aspects": [],
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
    provider: EvidenceAnswerProvider,
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
            logging.debug("redact_for_model failed closed: %s", exc)
            return None
        redacted_matches.append((page_path, redacted_citation))  # type: ignore[arg-type]
        source_refs.append((str(citation["source_id"]), int(citation["source_version"])))

    if egress_request is not None and last_redaction is not None:
        db_path = workspace_root / DATABASE_RELATIVE_PATH
        if db_path.is_file():
            try:
                from memoryforge.core.models import Sensitivity

                combined_text = "\n".join(
                    str(citation.get("quote", "")) for _, citation in redacted_matches
                )
                if len(combined_text) > egress_request.max_characters:
                    return None
                with _connect(db_path) as conn:
                    policy_schema = conn.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'source_versions'"
                    ).fetchone()
                    if policy_schema is None:
                        # Static Wiki fixtures have no managed source registry. They
                        # require the caller's explicit local opt-in to use a model.
                        if not allow_local:
                            return None
                    else:
                        for source_id, source_version in dict.fromkeys(source_refs):
                            row = conn.execute(
                                "SELECT sensitivity FROM source_versions WHERE id = ?",
                                (source_version,),
                            ).fetchone()
                            if row is None:
                                return None
                            decision = decide_egress(
                                conn,
                                request=egress_request,
                                source_id=source_id,
                                source_version=source_version,
                                sensitivity=Sensitivity(str(row[0])),
                            )
                            if not decision.allowed and not (
                                allow_local
                                and getattr(provider, "accepts_local_evidence", False)
                            ):
                                return None
                        record_disclosure(
                            conn,
                            request=egress_request,
                            text=combined_text,
                            source_refs=tuple(dict.fromkeys(source_refs)),
                            redaction=last_redaction,
                            policy_sha256=_egress_policy_digest(conn),
                        )
            except Exception as exc:  # noqa: BLE001
                logging.debug("egress check or receipt failed closed: %s", exc)
                return None

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
            """
            SELECT source_id, egress_class, allowed_hosts
            FROM source_egress_rules
            ORDER BY source_id
            """
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
    quote = _plain_fact_text(selected[0][1]["quote"])
    if "方法" in _terms(question):
        signature = next(
            (
                _plain_fact_text(citation["quote"])
                for _, citation in selected
                if citation["quote"].startswith("func ")
            ),
            quote,
        )
        body_lines = [
            _plain_fact_text(citation["quote"]).rstrip("{").strip()
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
    return " ".join(_plain_fact_text(citation["quote"]) for _, citation in selected)


def _unknown_payload(
    debug: bool,
    trace: list[TraceStep],
    *,
    support: SupportPayload | None = None,
    answer: str = "",
    selected: list[tuple[str, CitationPayload]] | None = None,
    unsupported_aspects: list[str] | None = None,
) -> AskPayload:
    selected = selected or []
    citations = [citation for _, citation in selected]
    pages = list(dict.fromkeys(page_path for page_path, _ in selected))
    partial = bool(citations)
    unsupported = unsupported_aspects or (
        list(support["failed_hard_gates"])
        if partial and support is not None
        else ["no_local_evidence"]
    )
    citation = citations[0] if citations else None
    result: AskPayload = {
        "status": "unknown",
        "evidence_status": "partial" if partial else "no_local_evidence",
        "answer": answer if partial else "不知道",
        "supported_claims": [answer] if partial and answer else [],
        "unsupported_aspects": unsupported,
        "citations": citations,
        "wiki_pages": pages,
        "source_id": citation["source_id"] if citation else None,
        "source_version": citation["source_version"] if citation else None,
        "locator": citation["locator"] if citation else None,
        "quote": citation["quote"] if citation else None,
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
    contextual_repository_ids = _question_repository_ids(workspace_root, question)
    unambiguous = tuple(
        match
        for match in matches
        if len(repository_ids_by_identifier[match.identifier]) == 1
        or match.repository_id in contextual_repository_ids
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


def _is_test_lifecycle_fact(citation: CitationPayload) -> bool:
    match = _SYMBOL_FACT_KIND.match(citation["quote"])
    if match is None or match["kind"] not in {"function", "method"}:
        return False
    symbol = match["symbol"].casefold()
    return symbol.endswith((".run_test", ".post_test")) or (
        "._wait_" in symbol and "deleted" in symbol
    )


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


def _is_explicit_code_question(question: str) -> bool:
    lowered = question.lower()
    english_terms = set(re.findall(r"[a-z_]+", lowered))
    return bool(
        english_terms
        & {
            "call",
            "class",
            "code",
            "depend",
            "function",
            "import",
            "interface",
            "method",
            "module",
            "struct",
        }
    ) or any(
        marker in question
        for marker in (
            "代码",
            "函数",
            "方法",
            "字段",
            "属性",
            "模块",
            "文件",
            "用例",
            "调用",
            "依赖",
            "导入",
            "继承",
        )
    )


def _retrieval_wiki_facts(
    workspace_root: Path,
    repository_id: str | None,
    question: str,
) -> list[dict[str, Any]]:
    """Use FTS to bound Retrieval v2 before its in-memory reranking lanes.

    Code-identifier tokens (camelCase/PascalCase words such as
    ``PerformanceInfo`` or ``ProvisionedBandwidth``) get an exact AND query
    first: those facts are the precise evidence for symbol-level questions and
    must not be pushed out of the 300-row window by high-frequency Feishu
    pages. When the AND query finds nothing, fall back to the broad OR query.
    """
    database = workspace_root / DATABASE_RELATIVE_PATH
    if not database.is_file() or database.is_symlink():
        return []
    expanded_terms = _expanded_question_terms(_content_question_terms(question))
    identifier_terms = _question_identifier_terms(question)
    try:
        with _connect_readonly(database) as connection:
            query = """
                SELECT facts.page_path, facts.repository_id, repositories.name AS repository_name,
                       facts.source_id, versions.title AS source_title,
                       CASE
                           WHEN instr(versions.tags_json, '"conversation"') > 0
                               THEN 'conversation'
                           WHEN instr(versions.tags_json, '"feishu"') > 0
                                OR sources.source_path LIKE 'feishu/%'
                               THEN 'feishu'
                           WHEN facts.repository_id IS NOT NULL
                                OR instr(versions.tags_json, '"code"') > 0
                                OR instr(versions.tags_json, '"code-module"') > 0
                               THEN 'code'
                           ELSE 'note'
                       END AS source_kind,
                       facts.source_version, facts.locator, facts.section_path,
                       facts.quote, facts.routing_text, facts.symbol, facts.relation_type
                FROM wiki_fact_fts
                JOIN wiki_facts AS facts ON facts.id = wiki_fact_fts.rowid
                JOIN source_versions AS versions ON versions.id = facts.source_version
                JOIN sources AS sources ON sources.id = versions.source_id
                JOIN applied_source_versions AS applied
                  ON applied.source_id = facts.source_id
                 AND applied.source_version_id = facts.source_version
                LEFT JOIN git_repositories AS repositories
                  ON repositories.repository_id = facts.repository_id
                WHERE wiki_fact_fts MATCH ?
                """
            parameters: list[object] = [_expanded_fts_query(expanded_terms, question)]
            if repository_id is not None:
                query += " AND (facts.repository_id IS NULL OR facts.repository_id = ?)"
                parameters.append(repository_id)
            query += " ORDER BY bm25(wiki_fact_fts), facts.page_path, facts.locator LIMIT 300"
            rows = connection.execute(query, tuple(parameters)).fetchall()
            if not rows and identifier_terms:
                identifier_query = " AND ".join(f'"{term}"' for term in identifier_terms)
                parameters = [identifier_query]
                if repository_id is not None:
                    query = query.replace(
                        " WHERE wiki_fact_fts MATCH ?",
                        " WHERE wiki_fact_fts MATCH ?"
                        " AND (facts.repository_id IS NULL OR facts.repository_id = ?)",
                    )
                    parameters.append(repository_id)
                rows = connection.execute(query, tuple(parameters)).fetchall()
    except (sqlite3.Error, ValueError) as exc:
        logging.debug("retrieval_v2 fact load unavailable: %s", exc)
        return []
    return [dict(row) for row in rows]


def _merge_retrieval_facts(
    groups: Iterable[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for facts in groups:
        for fact in facts:
            key = (
                str(fact["page_path"]),
                str(fact["source_id"]),
                int(fact["source_version"]),
                str(fact["locator"]),
            )
            merged.setdefault(key, fact)
    return list(merged.values())


def _retrieval_candidate_matches(
    result: RetrievalResult | None,
    facts: list[dict[str, Any]],
    question_terms: set[str],
    search_terms: set[str],
) -> list[tuple[tuple[int, ...], str, CitationPayload]]:
    if result is None:
        return []
    by_key = {
        (
            str(fact["page_path"]),
            str(fact["source_id"]),
            int(fact["source_version"]),
            str(fact["locator"]),
        ): fact
        for fact in facts
    }
    matches: list[tuple[tuple[int, ...], str, CitationPayload]] = []
    page_counts: Counter[str] = Counter()
    for index, candidate in enumerate(result.candidates):
        if len(matches) >= 18:
            break
        if page_counts[candidate.page_path] >= 3:
            continue
        key = (
            candidate.page_path,
            candidate.source_id,
            candidate.source_version,
            candidate.locator,
        )
        fact = by_key.get(key)
        if fact is None:
            continue
        citation: CitationPayload = {
            "source_id": candidate.source_id,
            "source_version": candidate.source_version,
            "locator": candidate.locator,
            "quote": str(fact["quote"]),
            "grounding": "exact",
        }
        if fact.get("section_path"):
            citation["section_path"] = str(fact["section_path"])
        if fact.get("routing_text"):
            citation["routing_text"] = str(fact["routing_text"])
        required_overlap = 1 if len(question_terms) <= 1 else 2
        if len(_matching_terms(search_terms, citation)) < required_overlap:
            continue
        matches.append(((len(result.candidates) - index,), candidate.page_path, citation))
        page_counts[candidate.page_path] += 1
    return matches


def _merge_model_matches(
    *groups: list[tuple[tuple[int, ...], str, CitationPayload]],
) -> list[tuple[tuple[int, ...], str, CitationPayload]]:
    merged: list[tuple[tuple[int, ...], str, CitationPayload]] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for group in groups:
        for match in group:
            key = _citation_fact_key(match[1], match[2])
            if key not in seen:
                seen.add(key)
                merged.append(match)
    return merged


def _prefer_readable_fallbacks(
    matches: list[tuple[tuple[int, ...], str, CitationPayload]],
) -> list[tuple[tuple[int, ...], str, CitationPayload]]:
    ranked = []
    for score, page_path, citation in matches:
        quote = citation["quote"].lstrip()
        section = citation.get("section_path", "").lower()
        readable = not quote.startswith(("|", "```", "flowchart", "sequenceDiagram"))
        substantive = not any(marker in section for marker in ("来源", "索引", "source"))
        ranked.append(((int(readable), int(substantive), *score), page_path, citation))
    return ranked


def _expanded_fts_query(expanded_terms: set[str], question: str) -> str:
    """FTS query over expanded terms; falls back to the raw question tokens."""
    terms = [term for term in expanded_terms if _FTS_SAFE.fullmatch(term)]
    if not terms:
        return _wiki_fact_fts_query(question)
    escaped = [term.replace('"', '""') for term in terms]
    return " OR ".join(f'"{term}"' for term in escaped)


def _question_identifier_terms(question: str) -> tuple[str, ...]:
    """Lowercased PascalCase/camelCase words in the question, longest first."""
    identifiers = tuple(
        dict.fromkeys(
            match.group(0).lower()
            for match in re.finditer(r"[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+", question)
        )
    )
    return tuple(sorted(identifiers, key=len, reverse=True))


def _candidate_pages(
    workspace_root: Path,
    question: str,
    question_terms: set[str],
    *,
    max_pages: int,
    trace: list[TraceStep],
    repository_id: str | None,
    preferred_repository_id: str | None = None,
    prefer_index_routes: bool = False,
    exact_symbol_page_paths: tuple[str, ...] = (),
    preferred_page_paths: tuple[str, ...] = (),
    required_page_paths: tuple[str, ...] | None = None,
) -> list[Path]:
    wiki_root = workspace_root / "wiki"
    index = wiki_root / "INDEX.md"
    scored: list[tuple[tuple[int, ...], bool, Path]] = []
    definition_question = _is_definition_question(question)
    definition_subject = _definition_subject(question)
    allowed_paths = (
        set(repository_page_paths(workspace_root, repository_id))
        if repository_id is not None
        else None
    )
    if required_page_paths is not None:
        requested = set(required_page_paths)
        allowed_paths = requested if allowed_paths is None else allowed_paths & requested
    preferred_paths = (
        set(repository_page_paths(workspace_root, preferred_repository_id))
        if repository_id is None and preferred_repository_id is not None
        else set()
    )
    candidate_limit = max_pages * 3 if preferred_paths else max_pages
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
                definition_score = _definition_title_score(title)
                if definition_subject and _text_defines_subject(
                    entry.group("summary"), definition_subject
                ):
                    definition_score += 3
                question_identifiers = {term for term in question_terms if not _CJK.fullmatch(term)}
                extra_module_parts = (
                    len(set(module_path.split("/")) - question_identifiers) if module_path else 0
                )
                scored.append(
                    (
                        (
                            int(module_path is not None),
                            definition_score if definition_question else 0,
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
                limit=candidate_limit,
                repository_id=repository_id,
            )
        strict_fts_paths = find_applied_page_paths(
            workspace_root,
            question,
            limit=candidate_limit,
            repository_id=repository_id,
        )
        if len(strict_fts_paths) < candidate_limit:
            relaxed_fts_paths = find_applied_page_paths(
                workspace_root,
                question,
                limit=candidate_limit,
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
    definition_pages = [
        page
        for score, code_title, page in ranked_index
        if not score[0] and not code_title and score[1] >= 3
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
    preferred_pages = [
        page
        for path in preferred_page_paths
        if (page := _safe_wiki_page(workspace_root, workspace_root / path)) is not None
        and (allowed_paths is None or str(page.relative_to(workspace_root)) in allowed_paths)
    ]
    if definition_question:
        ordered_pages = (
            *definition_pages,
            *fact_pages,
            *explanatory_pages,
            *strict_pages,
            *module_pages,
            *code_index_pages,
        )
    elif _is_explicit_code_question(question):
        ordered_pages = (*module_pages, *fact_pages, *strict_pages)
    else:
        ordered_pages = (*fact_pages, *strict_pages, *module_pages)
    exact_code_pages: tuple[Path, ...] = ()
    code_shortcut = (
        _is_explicit_code_question(question)
        or bool(exact_symbol_page_paths)
        or any(marker in question for marker in ("换算", "计算", "转换", "转成"))
        or ("作用" in question and bool(_explicit_code_identifiers(question)))
    )
    if (
        code_shortcut
        and (not definition_question or not has_explanatory_definition)
        and (ranked_index or exact_symbol_page_paths or relaxed_fts_paths)
    ):
        exact_code_pages = _exact_code_pages(
            workspace_root,
            question,
            max_pages=candidate_limit,
            repository_id=repository_id,
        )
    ordered_pages = (
        (
            *exact_symbol_pages,
            *exact_code_pages,
            *definition_pages,
            *preferred_pages,
            *ordered_pages,
        )
        if definition_question
        else (*exact_symbol_pages, *exact_code_pages, *preferred_pages, *ordered_pages)
    )
    if (exact_symbol_pages or exact_code_pages) and (
        not _is_code_relation_question(question)
        or any(marker in question for marker in ("方法", "字段", "属性", "函数"))
    ):
        exact_candidates = list(
            dict.fromkeys((*exact_symbol_pages, *preferred_pages, *exact_code_pages))
        )
        if allowed_paths is not None:
            exact_candidates = [
                page
                for page in exact_candidates
                if str(page.relative_to(workspace_root)) in allowed_paths
            ]
        if preferred_paths:
            exact_candidates.sort(
                key=lambda page: (str(page.relative_to(workspace_root)) not in preferred_paths,)
            )
        return exact_candidates[:max_pages]
    if prefer_index_routes and not any(_CJK.fullmatch(term) for term in question_terms):
        ordered_pages += tuple(
            _document_frequency_pages(
                workspace_root,
                question_terms,
                max_pages=candidate_limit,
                allowed_paths=allowed_paths,
            )
        )
    ordered_pages += (
        (*index_pages, *relaxed_pages) if prefer_index_routes else (*relaxed_pages, *index_pages)
    )
    candidates: list[Path] = []
    for page in ordered_pages:
        if allowed_paths is not None and str(page.relative_to(workspace_root)) not in allowed_paths:
            continue
        if page not in candidates:
            candidates.append(page)
    if preferred_paths:
        candidates.sort(
            key=lambda page: (str(page.relative_to(workspace_root)) not in preferred_paths,)
        )
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
    explicit_code_request = _is_explicit_code_question(question)
    identifiers = tuple(
        dict.fromkeys(
            match.group()
            for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_./-]*", question)
            if (
                any(character.isupper() for character in match.group())
                and (explicit_code_request or not match.group().isupper())
            )
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
    if max_citations == 1:
        return 1
    if "方法" in question:
        return min(max_citations, 8)
    if any(marker in question for marker in ("子模块", "字段", "属性")):
        return min(max_citations, 6)
    if any(
        marker in question
        for marker in (
            "分别",
            "以及",
            "与",
            "、",
            "，",
            "什么时候",
            " and ",
            " after ",
            " before ",
            " when ",
        )
    ):
        return min(max_citations, 2)
    return 1


def _top_matches(
    matches: list[tuple[tuple[int, ...], str, CitationPayload]],
    max_citations: int,
    *,
    question_terms: set[str],
    required_sources: int = 1,
    required_source_groups: tuple[frozenset[SourceVersionKey], ...] = (),
    minimum_citations: int = 1,
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
        uncovered_groups = tuple(
            group for group in required_source_groups if not group & selected_sources
        )
        if not selected:
            selected_index = 0
        else:
            selected_index = max(
                range(len(remaining)),
                key=lambda index: (
                    int(
                        any(
                            (
                                remaining[index][2]["source_id"],
                                remaining[index][2]["source_version"],
                            )
                            in group
                            for group in uncovered_groups
                        )
                    ),
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
        new_terms = _matching_terms(question_terms, citation) - covered_terms
        source = (citation["source_id"], citation["source_version"])
        source_groups_complete = all(group & selected_sources for group in required_source_groups)
        if (
            len(selected) >= minimum_citations
            and len(selected_sources) >= required_sources
            and source_groups_complete
            and not new_terms
        ):
            break
        selected.append((page_path, citation))
        selected_sources.add(source)
        covered_terms.update(new_terms)
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
    feishu_page_paths: set[str] | None = None,
    prefer_capability_facts: bool = False,
    prefer_cleanup_conclusion: bool = False,
    prefer_exact_identifiers: set[str] | None = None,
    prefer_feishu_operations: bool = False,
) -> list[tuple[tuple[int, ...], str, CitationPayload]]:
    page_ranks = page_ranks or {}
    local_morphology_pages = local_morphology_pages or set()
    focus_terms = focus_terms or set()
    exact_symbol_fact_keys = exact_symbol_fact_keys or set()
    conversation_page_paths = conversation_page_paths or set()
    feishu_page_paths = feishu_page_paths or set()
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
                prefer_feishu_operations
                and page_path in feishu_page_paths
                and bool(citation.get("section_path"))
            ),
            int(prefer_feishu_operations and page_path in feishu_page_paths),
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


def _question_repository_ids(workspace_root: Path, question: str) -> set[str]:
    database = workspace_root / DATABASE_RELATIVE_PATH
    if not database.is_file() or database.is_symlink():
        return set()
    try:
        with _connect_readonly(database) as connection:
            rows = connection.execute("SELECT repository_id, name FROM git_repositories").fetchall()
    except sqlite3.Error:
        return set()
    repositories = [(str(row["repository_id"]), str(row["name"])) for row in rows]
    aliases = Counter(
        part.casefold()
        for _, name in repositories
        if name.casefold().endswith("-mgr")
        for part in name.split("-")
        if len(part) >= 3 and part.casefold() != "mgr"
    )
    return {
        repository_id
        for repository_id, name in repositories
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])",
            question,
            re.IGNORECASE,
        )
        or any(
            aliases[part.casefold()] == 1
            and re.search(
                rf"(?<![A-Za-z0-9_-]){re.escape(part)}(?![A-Za-z0-9_-])",
                question,
                re.IGNORECASE,
            )
            for part in name.split("-")
            if len(part) >= 3 and part.casefold() != "mgr"
        )
    }


def _repository_name_terms(workspace_root: Path, repository_id: str | None) -> set[str]:
    if repository_id is None:
        return set()
    database = workspace_root / DATABASE_RELATIVE_PATH
    if not database.is_file() or database.is_symlink():
        return set()
    try:
        with _connect_readonly(database) as connection:
            row = connection.execute(
                "SELECT name FROM git_repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
    except sqlite3.Error:
        return set()
    return _terms(str(row["name"])) if row is not None else set()


def _explicit_titles(question: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            title
            for match in _EXPLICIT_TITLE.finditer(question)
            if (title := match["title"].strip())
        )
    )


def _explicit_applied_source_groups(
    workspace_root: Path,
    question: str,
) -> tuple[frozenset[SourceVersionKey], ...]:
    titles = _explicit_titles(question)
    if not titles:
        return ()
    database = workspace_root / DATABASE_RELATIVE_PATH
    if not database.is_file() or database.is_symlink():
        return tuple(frozenset() for _ in titles)
    groups: list[frozenset[SourceVersionKey]] = []
    try:
        with _connect_readonly(database) as connection:
            for title in titles:
                rows = connection.execute(
                    """
                    SELECT sources.source_id, versions.id
                    FROM applied_source_versions AS applied
                    JOIN sources ON sources.source_id = applied.source_id
                    JOIN source_versions AS versions
                      ON versions.id = applied.source_version_id
                    WHERE versions.title = ? COLLATE NOCASE
                    ORDER BY sources.source_id, versions.id
                    """,
                    (title,),
                ).fetchall()
                groups.append(frozenset((str(row["source_id"]), int(row["id"])) for row in rows))
    except sqlite3.Error:
        return tuple(frozenset() for _ in titles)
    return tuple(groups)


def _explicit_source_page_paths(
    workspace_root: Path,
    groups: tuple[frozenset[SourceVersionKey], ...],
) -> tuple[str, ...]:
    database = workspace_root / DATABASE_RELATIVE_PATH
    if not groups or not database.is_file() or database.is_symlink():
        return ()
    paths: list[str] = []
    try:
        with _connect_readonly(database) as connection:
            for group in groups:
                for source_id, source_version in sorted(group):
                    rows = connection.execute(
                        """
                        SELECT DISTINCT page_path
                        FROM wiki_facts
                        WHERE source_id = ? AND source_version = ?
                        ORDER BY page_path
                        """,
                        (source_id, source_version),
                    ).fetchall()
                    paths.extend(str(row["page_path"]) for row in rows)
    except sqlite3.Error:
        return ()
    return tuple(dict.fromkeys(paths))


def _strict_source_kind(question: str) -> Literal["feishu", "conversation"] | None:
    lowered = question.casefold()
    requests_feishu = any(marker in lowered for marker in ("飞书资料", "飞书文档"))
    requests_conversation = (
        re.search(
            r"(?:ai|codex|claude)\s*(?:会话|conversation)",
            lowered,
        )
        is not None
    )
    if requests_feishu == requests_conversation:
        return None
    return "feishu" if requests_feishu else "conversation"


def _yes_no_focus_terms(question: str) -> set[str]:
    """Return the subject terms of one explicit Chinese existence question."""
    for marker in ("是否", "有没有", "是否有"):
        if marker in question:
            suffix = question.split(marker, maxsplit=1)[1]
            focus = _terms(suffix) - {
                "实现",
                "说明",
                "给出",
                "明确",
                "存在",
                "支持",
            }
            return focus
    for match in _WORDS.finditer(question):
        token = match.group().lower()
        if _CJK.fullmatch(token) and token.endswith("吗"):
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


def _unescape_link_text(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _is_code_definition_fact(
    citation: CitationPayload,
    identifier_terms: set[str],
) -> bool:
    kind = _SYMBOL_FACT_KIND.match(citation["quote"])
    if kind is None or kind["kind"] not in {
        "class",
        "enum",
        "interface",
        "struct",
        "type",
        "type_alias",
    }:
        return False
    symbol_name = kind["symbol"].rsplit(".", maxsplit=1)[-1]
    return not identifier_terms or bool(identifier_terms & _terms(symbol_name))


def _definition_subject(question: str) -> str | None:
    match = re.fullmatch(
        r"\s*(?:"
        r"什么是\s*(?P<prefix>[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]{1,12})"
        r"|(?P<suffix>[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]{1,12})"
        r"\s*(?:是什么|是什么意思|是啥|是(?:一个|个)?什么(?:样的)?(?:项目|工具|系统|平台|产品))"
        r")[？?]?\s*",
        question,
    )
    if match is None:
        return None
    return match["prefix"] or match["suffix"]


def _plain_fact_text(text: str) -> str:
    text = re.sub(r"(?m)^\s*(?:>\s*)+", "", text)
    text = re.sub(r"\s+>\s+", " ", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    return text.strip()


def _defines_subject(
    citation: CitationPayload,
    subject: str,
    *,
    code_page: bool,
    identifier_terms: set[str],
) -> bool:
    if code_page:
        return _is_code_definition_fact(citation, identifier_terms)
    return _text_defines_subject(citation["quote"], subject)


def _text_defines_subject(text: str, subject: str) -> bool:
    searchable = re.sub(r"[`*_]", "", _plain_fact_text(text))
    return (
        re.search(
            rf"(?:^|[\n。；,，:：])\s*{re.escape(subject)}\s*"
            rf"(?:是|为|指|表示|的(?:定位|思路|用途|作用)是)",
            searchable,
            re.IGNORECASE,
        )
        is not None
    )


def _is_definition_question(question: str) -> bool:
    return _definition_subject(question) is not None or any(
        marker in question for marker in ("是什么", "什么是", "是啥", "简介", "介绍一下")
    )


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
