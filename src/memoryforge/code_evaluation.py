"""Deterministic baseline metrics for the code Wiki pipeline."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from memoryforge.code_index import build_code_index
from memoryforge.code_models import (
    ArchitectureEdge,
    ArchitectureGraph,
    CodeRelation,
    CodeRelationType,
    CodeSymbolKind,
    ModuleNode,
)
from memoryforge.module_planner import build_architecture_graph, build_module_plan
from memoryforge.workspace import read_source_excerpt


class SymbolExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    qualified_name: str
    kind: CodeSymbolKind


class RelationExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: Literal["core", "known_gap"]
    type: CodeRelationType
    source: str
    target: str


class ModuleExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    module_path: str


class IncrementalExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    old_text: str
    new_text: str
    expected_changed_symbols: tuple[str, ...]
    expected_changed_pages: tuple[str, ...]
    max_changed_page_ratio: float = Field(gt=0.0, le=1.0)


class CodeEvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    expected_source_paths: tuple[str, ...]
    symbols: tuple[SymbolExpectation, ...]
    relations: tuple[RelationExpectation, ...]
    modules: tuple[ModuleExpectation, ...]
    incremental: IncrementalExpectation


def run_code_evaluation(
    workspace_root: Path,
    repository_id: str,
    config_path: Path,
) -> dict[str, object]:
    """Evaluate current code facts without a model or mutable source reads."""

    suite = CodeEvaluationSuite.model_validate_json(config_path.read_text(encoding="utf-8"))
    snapshot = build_code_index(workspace_root, repository_id)
    plan = build_module_plan(snapshot)
    graph = build_architecture_graph(snapshot, plan)
    deterministic = (
        snapshot == build_code_index(workspace_root, repository_id)
        and plan == build_module_plan(snapshot)
        and graph == build_architecture_graph(snapshot, plan)
    )

    symbols = {symbol.qualified_name: symbol for symbol in snapshot.symbols}
    symbols_by_id = {symbol.symbol_id: symbol for symbol in snapshot.symbols}
    symbol_cases = [
        {
            "qualified_name": expected.qualified_name,
            "kind": expected.kind.value,
            "found": (
                expected.qualified_name in symbols
                and symbols[expected.qualified_name].kind is expected.kind
            ),
        }
        for expected in suite.symbols
    ]
    actual_relations = {
        (
            relation.type,
            symbols_by_id[relation.source_symbol_id].qualified_name,
            symbols_by_id[relation.target_symbol_id].qualified_name,
        )
        for relation in snapshot.relations
    }
    relation_cases = [
        {
            "tier": expected.tier,
            "type": expected.type.value,
            "source": expected.source,
            "target": expected.target,
            "found": (expected.type, expected.source, expected.target) in actual_relations,
        }
        for expected in suite.relations
    ]
    modules = _flatten_modules(plan.modules)
    symbol_modules = {
        symbol_id: module.path for module in modules for symbol_id in module.symbol_ids
    }
    module_cases = [
        {
            "symbol": expected.symbol,
            "module_path": expected.module_path,
            "found": (
                expected.symbol in symbols
                and symbol_modules[symbols[expected.symbol].symbol_id] == expected.module_path
            ),
        }
        for expected in suite.modules
    ]
    source_paths = {symbol.location.relative_path for symbol in snapshot.symbols}
    source_cases = [
        {"path": path, "found": path in source_paths} for path in suite.expected_source_paths
    ]
    grounding_checks = [
        hashlib.sha256(
            read_source_excerpt(
                workspace_root,
                source_id=symbol.location.source_id,
                source_version=symbol.location.source_version,
                locator=symbol.location.locator,
            ).encode()
        ).hexdigest()
        == symbol.body_sha256
        for symbol in snapshot.symbols
    ]
    grounding_checks.extend(
        bool(
            read_source_excerpt(
                workspace_root,
                source_id=evidence.source_id,
                source_version=evidence.source_version,
                locator=evidence.locator,
            )
        )
        for relation in snapshot.relations
        for evidence in relation.evidence
    )
    relation_ids = {relation.relation_id for relation in snapshot.relations}
    graph_grounded = all(set(edge.relation_ids) <= relation_ids for edge in graph.edges)
    mermaid_checks, architecture_citations, mermaid_ordering = _architecture_rendering_checks(
        workspace_root,
        graph,
        modules,
        {relation.relation_id: relation for relation in snapshot.relations},
    )
    core_relations = [case for case in relation_cases if case["tier"] == "core"]
    known_gaps = [case for case in relation_cases if case["tier"] == "known_gap"]
    return {
        "suite": suite.name,
        "schema_version": 1,
        "counts": {
            "symbols": len(snapshot.symbols),
            "relations": len(snapshot.relations),
            "modules": len(_flatten_modules(plan.modules)),
            "architecture_edges": len(graph.edges),
        },
        "metrics": {
            "expected_source_coverage": _percentage(bool(case["found"]) for case in source_cases),
            "symbol_recall": _percentage(bool(case["found"]) for case in symbol_cases),
            "core_relation_recall": _percentage(bool(case["found"]) for case in core_relations),
            "known_gap_relation_recall": _percentage(bool(case["found"]) for case in known_gaps),
            "overall_relation_recall": _percentage(bool(case["found"]) for case in relation_cases),
            "module_assignment_accuracy": _percentage(bool(case["found"]) for case in module_cases),
            "citation_grounding_accuracy": _percentage(grounding_checks),
            "architecture_edge_grounding": 100.0 if graph_grounded else 0.0,
            "mermaid_edge_coverage": _percentage(mermaid_checks),
            "architecture_citation_coverage": _percentage(architecture_citations),
            "architecture_mermaid_determinism": _percentage(mermaid_ordering),
            "deterministic_replay": 100.0 if deterministic else 0.0,
        },
        "gates": {
            "core_symbols": all(bool(case["found"]) for case in symbol_cases),
            "core_relations": all(bool(case["found"]) for case in core_relations),
            "module_assignment": all(bool(case["found"]) for case in module_cases),
            "citations": all(grounding_checks),
            "architecture_wiki": (
                all(mermaid_checks) and all(architecture_citations) and all(mermaid_ordering)
            ),
            "deterministic": deterministic,
        },
        "cases": {
            "sources": source_cases,
            "symbols": symbol_cases,
            "relations": relation_cases,
            "modules": module_cases,
        },
    }


def _architecture_rendering_checks(
    workspace_root: Path,
    graph: ArchitectureGraph,
    modules: tuple[ModuleNode, ...],
    relations: dict[str, CodeRelation],
) -> tuple[list[bool], list[bool], list[bool]]:
    modules_by_id = {module.module_id: module for module in modules}
    grouped: dict[str, list[ArchitectureEdge]] = {}
    mermaid_checks: list[bool] = []
    citation_checks: list[bool] = []
    contents: dict[str, str] = {}
    for edge in graph.edges:
        module = modules_by_id[edge.source_module_id]
        content = contents.setdefault(
            module.module_id,
            (workspace_root / module.wiki_path).read_text(encoding="utf-8"),
        )
        marker = f"%% architecture-edge:{edge.edge_id}"
        arrow = (
            f"m_{edge.source_module_id} -->|{edge.type.value} "
            f"({len(edge.relation_ids)})| m_{edge.target_module_id}"
        )
        mermaid_checks.append(content.count(marker) == 1 and arrow in content)
        grouped.setdefault(module.module_id, []).append(edge)
        for relation_id in edge.relation_ids:
            relation = relations[relation_id]
            evidence = relation.evidence[0]
            definition = (
                f"[^relation-{relation_id}]: source `{evidence.source_id}` · revision "
                f"`{evidence.source_version}` · `{evidence.locator}`"
            )
            citation_checks.append(definition in content)
    ordering_checks = []
    for module_id, edges in grouped.items():
        content = contents[module_id]
        ordered = sorted(
            edges,
            key=lambda edge: (edge.type.value, edge.target_module_id),
        )
        positions = [content.find(f"%% architecture-edge:{edge.edge_id}") for edge in ordered]
        ordering_checks.append(
            content.count("```mermaid\nflowchart LR\n") == 1
            and all(position >= 0 for position in positions)
            and positions == sorted(positions)
        )
    return mermaid_checks, citation_checks, ordering_checks


def _flatten_modules(modules: tuple[ModuleNode, ...]) -> tuple[ModuleNode, ...]:
    result: list[ModuleNode] = []
    for module in modules:
        result.append(module)
        result.extend(_flatten_modules(module.children))
    return tuple(result)


def _percentage(values: Iterable[bool]) -> float:
    checked = list(values)
    return round(100 * sum(checked) / len(checked), 1) if checked else 0.0
