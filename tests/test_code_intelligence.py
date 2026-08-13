from __future__ import annotations

import hashlib

import pytest

from memoryforge.code_intelligence import symbol_context
from memoryforge.code_models import (
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
)

REPOSITORY_ID = "a" * 64
COMMIT_SHA = "b" * 40
SOURCE_ID = "c" * 64
SOURCE_ID_2 = "d" * 64
CONTENT_SHA256 = "e" * 64


def _make_location(
    *,
    source_id: str = SOURCE_ID,
    source_version: int = 1,
    path: str,
    locator: str,
    lines: tuple[int, int],
) -> CodeLocation:
    return CodeLocation(
        source_id=source_id,
        source_version=source_version,
        content_sha256=CONTENT_SHA256,
        relative_path=path,
        locator=locator,
        start_line=lines[0],
        end_line=lines[1],
    )


def _make_symbol(
    *,
    path: str,
    qualified_name: str,
    display_name: str,
    kind: CodeSymbolKind,
    locator: str,
    lines: tuple[int, int],
    source_id: str = SOURCE_ID,
    source_version: int = 1,
) -> CodeSymbol:
    signature = f"def {display_name}():"
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            REPOSITORY_ID, path, CodeLanguage.PYTHON, kind, qualified_name
        ),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        language=CodeLanguage.PYTHON,
        kind=kind,
        qualified_name=qualified_name,
        display_name=display_name,
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(f"{signature}\n    pass\n".encode()).hexdigest(),
        location=_make_location(
            source_id=source_id,
            source_version=source_version,
            path=path,
            locator=locator,
            lines=lines,
        ),
    )


def _make_relation(
    *,
    source: CodeSymbol,
    target: CodeSymbol,
    rtype: CodeRelationType,
    evidence_source: CodeSymbol | None = None,
) -> CodeRelation:
    ev = evidence_source.location if evidence_source else source.location
    return CodeRelation(
        relation_id=make_code_relation_id(REPOSITORY_ID, rtype, source.symbol_id, target.symbol_id),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        type=rtype,
        source_symbol_id=source.symbol_id,
        target_symbol_id=target.symbol_id,
        evidence=(ev,),
    )


def _build_snapshot():
    entry_mod = _make_symbol(
        path="app/entry.py",
        qualified_name="app.entry",
        display_name="entry",
        kind=CodeSymbolKind.MODULE,
        locator="chars:0-100",
        lines=(1, 10),
    )
    storage_mod = _make_symbol(
        path="app/storage.py",
        qualified_name="app.storage",
        display_name="storage",
        kind=CodeSymbolKind.MODULE,
        locator="chars:0-120",
        lines=(1, 10),
    )
    service_mod = _make_symbol(
        path="app/service.py",
        qualified_name="app.service",
        display_name="service",
        kind=CodeSymbolKind.MODULE,
        locator="chars:0-200",
        lines=(1, 10),
    )

    main_fn = _make_symbol(
        path="app/entry.py",
        qualified_name="app.entry.main",
        display_name="main",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:10-50",
        lines=(2, 5),
    )
    get_user_fn = _make_symbol(
        path="app/storage.py",
        qualified_name="app.storage.get_user",
        display_name="get_user",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:10-40",
        lines=(2, 4),
    )
    save_user_fn = _make_symbol(
        path="app/storage.py",
        qualified_name="app.storage.save_user",
        display_name="save_user",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:50-90",
        lines=(5, 8),
    )
    _private_fn = _make_symbol(
        path="app/storage.py",
        qualified_name="app.storage._connect",
        display_name="_connect",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:100-120",
        lines=(9, 10),
    )
    create_user_svc = _make_symbol(
        path="app/service.py",
        qualified_name="app.service.create_user",
        display_name="create_user",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:10-60",
        lines=(2, 6),
    )

    test_create = _make_symbol(
        path="tests/test_service.py",
        qualified_name="tests.test_service.test_create",
        display_name="test_create",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:10-60",
        lines=(2, 6),
        source_id=SOURCE_ID_2,
        source_version=1,
    )

    ambiguous_a = _make_symbol(
        path="app/helpers.py",
        qualified_name="app.helpers.main",
        display_name="main",
        kind=CodeSymbolKind.FUNCTION,
        locator="chars:0-10",
        lines=(1, 2),
    )

    symbols = [
        entry_mod, storage_mod, service_mod,
        main_fn, get_user_fn, save_user_fn, _private_fn,
        create_user_svc, test_create, ambiguous_a,
    ]

    relations = [
        _make_relation(source=entry_mod, target=main_fn, rtype=CodeRelationType.CONTAINS, evidence_source=entry_mod),
        _make_relation(source=storage_mod, target=get_user_fn, rtype=CodeRelationType.CONTAINS, evidence_source=storage_mod),
        _make_relation(source=storage_mod, target=save_user_fn, rtype=CodeRelationType.CONTAINS, evidence_source=storage_mod),
        _make_relation(source=storage_mod, target=_private_fn, rtype=CodeRelationType.CONTAINS, evidence_source=storage_mod),
        _make_relation(source=service_mod, target=create_user_svc, rtype=CodeRelationType.CONTAINS, evidence_source=service_mod),
        _make_relation(source=main_fn, target=create_user_svc, rtype=CodeRelationType.CALLS, evidence_source=main_fn),
        _make_relation(source=create_user_svc, target=get_user_fn, rtype=CodeRelationType.CALLS, evidence_source=create_user_svc),
        _make_relation(source=create_user_svc, target=save_user_fn, rtype=CodeRelationType.CALLS, evidence_source=create_user_svc),
        _make_relation(source=main_fn, target=storage_mod, rtype=CodeRelationType.IMPORTS, evidence_source=main_fn),
        _make_relation(source=test_create, target=create_user_svc, rtype=CodeRelationType.TESTS, evidence_source=test_create),
    ]

    snapshot = CodeIndexSnapshot(
        index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        languages=(CodeLanguage.PYTHON,),
        source_versions={SOURCE_ID: 1, SOURCE_ID_2: 1},
        symbols=tuple(symbols),
        relations=tuple(relations),
    )
    return snapshot


def test_symbol_context_exact_match():
    snapshot = _build_snapshot()
    visible = lambda s, v: True
    result = symbol_context(snapshot, "app.service.create_user", visible_source=visible)
    assert result.status == "answered"
    assert result.symbol is not None
    assert result.symbol.qualified_name == "app.service.create_user"
    assert any(c.qualified_name == "app.entry.main" for c in result.direct_callers)
    callees_names = {c.qualified_name for c in result.direct_callees}
    assert "app.storage.get_user" in callees_names
    assert "app.storage.save_user" in callees_names


def test_symbol_context_prefix_match():
    snapshot = _build_snapshot()
    visible = lambda s, v: True
    result = symbol_context(snapshot, "app.storage", visible_source=visible)
    assert result.status == "answered"
    assert result.symbol is not None
    assert result.symbol.qualified_name == "app.storage"


def test_symbol_context_short_name_match():
    snapshot = _build_snapshot()
    visible = lambda s, v: True
    result = symbol_context(snapshot, "get_user", visible_source=visible)
    assert result.status == "answered"
    assert result.symbol is not None
    assert result.symbol.qualified_name == "app.storage.get_user"


def test_symbol_context_ambiguous():
    snapshot = _build_snapshot()
    visible = lambda s, v: True
    result = symbol_context(snapshot, "main", visible_source=visible)
    assert result.status == "partial"
    assert len(result.ambiguous) >= 2
    qns = {a.qualified_name for a in result.ambiguous}
    assert "app.entry.main" in qns
    assert "app.helpers.main" in qns


def test_symbol_context_unknown():
    snapshot = _build_snapshot()
    visible = lambda s, v: True
    result = symbol_context(snapshot, "nonexistent_function_xyz", visible_source=visible)
    assert result.status == "unknown"
    assert result.symbol is None


def test_symbol_context_visibility_filter_counts_unknown_edges():
    snapshot = _build_snapshot()
    visible = lambda s, v: s != SOURCE_ID_2
    result = symbol_context(snapshot, "app.service.create_user", visible_source=visible)
    assert result.unknown_edges > 0
    assert len(result.related_tests) == 0


def test_symbol_context_related_tests():
    snapshot = _build_snapshot()
    visible = lambda s, v: True
    result = symbol_context(snapshot, "app.service.create_user", visible_source=visible)
    assert any(t.qualified_name == "tests.test_service.test_create" for t in result.related_tests)
