from __future__ import annotations

import hashlib

import pytest

from memoryforge.code_models import (
    ArchitectureGraph,
    CodeIndexSnapshot,
    CodeLanguage,
    CodeLocation,
    CodeRelation,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
    make_code_index_id,
    make_code_relation_id,
    make_code_symbol_id,
    make_code_wiki_path,
)
from memoryforge.module_planner import (
    ModulePlanningError,
    build_architecture_graph,
    build_module_plan,
)

REPOSITORY_ID = "a" * 64
COMMIT_SHA = "b" * 40


def test_module_plan_builds_dependency_ordered_mixed_language_hierarchy() -> None:
    python_module = _symbol(
        "src/api.py",
        CodeLanguage.PYTHON,
        CodeSymbolKind.MODULE,
        "src.api",
        "api",
    )
    python_handler = _symbol(
        "src/api.py",
        CodeLanguage.PYTHON,
        CodeSymbolKind.FUNCTION,
        "src.api.handle",
        "handle",
    )
    ts_module = _symbol(
        "src/web/index.ts",
        CodeLanguage.TYPESCRIPT,
        CodeSymbolKind.MODULE,
        "src.web.index",
        "index",
    )
    ts_render = _symbol(
        "src/web/index.ts",
        CodeLanguage.TYPESCRIPT,
        CodeSymbolKind.FUNCTION,
        "src.web.index.render",
        "render",
    )
    go_package = _symbol(
        "internal/meter/model.go",
        CodeLanguage.GO,
        CodeSymbolKind.PACKAGE,
        "internal.meter@model.go",
        "meter",
    )
    go_record = _symbol(
        "internal/meter/model.go",
        CodeLanguage.GO,
        CodeSymbolKind.FUNCTION,
        "internal.meter.Record",
        "Record",
    )
    relations = (
        _relation(python_module, ts_render),
        _relation(python_handler, ts_render),
        _relation(ts_render, go_record),
    )
    snapshot = _snapshot(
        (
            python_module,
            python_handler,
            ts_module,
            ts_render,
            go_package,
            go_record,
        ),
        relations,
    )

    plan = build_module_plan(snapshot)

    assert build_module_plan(snapshot) == plan
    assert [module.path for module in plan.modules] == ["internal", "src"]
    internal, src = plan.modules
    assert [child.path for child in internal.children] == ["internal/meter"]
    assert [child.path for child in src.children] == ["src/web", "src/api"]
    assert set(internal.children[0].symbol_ids) == {
        go_package.symbol_id,
        go_record.symbol_id,
    }
    assert set(src.children[0].symbol_ids) == {
        ts_module.symbol_id,
        ts_render.symbol_id,
    }
    assert set(src.children[1].symbol_ids) == {
        python_module.symbol_id,
        python_handler.symbol_id,
    }

    graph = build_architecture_graph(snapshot, plan)
    assert {node.path for node in graph.nodes} == {
        "internal",
        "internal/meter",
        "src",
        "src/api",
        "src/web",
    }
    api_to_web = next(
        edge
        for edge in graph.edges
        if _module_path(graph, edge.source_module_id) == "src/api"
        and _module_path(graph, edge.target_module_id) == "src/web"
    )
    assert api_to_web.type is CodeRelationType.CALLS
    assert api_to_web.relation_ids == tuple(
        sorted((relations[0].relation_id, relations[1].relation_id))
    )
    assert all(edge.relation_ids for edge in graph.edges)


def test_module_plan_disambiguates_colliding_slug_segments() -> None:
    first = _symbol(
        "pkg/foo_bar.py",
        CodeLanguage.PYTHON,
        CodeSymbolKind.MODULE,
        "pkg.foo_bar",
        "foo_bar",
    )
    second = _symbol(
        "pkg/foo-bar.py",
        CodeLanguage.PYTHON,
        CodeSymbolKind.MODULE,
        "pkg.foo-bar",
        "foo-bar",
    )

    plan = build_module_plan(_snapshot((first, second), ()))

    children = plan.modules[0].children
    assert len(children) == 2
    assert len({child.path for child in children}) == 2
    assert all(child.path.startswith("pkg/foo-bar-") for child in children)
    assert all(
        child.wiki_path == make_code_wiki_path(REPOSITORY_ID, child.path) for child in children
    )


def test_code_wiki_paths_are_scoped_to_the_repository() -> None:
    assert make_code_wiki_path("a" * 64, "common") != make_code_wiki_path("b" * 64, "common")


def test_module_plan_rejects_an_empty_code_index() -> None:
    snapshot = CodeIndexSnapshot(
        index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        languages=(
            CodeLanguage.PYTHON,
            CodeLanguage.GO,
            CodeLanguage.TYPESCRIPT,
        ),
    )

    with pytest.raises(ModulePlanningError, match="empty code index"):
        build_module_plan(snapshot)


def _snapshot(
    symbols: tuple[CodeSymbol, ...],
    relations: tuple[CodeRelation, ...],
) -> CodeIndexSnapshot:
    return CodeIndexSnapshot(
        index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        languages=(
            CodeLanguage.PYTHON,
            CodeLanguage.GO,
            CodeLanguage.TYPESCRIPT,
        ),
        source_versions={
            symbol.location.source_id: symbol.location.source_version for symbol in symbols
        },
        symbols=symbols,
        relations=relations,
    )


def _symbol(
    path: str,
    language: CodeLanguage,
    kind: CodeSymbolKind,
    qualified_name: str,
    display_name: str,
) -> CodeSymbol:
    source_id = hashlib.sha256(f"source:{path}".encode()).hexdigest()
    signature = f"{kind.value} {qualified_name}"
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            REPOSITORY_ID,
            path,
            language,
            kind,
            qualified_name,
        ),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        language=language,
        kind=kind,
        qualified_name=qualified_name,
        display_name=display_name,
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(f"body:{qualified_name}".encode()).hexdigest(),
        location=CodeLocation(
            source_id=source_id,
            source_version=1,
            content_sha256=hashlib.sha256(f"content:{path}".encode()).hexdigest(),
            relative_path=path,
            locator="chars:0-10",
            start_line=1,
            end_line=1,
        ),
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


def _module_path(graph: ArchitectureGraph, module_id: str) -> str:
    return next(node.path for node in graph.nodes if node.module_id == module_id)
