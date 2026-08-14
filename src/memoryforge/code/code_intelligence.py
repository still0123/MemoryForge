"""Symbol context resolution for CodeIndexSnapshot lookups."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.code.code_models import (
    CodeIndexSnapshot,
    CodeLocation,
    CodeRelation,
    CodeRelationType,
    CodeSymbol,
)

VisibleSource = Callable[[str, int], bool]


class SymbolRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol_id: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    path: str = Field(min_length=1)
    location: CodeLocation


def _symbol_to_ref(symbol: CodeSymbol) -> SymbolRef:
    return SymbolRef(
        symbol_id=symbol.symbol_id,
        qualified_name=symbol.qualified_name,
        kind=symbol.kind.value,
        path=symbol.location.relative_path,
        location=symbol.location,
    )


class SymbolContextResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["answered", "partial", "unknown"]
    symbol: SymbolRef | None = None
    module: SymbolRef | None = None
    package: SymbolRef | None = None
    direct_callers: tuple[SymbolRef, ...] = ()
    direct_callees: tuple[SymbolRef, ...] = ()
    imports: tuple[SymbolRef, ...] = ()
    implements_extends: tuple[SymbolRef, ...] = ()
    related_tests: tuple[SymbolRef, ...] = ()
    ambiguous: tuple[SymbolRef, ...] = ()
    unknown_edges: int = 0


def _is_relation_visible(relation: CodeRelation, visible_source: VisibleSource) -> bool:
    for evidence in relation.evidence:
        if not visible_source(evidence.source_id, evidence.source_version):
            return False
    return True


def _find_matches(
    snapshot: CodeIndexSnapshot, identifier: str
) -> list[CodeSymbol]:
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
            if last_dot >= 0:
                name_part = qn[last_dot + 1 :]
            else:
                name_part = qn
            if name_part == identifier:
                short_name.append(symbol)
    if exact:
        return exact
    if prefix:
        return prefix
    return short_name


def _find_container(
    snapshot: CodeIndexSnapshot, symbol: CodeSymbol, kinds: set[str]
) -> SymbolRef | None:
    target_path = symbol.location.relative_path
    for rel in snapshot.relations:
        if rel.type is not CodeRelationType.CONTAINS:
            continue
        if rel.target_symbol_id != symbol.symbol_id:
            continue
        src = next((s for s in snapshot.symbols if s.symbol_id == rel.source_symbol_id), None)
        if src is not None and src.kind.value in kinds:
            return _symbol_to_ref(src)
    for s in snapshot.symbols:
        if s.location.relative_path == target_path and s.kind.value in kinds:
            return _symbol_to_ref(s)
    return None


def symbol_context(
    snapshot: CodeIndexSnapshot,
    identifier: str,
    *,
    visible_source: VisibleSource,
    max_relations: int = 20,
) -> SymbolContextResult:
    matches = _find_matches(snapshot, identifier)
    if not matches:
        return SymbolContextResult(status="unknown")
    if len(matches) > 1:
        ambiguous = tuple(_symbol_to_ref(m) for m in matches)
        return SymbolContextResult(status="partial", ambiguous=ambiguous)

    target = matches[0]
    symbol_ref = _symbol_to_ref(target)
    unknown_edges = 0

    symbols_by_id = {s.symbol_id: s for s in snapshot.symbols}

    direct_callers: list[SymbolRef] = []
    direct_callees: list[SymbolRef] = []
    imports: list[SymbolRef] = []
    implements_extends: list[SymbolRef] = []
    related_tests: list[SymbolRef] = []

    caller_edge_count = 0
    callee_edge_count = 0
    imports_edge_count = 0
    impl_edge_count = 0
    tests_edge_count = 0

    for relation in snapshot.relations:
        if not _is_relation_visible(relation, visible_source):
            unknown_edges += 1
            continue
        rtype = relation.type

        if relation.target_symbol_id == target.symbol_id:
            src = symbols_by_id.get(relation.source_symbol_id)
            if src is None:
                unknown_edges += 1
                continue
            if rtype is CodeRelationType.CALLS and caller_edge_count < max_relations:
                direct_callers.append(_symbol_to_ref(src))
                caller_edge_count += 1
            elif rtype is CodeRelationType.IMPORTS and imports_edge_count < max_relations:
                imports.append(_symbol_to_ref(src))
                imports_edge_count += 1
            elif rtype in {CodeRelationType.IMPLEMENTS, CodeRelationType.EXTENDS} and impl_edge_count < max_relations:
                implements_extends.append(_symbol_to_ref(src))
                impl_edge_count += 1
            elif rtype is CodeRelationType.TESTS and tests_edge_count < max_relations:
                related_tests.append(_symbol_to_ref(src))
                tests_edge_count += 1

        if relation.source_symbol_id == target.symbol_id:
            tgt = symbols_by_id.get(relation.target_symbol_id)
            if tgt is None:
                unknown_edges += 1
                continue
            if rtype is CodeRelationType.CALLS and callee_edge_count < max_relations:
                direct_callees.append(_symbol_to_ref(tgt))
                callee_edge_count += 1

    def _sort_key(s: SymbolRef):
        return (s.qualified_name, s.symbol_id)

    module_ref = _find_container(snapshot, target, {"module"})
    package_ref = _find_container(snapshot, target, {"package"})

    return SymbolContextResult(
        status="answered",
        symbol=symbol_ref,
        module=module_ref,
        package=package_ref,
        direct_callers=tuple(sorted(direct_callers, key=_sort_key)),
        direct_callees=tuple(sorted(direct_callees, key=_sort_key)),
        imports=tuple(sorted(imports, key=_sort_key)),
        implements_extends=tuple(sorted(implements_extends, key=_sort_key)),
        related_tests=tuple(sorted(related_tests, key=_sort_key)),
        unknown_edges=unknown_edges,
    )
