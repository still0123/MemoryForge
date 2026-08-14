"""Reverse impact analysis and call-path routing over CodeIndexSnapshot."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.code.code_intelligence import SymbolRef, VisibleSource, _symbol_to_ref
from memoryforge.code.code_models import (
    CodeIndexSnapshot,
    CodeLocation,
    CodeRelation,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
)

IMPACT_TYPES = {
    CodeRelationType.CALLS,
    CodeRelationType.IMPORTS,
    CodeRelationType.IMPLEMENTS,
    CodeRelationType.EXTENDS,
}


class ImpactEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    source: SymbolRef
    target: SymbolRef
    evidence: CodeLocation
    depth: int = Field(ge=0)


def _edge_sort_key(edge: ImpactEdge):
    return (
        edge.relation_type,
        edge.depth,
        edge.source.qualified_name,
        edge.relation_id,
    )


class ImpactResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["answered", "partial", "unknown"]
    repository_id: str | None = None
    analyzed_commit: str | None = None
    target: SymbolRef | None = None
    direct: tuple[ImpactEdge, ...] = ()
    transitive: tuple[ImpactEdge, ...] = ()
    tests: tuple[SymbolRef, ...] = ()
    unknown_edges: int = 0
    truncated: bool = False
    risk: Literal["low", "medium", "high", "unknown"] = "unknown"


class CallPathResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["answered", "partial", "unknown"]
    paths: tuple[tuple[ImpactEdge, ...], ...] = ()
    truncated: bool = False


class DiffImpactResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["answered", "partial", "index_unavailable"]
    added: tuple[ImpactEdge, ...] = ()
    removed: tuple[ImpactEdge, ...] = ()
    affected_pages: tuple[str, ...] = ()


def _is_relation_visible(relation: CodeRelation, visible_source: VisibleSource) -> bool:
    for evidence in relation.evidence:
        if not visible_source(evidence.source_id, evidence.source_version):
            return False
    return True


def _find_symbol_by_identifier(snapshot: CodeIndexSnapshot, identifier: str) -> CodeSymbol | None:
    exact: list[CodeSymbol] = []
    prefix: list[CodeSymbol] = []
    short_name: list[CodeSymbol] = []
    for symbol in snapshot.symbols:
        qn = symbol.qualified_name
        if qn == identifier:
            exact.append(symbol)
        elif qn.startswith(identifier + "."):
            prefix.append(symbol)
        else:
            last_dot = qn.rfind(".")
            name_part = qn[last_dot + 1 :] if last_dot >= 0 else qn
            if name_part == identifier:
                short_name.append(symbol)
    for candidates in (exact, prefix, short_name):
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return candidates[0]
    return None


def _is_private(symbol: CodeSymbol) -> bool:
    if symbol.kind not in {CodeSymbolKind.FUNCTION, CodeSymbolKind.METHOD}:
        return False
    return "._" in symbol.qualified_name


def _module_for(symbol: CodeSymbol) -> str:
    return symbol.location.relative_path


def impact_analysis(
    snapshot: CodeIndexSnapshot,
    target: str,
    *,
    visible_source: VisibleSource,
    max_depth: int = 2,
    max_nodes: int = 50,
) -> ImpactResult:
    target_symbol = _find_symbol_by_identifier(snapshot, target)
    if target_symbol is None:
        return ImpactResult(status="unknown")

    repository_id = snapshot.repository_id
    analyzed_commit = snapshot.commit_sha
    target_ref = _symbol_to_ref(target_symbol)

    symbols_by_id = {s.symbol_id: s for s in snapshot.symbols}

    incoming_edges: dict[str, list[CodeRelation]] = {}
    tests_list: list[CodeSymbol] = []
    unknown_edges = 0

    for relation in snapshot.relations:
        if not _is_relation_visible(relation, visible_source):
            unknown_edges += 1
            continue
        rtype = relation.type
        if rtype is CodeRelationType.TESTS and relation.target_symbol_id == target_symbol.symbol_id:
            src = symbols_by_id.get(relation.source_symbol_id)
            if src is not None:
                tests_list.append(src)
            else:
                unknown_edges += 1
            continue
        if rtype not in IMPACT_TYPES:
            continue
        incoming_edges.setdefault(relation.target_symbol_id, []).append(relation)

    visited: set[str] = set()
    visited.add(target_symbol.symbol_id)
    queue: deque[tuple[str, int]] = deque()
    queue.append((target_symbol.symbol_id, 0))

    direct_edges: list[ImpactEdge] = []
    transitive_edges: list[ImpactEdge] = []
    node_count = 0
    truncated = False

    module_set: set[str] = set()
    module_set.add(_module_for(target_symbol))
    caller_count_for_risk = 0

    while queue:
        current_id, current_depth = queue.popleft()
        if current_depth >= max_depth:
            continue
        for relation in incoming_edges.get(current_id, []):
            source_id = relation.source_symbol_id
            if node_count >= max_nodes:
                truncated = True
                break
            if source_id in visited:
                continue
            visited.add(source_id)
            node_count += 1
            source_sym = symbols_by_id.get(source_id)
            if source_sym is None:
                unknown_edges += 1
                continue
            evidence = relation.evidence[0]
            edge = ImpactEdge(
                relation_id=relation.relation_id,
                relation_type=relation.type.value,
                source=_symbol_to_ref(source_sym),
                target=(
                    target_ref if current_depth == 0 else _symbol_to_ref(symbols_by_id[current_id])
                ),
                evidence=evidence,
                depth=current_depth + 1,
            )
            if current_depth == 0:
                direct_edges.append(edge)
                caller_count_for_risk += 1
            else:
                transitive_edges.append(edge)
            module_set.add(_module_for(source_sym))
            queue.append((source_id, current_depth + 1))
        if truncated:
            break

    direct_sorted = tuple(sorted(direct_edges, key=_edge_sort_key))
    transitive_sorted = tuple(sorted(transitive_edges, key=_edge_sort_key))
    tests_sorted = tuple(
        sorted(
            (_symbol_to_ref(test) for test in tests_list),
            key=lambda symbol: (symbol.qualified_name, symbol.symbol_id),
        )
    )

    risk: Literal["low", "medium", "high", "unknown"]
    total_affected = len(direct_edges) + len(transitive_edges)
    graph_incomplete = unknown_edges > 0 or not snapshot.relations

    if _is_private(target_symbol) and caller_count_for_risk < 3 and tests_list:
        base_risk: Literal["low", "medium", "high"] = "low"
    elif len(module_set) >= 4 or total_affected > 20:
        base_risk = "high"
    elif len(module_set) > 1 or not tests_list:
        base_risk = "medium"
    else:
        base_risk = "low"

    if graph_incomplete and base_risk != "high":
        risk = "medium" if base_risk == "low" else base_risk
    else:
        risk = base_risk
    if graph_incomplete:
        risk = "unknown" if risk == "low" else risk

    return ImpactResult(
        status="answered",
        repository_id=repository_id,
        analyzed_commit=analyzed_commit,
        target=target_ref,
        direct=direct_sorted,
        transitive=transitive_sorted,
        tests=tests_sorted,
        unknown_edges=unknown_edges,
        truncated=truncated,
        risk=risk,
    )


def call_paths(
    snapshot: CodeIndexSnapshot,
    start: str,
    end: str,
    *,
    visible_source: VisibleSource,
    max_depth: int = 8,
    max_paths: int = 5,
) -> CallPathResult:
    start_sym = _find_symbol_by_identifier(snapshot, start)
    end_sym = _find_symbol_by_identifier(snapshot, end)
    if start_sym is None or end_sym is None:
        return CallPathResult(status="unknown")

    symbols_by_id = {s.symbol_id: s for s in snapshot.symbols}
    outgoing: dict[str, list[CodeRelation]] = {}
    for relation in snapshot.relations:
        if relation.type is not CodeRelationType.CALLS:
            continue
        if not _is_relation_visible(relation, visible_source):
            continue
        outgoing.setdefault(relation.source_symbol_id, []).append(relation)

    start_id = start_sym.symbol_id
    end_id = end_sym.symbol_id

    paths_found: list[list[ImpactEdge]] = []
    truncated = False

    initial_path: list[ImpactEdge] = []
    queue: deque[tuple[str, list[ImpactEdge]]] = deque()
    queue.append((start_id, initial_path))

    while queue and len(paths_found) < max_paths:
        current_id, current_path = queue.popleft()
        if len(current_path) > max_depth:
            truncated = True
            continue
        if current_id == end_id and current_path:
            paths_found.append(current_path)
            continue
        if len(current_path) >= max_depth:
            continue
        rels = sorted(
            outgoing.get(current_id, []),
            key=lambda r: (r.relation_id,),
        )
        for relation in rels:
            target_id = relation.target_symbol_id
            target_sym = symbols_by_id.get(target_id)
            if target_sym is None:
                continue
            edge = ImpactEdge(
                relation_id=relation.relation_id,
                relation_type=relation.type.value,
                source=_symbol_to_ref(symbols_by_id[current_id]),
                target=_symbol_to_ref(target_sym),
                evidence=relation.evidence[0],
                depth=len(current_path) + 1,
            )
            queue.append((target_id, current_path + [edge]))

    if not paths_found:
        return CallPathResult(status="unknown", truncated=truncated)

    normalized_paths: list[tuple[ImpactEdge, ...]] = []
    for p in paths_found:
        sorted_p = tuple(sorted(p, key=lambda e: (e.relation_id,)))
        normalized_paths.append(sorted_p)
    normalized_paths.sort(key=lambda tp: (len(tp), tuple(e.relation_id for e in tp)))

    return CallPathResult(
        status="answered",
        paths=tuple(normalized_paths),
        truncated=truncated,
    )


def _snapshot_relations_within_paths(
    snapshot: CodeIndexSnapshot, changed_paths: Iterable[str]
) -> set[tuple[str, str, str]]:
    path_set = set(changed_paths)
    out: set[tuple[str, str, str]] = set()
    for rel in snapshot.relations:
        src = next((s for s in snapshot.symbols if s.symbol_id == rel.source_symbol_id), None)
        tgt = next((s for s in snapshot.symbols if s.symbol_id == rel.target_symbol_id), None)
        if src is None or tgt is None:
            continue
        if src.location.relative_path in path_set or tgt.location.relative_path in path_set:
            out.add((rel.type.value, rel.source_symbol_id, rel.target_symbol_id))
    return out


def analyze_diff(
    base: CodeIndexSnapshot,
    head: CodeIndexSnapshot,
    changed_paths: tuple[str, ...],
    *,
    visible_source: VisibleSource,
) -> DiffImpactResult:
    if base is None or head is None:
        return DiffImpactResult(status="index_unavailable")
    try:
        if not base.repository_id or not head.repository_id:
            return DiffImpactResult(status="index_unavailable")
    except Exception:
        return DiffImpactResult(status="index_unavailable")

    base_set = _snapshot_relations_within_paths(base, changed_paths)
    head_set = _snapshot_relations_within_paths(head, changed_paths)

    added_keys = head_set - base_set
    removed_keys = base_set - head_set

    symbols_by_id_head = {s.symbol_id: s for s in head.symbols}
    symbols_by_id_base = {s.symbol_id: s for s in base.symbols}

    added_edges: list[ImpactEdge] = []
    for rel in head.relations:
        key = (rel.type.value, rel.source_symbol_id, rel.target_symbol_id)
        if key not in added_keys:
            continue
        src = symbols_by_id_head.get(rel.source_symbol_id)
        tgt = symbols_by_id_head.get(rel.target_symbol_id)
        if src is None or tgt is None:
            continue
        if not _is_relation_visible(rel, visible_source):
            continue
        edge = ImpactEdge(
            relation_id=rel.relation_id,
            relation_type=rel.type.value,
            source=_symbol_to_ref(src),
            target=_symbol_to_ref(tgt),
            evidence=rel.evidence[0],
            depth=1,
        )
        added_edges.append(edge)

    removed_edges: list[ImpactEdge] = []
    for rel in base.relations:
        key = (rel.type.value, rel.source_symbol_id, rel.target_symbol_id)
        if key not in removed_keys:
            continue
        src = symbols_by_id_base.get(rel.source_symbol_id)
        tgt = symbols_by_id_base.get(rel.target_symbol_id)
        if src is None or tgt is None:
            continue
        if not _is_relation_visible(rel, visible_source):
            continue
        edge = ImpactEdge(
            relation_id=rel.relation_id,
            relation_type=rel.type.value,
            source=_symbol_to_ref(src),
            target=_symbol_to_ref(tgt),
            evidence=rel.evidence[0],
            depth=1,
        )
        removed_edges.append(edge)

    affected_path_set: set[str] = set(changed_paths)
    for rel_key in added_keys | removed_keys:
        _, src_id, tgt_id = rel_key
        src_h = symbols_by_id_head.get(src_id)
        tgt_h = symbols_by_id_head.get(tgt_id)
        if src_h is not None:
            affected_path_set.add(src_h.location.relative_path)
        if tgt_h is not None:
            affected_path_set.add(tgt_h.location.relative_path)
        src_b = symbols_by_id_base.get(src_id)
        tgt_b = symbols_by_id_base.get(tgt_id)
        if src_b is not None:
            affected_path_set.add(src_b.location.relative_path)
        if tgt_b is not None:
            affected_path_set.add(tgt_b.location.relative_path)

    return DiffImpactResult(
        status="answered",
        added=tuple(sorted(added_edges, key=_edge_sort_key)),
        removed=tuple(sorted(removed_edges, key=_edge_sort_key)),
        affected_pages=tuple(sorted(affected_path_set)),
    )
