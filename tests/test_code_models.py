from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from memoryforge.code_models import (
    ArchitectureEdge,
    ArchitectureGraph,
    ArchitectureNode,
    CodeIndexSnapshot,
    CodeLanguage,
    CodeLocation,
    CodeRelation,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
    ModuleNode,
    ModulePlan,
    make_architecture_edge_id,
    make_architecture_graph_id,
    make_code_index_id,
    make_code_relation_id,
    make_code_symbol_id,
    make_module_id,
    make_module_plan_id,
)

REPOSITORY_ID = "a" * 64
COMMIT_SHA = "b" * 40
SOURCE_ID = "c" * 64
CONTENT_SHA256 = "d" * 64


def test_code_contracts_round_trip_as_strict_json() -> None:
    handler = _symbol(
        path="src/api.py",
        qualified_name="api.handle",
        display_name="handle",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:0-31",
        lines=(1, 2),
    )
    service = _symbol(
        path="src/service.py",
        qualified_name="service.run",
        display_name="run",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:32-66",
        lines=(3, 4),
    )
    relation = _relation(handler, service)
    snapshot = CodeIndexSnapshot(
        index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        languages=(CodeLanguage.PYTHON,),
        source_versions={SOURCE_ID: 1},
        symbols=(handler, service),
        relations=(relation,),
    )

    api_module = _module("api", (handler.symbol_id,))
    runtime_module = _module("runtime", (service.symbol_id,))
    plan = ModulePlan(
        plan_id=make_module_plan_id(snapshot.index_id),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        code_index_id=snapshot.index_id,
        symbol_ids=(handler.symbol_id, service.symbol_id),
        modules=(api_module, runtime_module),
    )
    edge = ArchitectureEdge(
        edge_id=make_architecture_edge_id(
            REPOSITORY_ID,
            CodeRelationType.CALLS,
            api_module.module_id,
            runtime_module.module_id,
            (relation.relation_id,),
        ),
        repository_id=REPOSITORY_ID,
        type=CodeRelationType.CALLS,
        source_module_id=api_module.module_id,
        target_module_id=runtime_module.module_id,
        relation_ids=(relation.relation_id,),
    )
    graph = ArchitectureGraph(
        graph_id=make_architecture_graph_id(snapshot.index_id, plan.plan_id),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        code_index_id=snapshot.index_id,
        module_plan_id=plan.plan_id,
        nodes=(
            _architecture_node(api_module),
            _architecture_node(runtime_module),
        ),
        edges=(edge,),
    )

    assert CodeIndexSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert ModulePlan.model_validate_json(plan.model_dump_json()) == plan
    assert ArchitectureGraph.model_validate_json(graph.model_dump_json()) == graph


def test_symbol_identity_signature_and_path_are_fail_closed() -> None:
    with pytest.raises(ValidationError, match="canonical symbol identity"):
        _symbol(symbol_id="0" * 64)

    with pytest.raises(ValidationError, match="signature_sha256"):
        _symbol(signature_sha256="0" * 64)

    with pytest.raises(ValidationError, match="canonical repository-relative"):
        _symbol(path="../src/api.py")


def test_snapshot_rejects_dangling_relations_and_source_version_drift() -> None:
    handler = _symbol()
    unknown = _symbol(
        path="src/unknown.py",
        qualified_name="unknown.run",
        display_name="run",
    )
    relation = _relation(handler, unknown)

    with pytest.raises(ValidationError, match="unknown symbol"):
        CodeIndexSnapshot(
            index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
            repository_id=REPOSITORY_ID,
            commit_sha=COMMIT_SHA,
            languages=(CodeLanguage.PYTHON,),
            source_versions={SOURCE_ID: 1},
            symbols=(handler,),
            relations=(relation,),
        )

    drifted = handler.model_copy(
        update={"location": handler.location.model_copy(update={"source_version": 2})}
    )
    with pytest.raises(ValidationError, match="snapshot source version"):
        CodeIndexSnapshot(
            index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
            repository_id=REPOSITORY_ID,
            commit_sha=COMMIT_SHA,
            languages=(CodeLanguage.PYTHON,),
            source_versions={SOURCE_ID: 1},
            symbols=(drifted,),
        )


def test_module_plan_requires_unique_and_complete_symbol_ownership() -> None:
    first = _symbol()
    second = _symbol(
        path="src/service.py",
        qualified_name="service.run",
        display_name="run",
    )
    index_id = make_code_index_id(REPOSITORY_ID, COMMIT_SHA)
    duplicate_owner = _module("runtime", (first.symbol_id, second.symbol_id))
    second_owner = _module("api", (second.symbol_id,))

    with pytest.raises(ValidationError, match="multiple modules"):
        ModulePlan(
            plan_id=make_module_plan_id(index_id),
            repository_id=REPOSITORY_ID,
            commit_sha=COMMIT_SHA,
            code_index_id=index_id,
            symbol_ids=(first.symbol_id, second.symbol_id),
            modules=(duplicate_owner, second_owner),
        )

    with pytest.raises(ValidationError, match="every declared symbol"):
        ModulePlan(
            plan_id=make_module_plan_id(index_id),
            repository_id=REPOSITORY_ID,
            commit_sha=COMMIT_SHA,
            code_index_id=index_id,
            symbol_ids=(first.symbol_id, second.symbol_id),
            modules=(_module("runtime", (first.symbol_id,)),),
        )


def test_architecture_graph_rejects_unbound_module_edges() -> None:
    symbol = _symbol()
    module = _module("runtime", (symbol.symbol_id,))
    relation_id = "e" * 64
    unknown_module_id = "f" * 64
    edge = ArchitectureEdge(
        edge_id=make_architecture_edge_id(
            REPOSITORY_ID,
            CodeRelationType.CALLS,
            module.module_id,
            unknown_module_id,
            (relation_id,),
        ),
        repository_id=REPOSITORY_ID,
        type=CodeRelationType.CALLS,
        source_module_id=module.module_id,
        target_module_id=unknown_module_id,
        relation_ids=(relation_id,),
    )
    index_id = make_code_index_id(REPOSITORY_ID, COMMIT_SHA)
    plan_id = make_module_plan_id(index_id)

    with pytest.raises(ValidationError, match="unknown module"):
        ArchitectureGraph(
            graph_id=make_architecture_graph_id(index_id, plan_id),
            repository_id=REPOSITORY_ID,
            commit_sha=COMMIT_SHA,
            code_index_id=index_id,
            module_plan_id=plan_id,
            nodes=(_architecture_node(module),),
            edges=(edge,),
        )


def _location(
    *,
    path: str = "src/api.py",
    locator: str = "chars:0-31",
    lines: tuple[int, int] = (1, 2),
) -> CodeLocation:
    return CodeLocation(
        source_id=SOURCE_ID,
        source_version=1,
        content_sha256=CONTENT_SHA256,
        relative_path=path,
        locator=locator,
        start_line=lines[0],
        end_line=lines[1],
    )


def _symbol(
    *,
    path: str = "src/api.py",
    qualified_name: str = "api.handle",
    display_name: str = "handle",
    kind: CodeSymbolKind = CodeSymbolKind.FUNCTION,
    locator: str = "chars:0-31",
    lines: tuple[int, int] = (1, 2),
    symbol_id: str | None = None,
    signature_sha256: str | None = None,
) -> CodeSymbol:
    signature = f"def {display_name}() -> None"
    return CodeSymbol(
        symbol_id=symbol_id
        or make_code_symbol_id(
            REPOSITORY_ID,
            path,
            CodeLanguage.PYTHON,
            kind,
            qualified_name,
        ),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        language=CodeLanguage.PYTHON,
        kind=kind,
        qualified_name=qualified_name,
        display_name=display_name,
        signature=signature,
        signature_sha256=signature_sha256 or hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(f"{signature}\n    pass\n".encode()).hexdigest(),
        location=_location(path=path, locator=locator, lines=lines),
    )


def _relation(source: CodeSymbol, target: CodeSymbol) -> CodeRelation:
    return CodeRelation(
        relation_id=make_code_relation_id(
            REPOSITORY_ID,
            CodeRelationType.CALLS,
            source.symbol_id,
            target.symbol_id,
        ),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        type=CodeRelationType.CALLS,
        source_symbol_id=source.symbol_id,
        target_symbol_id=target.symbol_id,
        evidence=(source.location,),
    )


def _module(path: str, symbol_ids: tuple[str, ...]) -> ModuleNode:
    return ModuleNode(
        module_id=make_module_id(REPOSITORY_ID, path),
        repository_id=REPOSITORY_ID,
        path=path,
        title=path.title(),
        summary=f"{path.title()} module.",
        wiki_path=f"wiki/pages/code/{path}.md",
        symbol_ids=symbol_ids,
    )


def _architecture_node(module: ModuleNode) -> ArchitectureNode:
    return ArchitectureNode(
        module_id=module.module_id,
        repository_id=module.repository_id,
        path=module.path,
        label=module.title,
        wiki_path=module.wiki_path,
    )
