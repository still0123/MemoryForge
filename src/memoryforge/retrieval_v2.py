from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal, Optional, TYPE_CHECKING

from memoryforge.retrieval_models import (
    RetrievalCandidate,
    RetrievalResult,
    VisibleSource,
)

if TYPE_CHECKING:
    from memoryforge.semantic_index import SemanticIndex

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\w+", re.UNICODE)
_IDENT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_WORD = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)


def retrieve_candidates(
    workspace: Path,
    question: str,
    *,
    repository_id: str,
    visible_source: VisibleSource,
    max_pages: int = 3,
    semantic_index: Optional["SemanticIndex"] = None,
    wiki_facts: Optional[list[dict[str, Any]]] = None,
    code_symbols: Optional[list[dict[str, Any]]] = None,
    code_relations: Optional[list[dict[str, Any]]] = None,
) -> RetrievalResult:
    if wiki_facts is None:
        wiki_facts = []
    if code_symbols is None:
        code_symbols = []
    if code_relations is None:
        code_relations = []

    facts = [f for f in wiki_facts if f.get("repository_id") in (None, repository_id)]

    routes: list[str] = []

    exact_hits = _exact_lane(question, facts, repository_id)
    if exact_hits:
        routes.append("exact")

    lexical_hits = _lexical_lane(question, facts)
    if lexical_hits:
        routes.append("lexical")

    semantic_hits: list[tuple[dict[str, Any], int]] = []
    semantic_status: Literal["used", "disabled", "unavailable", "stale"] = "disabled"
    if semantic_index is None:
        semantic_status = "disabled"
    elif not semantic_index.available():
        semantic_status = "unavailable"
    else:
        semantic_status = "used"
        routes.append("semantic")
        search_results = semantic_index.search(question, k=20)
        fact_by_id = {_fact_object_id(f): f for f in facts}
        for rank, (obj_id, _score) in enumerate(search_results, start=1):
            if obj_id in fact_by_id:
                semantic_hits.append((fact_by_id[obj_id], rank))

    symbol_ids_from_hits: set[str] = set()
    for fact, _rank in exact_hits + lexical_hits + semantic_hits:
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
    semantic_filtered = _filter_visible(semantic_hits)
    relation_filtered = _filter_visible(relation_hits)

    dedup: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    exact_ranks: dict[tuple[str, str, int, str], int] = {}
    lexical_ranks: dict[tuple[str, str, int, str], int] = {}
    semantic_ranks: dict[tuple[str, str, int, str], int] = {}
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

    for fact, rank in semantic_filtered:
        key = (fact["page_path"], fact["source_id"], fact["source_version"], fact["locator"])
        if key not in dedup:
            dedup[key] = fact
        if key not in semantic_ranks or rank < semantic_ranks[key]:
            semantic_ranks[key] = rank

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
        semantic_rank = semantic_ranks.get(key)
        relation_rank = relation_ranks.get(key)

        fused_score = 0.0
        if exact_rank is not None:
            fused_score += 1.0 / (60.0 + exact_rank)
        if lexical_rank is not None:
            fused_score += 1.0 / (60.0 + lexical_rank)
        if semantic_rank is not None:
            fused_score += 1.0 / (60.0 + semantic_rank)
        if relation_rank is not None:
            fused_score += 1.0 / (60.0 + relation_rank)
        if key in exact_full_hit_keys:
            fused_score += 0.02

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
                exact_rank=exact_rank,
                lexical_rank=lexical_rank,
                semantic_rank=semantic_rank,
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
            seen_pages.add(cand.page_path)
            page_count += 1
            if page_count > max_pages:
                continue
        final.append(cand)

    return RetrievalResult(
        candidates=tuple(final),
        routes=tuple(routes),
        semantic_status=semantic_status,
    )


def _fact_object_id(fact: dict[str, Any]) -> str:
    return f"{fact['page_path']}|{fact['source_id']}|{fact['source_version']}|{fact['locator']}"


def _extract_identifier_tokens(text: str) -> list[str]:
    tokens = _IDENT_TOKEN.findall(text)
    return [t for t in tokens if len(t) >= 2]


def _exact_lane(
    question: str,
    facts: list[dict[str, Any]],
    repository_id: str,
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
    tokens: list[str] = []
    for m in _WORD.finditer(text.lower()):
        tokens.append(m.group(0))
    return tokens


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


def _relation_lane(
    seed_symbol_ids: set[str],
    facts: list[dict[str, Any]],
    code_symbols: list[dict[str, Any]],
    code_relations: list[dict[str, Any]],
    repository_id: str,
) -> list[tuple[dict[str, Any], int]]:
    if not seed_symbol_ids:
        return []

    sym_by_id = {
        str(symbol.get("symbol_id")): symbol
        for symbol in code_symbols
        if symbol.get("repository_id") == repository_id and symbol.get("symbol_id")
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

    seed_ids = {
        symbol_id_by_name.get(name, name)
        for name in seed_symbol_ids
    }
    related_symbol_ids: set[str] = set()
    for rel in code_relations:
        if rel.get("repository_id") != repository_id:
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
