from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal, cast

from memoryforge.core.retrieval_models import RetrievalCandidate, RetrievalResult, VisibleSource
from memoryforge.core.tokenization import bigram_tokens
from memoryforge.query.support import (
    _content_question_terms,
    _expanded_question_terms,
    _explicit_code_identifiers,
)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\w+", re.UNICODE)
_IDENT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_WORD = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_HYPHENATED_NAME = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)")
_EXPLICIT_CODE_IDENTIFIER = re.compile(
    r"`[^`]+`|[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+|"
    r"[A-Za-z_$][A-Za-z0-9$]*_[A-Za-z0-9_$]+|[A-Za-z]+[A-Z][A-Za-z0-9_$]*"
)
_CONVERSATION_MARKERS = (
    "会话",
    "聊天",
    "刚才",
    "上次",
    "之前说",
    "我们聊",
    "conversation",
    "chat history",
    "previously discussed",
)
_FEISHU_OPERATION_MARKERS = (
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
# Code-location intent that must beat generic Feishu operation markers: these
# phrases explicitly ask where a code construct is defined, which a Feishu
# procedure lookup would not. Generic words like 配置/带宽/步骤 stay with the
# Feishu markers so operation-style questions are not misrouted.
_CODE_INTENT_MARKERS = re.compile(
    r"代码|源码|定义在|定义于|在哪里定义|defined at|defined in|implemented in|located in",
    re.IGNORECASE,
)


def retrieve_candidates(
    workspace: Path,
    question: str,
    *,
    repository_id: str | None,
    visible_source: VisibleSource,
    max_pages: int = 3,
    wiki_facts: list[dict[str, Any]] | None = None,
    code_symbols: list[dict[str, Any]] | None = None,
    code_relations: list[dict[str, Any]] | None = None,
    query_variants: tuple[str, ...] = (),
) -> RetrievalResult:
    if wiki_facts is None:
        wiki_facts = []
    if code_symbols is None:
        code_symbols = []
    if code_relations is None:
        code_relations = []

    facts = (
        list(wiki_facts)
        if repository_id is None
        else [f for f in wiki_facts if f.get("repository_id") in (None, repository_id)]
    )

    routes: list[str] = []
    preferred_source_kind = _preferred_source_kind(question)
    if preferred_source_kind is not None:
        routes.append(f"source_{preferred_source_kind}")

    identifiers = _explicit_code_identifiers(question)
    exact_hits = (
        _exact_lane(" ".join(identifiers), facts, repository_id)
        if identifiers and preferred_source_kind == "code"
        else []
    )
    if exact_hits:
        routes.append("exact")

    question_terms = _content_question_terms(question)
    expanded_terms = sorted(
        term for term in _expanded_question_terms(question_terms) - question_terms if term.isascii()
    )
    lexical_lists = [
        _lexical_lane(" ".join((variant, *expanded_terms)), facts)
        for variant in dict.fromkeys((question, *query_variants))
    ]
    lexical_hits = _rrf_hits(lexical_lists)
    if lexical_hits:
        routes.append("lexical")
    if len(lexical_lists) > 1:
        routes.append("multi_query")

    symbol_ids_from_hits: set[str] = set()
    for fact, _rank in exact_hits + lexical_hits:
        sym = fact.get("symbol")
        if sym:
            symbol_ids_from_hits.add(sym)

    relation_hits = _relation_lane(
        symbol_ids_from_hits,
        facts,
        code_symbols,
        code_relations,
        repository_id,
    )
    if repository_id is None:
        cross_repository_hits = _cross_repository_lane(question, facts)
        if cross_repository_hits:
            relation_hits = cross_repository_hits + relation_hits
            routes.append("cross_repository")
    if relation_hits:
        routes.append("relation")

    def _filter_visible(
        hits: list[tuple[dict[str, Any], int]],
    ) -> list[tuple[dict[str, Any], int]]:
        result = []
        seen_keys: set[tuple[str, str, int, str]] = set()
        for fact, rank in hits:
            key = (
                fact["page_path"],
                fact["source_id"],
                fact["source_version"],
                fact["locator"],
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if not visible_source(fact["source_id"], fact["source_version"]):
                continue
            result.append((fact, rank))
        return result

    exact_filtered = _filter_visible(exact_hits)
    lexical_filtered = _filter_visible(lexical_hits)
    relation_filtered = _filter_visible(relation_hits)

    dedup: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    exact_ranks: dict[tuple[str, str, int, str], int] = {}
    lexical_ranks: dict[tuple[str, str, int, str], int] = {}
    relation_ranks: dict[tuple[str, str, int, str], int] = {}
    exact_full_hit_keys: set[tuple[str, str, int, str]] = set()

    for fact, rank in exact_filtered:
        key = (fact["page_path"], fact["source_id"], fact["source_version"], fact["locator"])
        if key not in dedup:
            dedup[key] = fact
        if key not in exact_ranks or rank < exact_ranks[key]:
            exact_ranks[key] = rank
        if fact.get("_exact_full_match"):
            exact_full_hit_keys.add(key)

    for fact, rank in lexical_filtered:
        key = (fact["page_path"], fact["source_id"], fact["source_version"], fact["locator"])
        if key not in dedup:
            dedup[key] = fact
        if key not in lexical_ranks or rank < lexical_ranks[key]:
            lexical_ranks[key] = rank

    for fact, rank in relation_filtered:
        key = (fact["page_path"], fact["source_id"], fact["source_version"], fact["locator"])
        if key not in dedup:
            dedup[key] = fact
        if key not in relation_ranks or rank < relation_ranks[key]:
            relation_ranks[key] = rank

    candidates: list[RetrievalCandidate] = []
    for key, fact in dedup.items():
        exact_rank = exact_ranks.get(key)
        lexical_rank = lexical_ranks.get(key)
        relation_rank = relation_ranks.get(key)

        fused_score = 0.0
        if exact_rank is not None:
            fused_score += 1.0 / (60.0 + exact_rank)
        if lexical_rank is not None:
            fused_score += 1.0 / (60.0 + lexical_rank)
        if relation_rank is not None:
            fused_score += 1.0 / (60.0 + relation_rank)
        if key in exact_full_hit_keys:
            fused_score += 0.02
        source_kind = _source_kind(fact)
        if source_kind == preferred_source_kind:
            fused_score += 0.01

        kind: Literal["page", "symbol", "relation"] = "page"
        if fact.get("symbol"):
            kind = "symbol"
        elif fact.get("relation_type"):
            kind = "relation"

        candidates.append(
            RetrievalCandidate(
                page_path=fact["page_path"],
                source_id=fact["source_id"],
                source_version=fact["source_version"],
                locator=fact["locator"],
                kind=kind,
                source_kind=source_kind,
                exact_rank=exact_rank,
                lexical_rank=lexical_rank,
                relation_rank=relation_rank,
                fused_score=fused_score,
            )
        )

    candidates.sort(
        key=lambda c: (
            -c.fused_score,
            c.page_path,
            c.source_id,
            c.locator,
        )
    )

    seen_pages: set[str] = set()
    page_count = 0
    max_candidates = max_pages * 6
    final: list[RetrievalCandidate] = []
    for cand in candidates:
        if len(final) >= max_candidates:
            break
        if cand.page_path not in seen_pages:
            if page_count >= max_pages:
                continue
            seen_pages.add(cand.page_path)
            page_count += 1
        final.append(cand)

    return RetrievalResult(
        candidates=tuple(final),
        routes=tuple(routes),
    )


def _extract_identifier_tokens(text: str) -> list[str]:
    tokens = _IDENT_TOKEN.findall(text)
    return [t for t in tokens if len(t) >= 2]


def _exact_lane(
    question: str,
    facts: list[dict[str, Any]],
    repository_id: str | None,
) -> list[tuple[dict[str, Any], int]]:
    idents = _extract_identifier_tokens(question)
    if not idents:
        return []

    scored: list[tuple[dict[str, Any], int, bool]] = []
    for fact in facts:
        sym = fact.get("symbol") or ""
        page = fact.get("page_path") or ""
        sym_matches = 0
        full_hit = False
        for ident in idents:
            short = ident.split(".")[-1] if "." in ident else ident
            if sym == ident or sym.endswith("." + ident):
                sym_matches += 2
                if sym == ident:
                    full_hit = True
            elif sym and (sym == short or sym.endswith("." + short)):
                sym_matches += 1
            parts = page.split("/")
            base = parts[-1].replace(".md", "") if parts else ""
            if ident == base or short == base:
                sym_matches += 1
        if sym_matches > 0:
            scored.append((fact, sym_matches, full_hit))

    scored.sort(key=lambda x: (-x[1], x[0]["page_path"], x[0]["locator"]))
    result: list[tuple[dict[str, Any], int]] = []
    for rank, (fact, _score, full_hit) in enumerate(scored[:20], start=1):
        fact_copy = dict(fact)
        if full_hit:
            fact_copy["_exact_full_match"] = True
        result.append((fact_copy, rank))
    return result


def _tokenize(text: str) -> list[str]:
    return bigram_tokens(text)


def _lexical_lane(
    question: str,
    facts: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], int]]:
    q_tokens = _tokenize(question)
    if not q_tokens:
        return []

    q_counter = Counter(q_tokens)

    doc_counters: list[Counter[str]] = []
    for fact in facts:
        blob = " ".join(
            [
                fact.get("routing_text", "") or "",
                fact.get("quote", "") or "",
                fact.get("section_path", "") or "",
                fact.get("source_title", "") or "",
                fact.get("page_path", "") or "",
            ]
        )
        doc_counters.append(Counter(_tokenize(blob)))

    N = max(1, len(doc_counters))
    df: Counter[str] = Counter()
    for dc in doc_counters:
        for tok in dc:
            df[tok] += 1

    scores: list[tuple[int, float]] = []
    for idx, dc in enumerate(doc_counters):
        score = 0.0
        for tok, qf in q_counter.items():
            tf = dc.get(tok, 0)
            if tf <= 0:
                continue
            idf = math.log((N + 1.0) / (df.get(tok, 0) + 1.0)) + 1.0
            score += qf * tf * idf
        if score > 0.0:
            scores.append((idx, score))

    scores.sort(key=lambda x: (-x[1], facts[x[0]]["page_path"], facts[x[0]]["locator"]))
    result: list[tuple[dict[str, Any], int]] = []
    for rank, (idx, _s) in enumerate(scores[:20], start=1):
        result.append((facts[idx], rank))
    return result


def _rrf_hits(
    ranked_lists: list[list[tuple[dict[str, Any], int]]],
) -> list[tuple[dict[str, Any], int]]:
    scored: dict[tuple[str, str, int, str], tuple[dict[str, Any], float]] = {}
    for ranked in ranked_lists:
        for fact, rank in ranked:
            key = (
                str(fact["page_path"]),
                str(fact["source_id"]),
                int(fact["source_version"]),
                str(fact["locator"]),
            )
            previous = scored.get(key)
            score = (previous[1] if previous else 0.0) + 1.0 / (60.0 + rank)
            scored[key] = (fact, score)
    ordered = sorted(
        scored.values(),
        key=lambda item: (
            -item[1],
            str(item[0]["page_path"]),
            str(item[0]["locator"]),
        ),
    )
    return [(fact, rank) for rank, (fact, _score) in enumerate(ordered[:40], start=1)]


def _relation_lane(
    seed_symbol_ids: set[str],
    facts: list[dict[str, Any]],
    code_symbols: list[dict[str, Any]],
    code_relations: list[dict[str, Any]],
    repository_id: str | None,
) -> list[tuple[dict[str, Any], int]]:
    if not seed_symbol_ids:
        return []

    sym_by_id = {
        str(symbol.get("symbol_id")): symbol
        for symbol in code_symbols
        if (repository_id is None or symbol.get("repository_id") == repository_id)
        and symbol.get("symbol_id")
    }
    symbol_id_by_name = {
        str(symbol.get("qualified_name")): symbol_id
        for symbol_id, symbol in sym_by_id.items()
        if symbol.get("qualified_name")
    }
    fact_by_symbol: dict[str, dict[str, Any]] = {}
    for fact in facts:
        sym = fact.get("symbol")
        if sym:
            fact_by_symbol.setdefault(sym, fact)

    seed_ids = {symbol_id_by_name.get(name, name) for name in seed_symbol_ids}
    related_symbol_ids: set[str] = set()
    for rel in code_relations:
        if repository_id is not None and rel.get("repository_id") != repository_id:
            continue
        src = rel.get("source_symbol_id", "")
        tgt = rel.get("target_symbol_id", "")
        if src in seed_ids:
            related_symbol_ids.add(tgt)
        if tgt in seed_ids:
            related_symbol_ids.add(src)

    related_symbol_ids.difference_update(seed_ids)
    if not related_symbol_ids:
        return []

    hits: list[tuple[dict[str, Any], str]] = []
    for sid in related_symbol_ids:
        symbol = sym_by_id.get(sid, {})
        qualified_name = str(symbol.get("qualified_name", sid))
        if qualified_name in fact_by_symbol:
            hits.append((fact_by_symbol[qualified_name], sid))

    hits.sort(key=lambda x: (x[0]["page_path"], x[0]["locator"], x[1]))
    result: list[tuple[dict[str, Any], int]] = []
    for rank, (fact, _sid) in enumerate(hits[:20], start=1):
        result.append((fact, rank))
    return result


def _cross_repository_lane(
    question: str,
    facts: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], int]]:
    names = tuple(dict.fromkeys(name.lower() for name in _HYPHENATED_NAME.findall(question)))
    if len(names) < 2:
        return []
    common_parts = set.intersection(*(set(name.split("-")) for name in names))
    hits: list[tuple[int, dict[str, Any]]] = []
    for fact in facts:
        repository_name = str(fact.get("repository_name", "")).lower()
        text = " ".join(
            str(fact.get(field, ""))
            for field in (
                "routing_text",
                "quote",
                "section_path",
                "page_path",
            )
        ).lower()
        source_names = [name for name in names if repository_name == name]
        links_named_repositories = any(
            any(part in text for part in target.split("-") if part not in common_parts)
            for source in source_names
            for target in names
            if target != source
        )
        if links_named_repositories:
            hits.append((0, fact))
        elif all(name in text for name in names):
            hits.append((1, fact))
    hits.sort(key=lambda item: (item[0], item[1]["page_path"], item[1]["locator"]))
    return [
        (fact, index + 1 if priority == 0 else index + 20)
        for index, (priority, fact) in enumerate(hits[:20])
    ]


_SOURCE_KINDS = frozenset({"code", "feishu", "conversation", "note"})


def _source_kind(
    fact: dict[str, Any],
) -> Literal["code", "feishu", "conversation", "note"]:
    value = fact.get("source_kind")
    if value in _SOURCE_KINDS:
        return cast(Literal["code", "feishu", "conversation", "note"], value)
    if fact.get("symbol") or fact.get("relation_type") or fact.get("repository_id"):
        return "code"
    return "note"


def _preferred_source_kind(
    question: str,
) -> Literal["code", "feishu", "conversation"] | None:
    lowered = question.lower()
    if any(marker in lowered for marker in _CONVERSATION_MARKERS):
        return "conversation"
    # Feishu operation markers win over a bare PascalCase mention: "配置操作步骤"
    # with a type name is a procedure lookup, not a code-location question.
    feishu_operation = any(marker in lowered for marker in _FEISHU_OPERATION_MARKERS)
    if _CODE_INTENT_MARKERS.search(question):
        return "code"
    if feishu_operation:
        return "feishu"
    identifiers = _explicit_code_identifiers(question)
    if any(
        "." in identifier or "_" in identifier or "$" in identifier or f"`{identifier}`" in question
        for identifier in identifiers
    ):
        return "code"
    if any(marker in lowered for marker in (" import ", " imports ", " dependency ")) or any(
        marker in question for marker in ("导入", "依赖", "继承", "实现", "用例")
    ):
        return "code"
    return None
