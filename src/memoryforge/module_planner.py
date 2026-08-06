"""Deterministic module planning and architecture graph aggregation."""

from __future__ import annotations

import hashlib
import heapq
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from memoryforge.code_models import (
    ArchitectureEdge,
    ArchitectureGraph,
    ArchitectureNode,
    CodeIndexSnapshot,
    CodeLanguage,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
    ModuleNode,
    ModulePlan,
    make_architecture_edge_id,
    make_architecture_graph_id,
    make_code_wiki_path,
    make_module_id,
    make_module_plan_id,
)

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_MULTIPLE_HYPHENS = re.compile(r"-{2,}")


class ModulePlanningError(ValueError):
    """Raised when a code snapshot cannot produce a complete module plan."""


@dataclass
class _ModuleDraft:
    raw_name: str
    raw_path: str
    symbol_ids: list[str] = field(default_factory=list)
    children: dict[str, _ModuleDraft] = field(default_factory=dict)


def build_module_plan(snapshot: CodeIndexSnapshot) -> ModulePlan:
    """Assign every indexed symbol to a deterministic hierarchical module."""

    if not snapshot.symbols:
        raise ModulePlanningError("cannot plan modules for an empty code index")

    package_names = {
        symbol.location.relative_path: symbol.display_name
        for symbol in snapshot.symbols
        if symbol.kind is CodeSymbolKind.PACKAGE
    }
    symbol_to_raw_module: dict[str, str] = {}
    grouped_symbols: dict[str, list[str]] = defaultdict(list)
    for symbol in snapshot.symbols:
        raw_path = _raw_module_path(symbol, package_names)
        symbol_to_raw_module[symbol.symbol_id] = raw_path
        grouped_symbols[raw_path].append(symbol.symbol_id)

    roots = _build_draft_tree(grouped_symbols)
    slug_paths = _assign_slug_paths(roots)
    module_dependencies = {
        (
            symbol_to_raw_module[relation.source_symbol_id],
            symbol_to_raw_module[relation.target_symbol_id],
        )
        for relation in snapshot.relations
        if relation.type is not CodeRelationType.CONTAINS
        and symbol_to_raw_module[relation.source_symbol_id]
        != symbol_to_raw_module[relation.target_symbol_id]
    }
    symbol_order = {
        symbol.symbol_id: (
            symbol.location.relative_path,
            symbol.location.start_line,
            symbol.qualified_name,
        )
        for symbol in snapshot.symbols
    }
    modules = tuple(
        _render_module(
            root,
            repository_id=snapshot.repository_id,
            slug_paths=slug_paths,
            dependencies=module_dependencies,
            symbol_order=symbol_order,
        )
        for root in _ordered_drafts("", roots, module_dependencies)
    )
    return ModulePlan(
        plan_id=make_module_plan_id(snapshot.index_id),
        repository_id=snapshot.repository_id,
        commit_sha=snapshot.commit_sha,
        code_index_id=snapshot.index_id,
        symbol_ids=tuple(symbol.symbol_id for symbol in snapshot.symbols),
        modules=modules,
    )


def build_architecture_graph(
    snapshot: CodeIndexSnapshot,
    plan: ModulePlan,
) -> ArchitectureGraph:
    """Aggregate concrete CodeRelations into evidence-bound module edges."""

    if (
        plan.repository_id != snapshot.repository_id
        or plan.commit_sha != snapshot.commit_sha
        or plan.code_index_id != snapshot.index_id
    ):
        raise ModulePlanningError("module plan does not belong to the code index")

    modules = _flatten_modules(plan.modules)
    symbol_to_module = {symbol_id: module for module in modules for symbol_id in module.symbol_ids}
    if set(symbol_to_module) != {symbol.symbol_id for symbol in snapshot.symbols}:
        raise ModulePlanningError("module plan does not cover the code index symbols")

    grouped_relations: dict[
        tuple[CodeRelationType, str, str],
        list[str],
    ] = defaultdict(list)
    for relation in snapshot.relations:
        source_module = symbol_to_module[relation.source_symbol_id]
        target_module = symbol_to_module[relation.target_symbol_id]
        if source_module.module_id == target_module.module_id:
            continue
        grouped_relations[
            (
                relation.type,
                source_module.module_id,
                target_module.module_id,
            )
        ].append(relation.relation_id)

    edges = []
    for (relation_type, source_id, target_id), relation_ids in sorted(
        grouped_relations.items(),
        key=lambda item: (item[0][0].value, item[0][1], item[0][2]),
    ):
        evidence = tuple(sorted(set(relation_ids)))
        edges.append(
            ArchitectureEdge(
                edge_id=make_architecture_edge_id(
                    snapshot.repository_id,
                    relation_type,
                    source_id,
                    target_id,
                    evidence,
                ),
                repository_id=snapshot.repository_id,
                type=relation_type,
                source_module_id=source_id,
                target_module_id=target_id,
                relation_ids=evidence,
            )
        )

    nodes = tuple(
        ArchitectureNode(
            module_id=module.module_id,
            repository_id=snapshot.repository_id,
            path=module.path,
            label=module.title,
            wiki_path=module.wiki_path,
        )
        for module in sorted(modules, key=lambda item: item.path)
    )
    return ArchitectureGraph(
        graph_id=make_architecture_graph_id(snapshot.index_id, plan.plan_id),
        repository_id=snapshot.repository_id,
        commit_sha=snapshot.commit_sha,
        code_index_id=snapshot.index_id,
        module_plan_id=plan.plan_id,
        nodes=nodes,
        edges=tuple(edges),
    )


def _raw_module_path(
    symbol: CodeSymbol,
    package_names: dict[str, str],
) -> str:
    path = PurePosixPath(symbol.location.relative_path)
    if symbol.language is CodeLanguage.GO:
        if path.parent.parts:
            return path.parent.as_posix()
        return package_names.get(symbol.location.relative_path, path.stem)

    without_suffix = path.with_suffix("")
    parts = list(without_suffix.parts)
    if (symbol.language is CodeLanguage.PYTHON and parts and parts[-1] == "__init__") or (
        symbol.language is CodeLanguage.TYPESCRIPT and parts and parts[-1] == "index"
    ):
        parts.pop()
    return "/".join(parts) if parts else "root"


def _build_draft_tree(
    grouped_symbols: dict[str, list[str]],
) -> dict[str, _ModuleDraft]:
    roots: dict[str, _ModuleDraft] = {}
    for raw_path in sorted(grouped_symbols):
        parts = raw_path.split("/")
        current_children = roots
        current_path: list[str] = []
        draft: _ModuleDraft | None = None
        for part in parts:
            current_path.append(part)
            full_path = "/".join(current_path)
            draft = current_children.setdefault(
                part,
                _ModuleDraft(raw_name=part, raw_path=full_path),
            )
            current_children = draft.children
        if draft is None:
            raise ModulePlanningError("module path produced no hierarchy")
        draft.symbol_ids.extend(grouped_symbols[raw_path])
    return roots


def _assign_slug_paths(roots: dict[str, _ModuleDraft]) -> dict[str, str]:
    paths: dict[str, str] = {}

    def assign(children: dict[str, _ModuleDraft], parent_slug: str) -> None:
        base_counts: dict[str, int] = defaultdict(int)
        for child in children.values():
            base_counts[_slug_segment(child.raw_name)] += 1

        used: set[str] = set()
        for child in sorted(children.values(), key=lambda item: item.raw_path):
            base = _slug_segment(child.raw_name)
            candidate = base
            if base_counts[base] > 1:
                candidate = f"{base}-{_short_hash(child.raw_path)}"
            if candidate in used:
                candidate = f"{base}-{_short_hash(child.raw_path, length=12)}"
            if candidate in used:
                raise ModulePlanningError("module slug collision could not be resolved")
            used.add(candidate)
            slug_path = f"{parent_slug}/{candidate}" if parent_slug else candidate
            paths[child.raw_path] = slug_path
            assign(child.children, slug_path)

    assign(roots, "")
    return paths


def _render_module(
    draft: _ModuleDraft,
    *,
    repository_id: str,
    slug_paths: dict[str, str],
    dependencies: set[tuple[str, str]],
    symbol_order: dict[str, tuple[str, int, str]],
) -> ModuleNode:
    path = slug_paths[draft.raw_path]
    children = tuple(
        _render_module(
            child,
            repository_id=repository_id,
            slug_paths=slug_paths,
            dependencies=dependencies,
            symbol_order=symbol_order,
        )
        for child in _ordered_drafts(
            draft.raw_path,
            draft.children,
            dependencies,
        )
    )
    return ModuleNode(
        module_id=make_module_id(repository_id, path),
        repository_id=repository_id,
        path=path,
        title=_module_title(draft.raw_name),
        summary=f"Code symbols from `{draft.raw_path}`.",
        wiki_path=make_code_wiki_path(repository_id, path),
        symbol_ids=tuple(sorted(draft.symbol_ids, key=symbol_order.__getitem__)),
        children=children,
    )


def _ordered_drafts(
    parent_path: str,
    children: dict[str, _ModuleDraft],
    dependencies: set[tuple[str, str]],
) -> tuple[_ModuleDraft, ...]:
    if len(children) < 2:
        return tuple(children.values())

    child_by_path = {child.raw_path: child for child in children.values()}
    graph: dict[str, set[str]] = {path: set() for path in child_by_path}
    indegree = {path: 0 for path in child_by_path}
    for dependent_raw, dependency_raw in dependencies:
        dependent = _direct_child(parent_path, dependent_raw)
        dependency = _direct_child(parent_path, dependency_raw)
        if (
            dependent is None
            or dependency is None
            or dependent == dependency
            or dependent not in graph
            or dependency not in graph
            or dependent in graph[dependency]
        ):
            continue
        graph[dependency].add(dependent)
        indegree[dependent] += 1

    ready = [path for path, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        current = heapq.heappop(ready)
        ordered.append(current)
        for dependent in sorted(graph[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    ordered.extend(sorted(set(child_by_path) - set(ordered)))
    return tuple(child_by_path[path] for path in ordered)


def _direct_child(parent_path: str, raw_path: str) -> str | None:
    if parent_path:
        prefix = parent_path + "/"
        if not raw_path.startswith(prefix):
            return None
        remainder = raw_path[len(prefix) :]
        return prefix + remainder.split("/", 1)[0]
    return raw_path.split("/", 1)[0]


def _flatten_modules(modules: tuple[ModuleNode, ...]) -> tuple[ModuleNode, ...]:
    flattened: list[ModuleNode] = []

    def collect(module: ModuleNode) -> None:
        flattened.append(module)
        for child in module.children:
            collect(child)

    for module in modules:
        collect(module)
    return tuple(flattened)


def _slug_segment(value: str) -> str:
    normalized = _NON_SLUG.sub("-", value.lower()).strip("-")
    normalized = _MULTIPLE_HYPHENS.sub("-", normalized)
    return normalized or "module"


def _module_title(value: str) -> str:
    words = [word for word in re.split(r"[^A-Za-z0-9]+", value) if word]
    return " ".join(word[:1].upper() + word[1:] for word in words) or "Module"


def _short_hash(value: str, *, length: int = 8) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]
