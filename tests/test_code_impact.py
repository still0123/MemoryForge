from __future__ import annotations

import hashlib

import pytest

from memoryforge.code_impact import (
    analyze_diff,
    call_paths,
    impact_analysis,
)
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
        location=_make_location(path=path, locator=locator, lines=lines),
    )


def _make_relation(
    *,
    source: CodeSymbol,
    target: CodeSymbol,
    rtype: CodeRelationType,
) -> CodeRelation:
    return CodeRelation(
        relation_id=make_code_relation_id(REPOSITORY_ID, rtype, source.symbol_id, target.symbol_id),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        type=rtype,
        source_symbol_id=source.symbol_id,
        target_symbol_id=target.symbol_id,
        evidence=(source.location,),
    )


def _build_simple_graph():
    a = _make_symbol(
        path="a.py", qualified_name="a.main", display_name="main",
        kind=CodeSymbolKind.FUNCTION, locator="chars:0-10", lines=(1, 2),
    )
    b = _make_symbol(
        path="b.py", qualified_name="b.service", display_name="service",
        kind=CodeSymbolKind.FUNCTION, locator="chars:0-10", lines=(1, 2),
    )
    c = _make_symbol(
        path="c.py", qualified_name="c.storage", display_name="storage",
        kind=CodeSymbolKind.FUNCTION, locator="chars:0-10", lines=(1, 2),
    )
    priv = _make_symbol(
        path="priv.py", qualified_name="priv._helper", display_name="_helper",
        kind=CodeSymbolKind.FUNCTION, locator="chars:0-10", lines=(1, 2),
    )
    test_c = _make_symbol(
        path="tests/test_c.py", qualified_name="tests.test_c.test_storage", display_name="test_storage",
        kind=CodeSymbolKind.FUNCTION, locator="chars:0-10", lines=(1, 2),
    )

    symbols = [a, b, c, priv, test_c]
    relations = [
        _make_relation(source=a, target=b, rtype=CodeRelationType.CALLS),
        _make_relation(source=b, target=c, rtype=CodeRelationType.CALLS),
        _make_relation(source=b, target=priv, rtype=CodeRelationType.CALLS),
        _make_relation(source=test_c, target=c, rtype=CodeRelationType.TESTS),
    ]
    snapshot = CodeIndexSnapshot(
        index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        languages=(CodeLanguage.PYTHON,),
        source_versions={SOURCE_ID: 1},
        symbols=tuple(symbols),
        relations=tuple(relations),
    )
    return snapshot, a, b, c, priv, test_c


def test_impact_analysis_direct_and_transitive():
    snapshot, a, b, c, _, _ = _build_simple_graph()
    visible = lambda s, v: True
    result = impact_analysis(snapshot, "c.storage", visible_source=visible, max_depth=2)
    assert result.status == "answered"
    assert result.target is not None
    assert result.target.qualified_name == "c.storage"
    direct_srcs = {e.source.qualified_name for e in result.direct}
    assert "b.service" in direct_srcs
    transitive_srcs = {e.source.qualified_name for e in result.transitive}
    assert "a.main" in transitive_srcs


def test_impact_analysis_tests_collection():
    snapshot, _, _, c, _, test_c = _build_simple_graph()
    visible = lambda s, v: True
    result = impact_analysis(snapshot, "c.storage", visible_source=visible)
    assert any(t.qualified_name == test_c.qualified_name for t in result.tests)


def test_impact_analysis_risk_private_with_tests_is_low():
    snapshot, _, _, _, priv, test_c = _build_simple_graph()
    visible = lambda s, v: True
    result = impact_analysis(snapshot, "priv._helper", visible_source=visible)
    assert result.status == "answered"
    assert result.risk in {"low", "medium", "unknown"}


def test_impact_analysis_unknown_target():
    snapshot, _, _, _, _, _ = _build_simple_graph()
    visible = lambda s, v: True
    result = impact_analysis(snapshot, "nonexistent.fn", visible_source=visible)
    assert result.status == "unknown"


def test_impact_analysis_truncated():
    snapshot, a, b, c, _, _ = _build_simple_graph()
    visible = lambda s, v: True
    result = impact_analysis(snapshot, "c.storage", visible_source=visible, max_depth=2, max_nodes=1)
    assert result.truncated is True


def test_call_paths_found():
    snapshot, a, b, c, _, _ = _build_simple_graph()
    visible = lambda s, v: True
    result = call_paths(snapshot, "a.main", "c.storage", visible_source=visible)
    assert result.status == "answered"
    assert len(result.paths) >= 1
    path = result.paths[0]
    assert len(path) == 2


def test_call_paths_not_found():
    snapshot, a, b, c, _, _ = _build_simple_graph()
    visible = lambda s, v: True
    result = call_paths(snapshot, "c.storage", "a.main", visible_source=visible)
    assert result.status == "unknown"


def test_analyze_diff_index_unavailable():
    snapshot, _, _, _, _, _ = _build_simple_graph()
    visible = lambda s, v: True
    result = analyze_diff(None, snapshot, ("a.py",), visible_source=visible)
    assert result.status == "index_unavailable"


def test_analyze_diff_added_and_removed():
    snapshot1, a, b, c, _, _ = _build_simple_graph()
    symbols2 = list(snapshot1.symbols)
    d = _make_symbol(
        path="d.py", qualified_name="d.extra", display_name="extra",
        kind=CodeSymbolKind.FUNCTION, locator="chars:0-10", lines=(1, 2),
    )
    symbols2.append(d)
    new_rels = []
    for r in snapshot1.relations:
        tgt = next((s for s in snapshot1.symbols if s.symbol_id == r.target_symbol_id), None)
        if tgt is None or tgt.qualified_name != "c.storage":
            new_rels.append(r)
    new_rels.append(_make_relation(source=d, target=b, rtype=CodeRelationType.CALLS))
    snapshot2 = CodeIndexSnapshot(
        index_id=make_code_index_id(REPOSITORY_ID, COMMIT_SHA),
        repository_id=REPOSITORY_ID,
        commit_sha=COMMIT_SHA,
        languages=(CodeLanguage.PYTHON,),
        source_versions={SOURCE_ID: 1},
        symbols=tuple(symbols2),
        relations=tuple(new_rels),
    )
    visible = lambda s, v: True
    result = analyze_diff(snapshot1, snapshot2, ("a.py", "b.py", "c.py", "d.py"), visible_source=visible)
    assert result.status == "answered"
    assert result.affected_pages


def test_impact_analysis_sorting_stable():
    snapshot, a, b, c, _, _ = _build_simple_graph()
    visible = lambda s, v: True
    r1 = impact_analysis(snapshot, "c.storage", visible_source=visible, max_depth=2)
    r2 = impact_analysis(snapshot, "c.storage", visible_source=visible, max_depth=2)
    direct_ids_1 = tuple(e.relation_id for e in r1.direct)
    direct_ids_2 = tuple(e.relation_id for e in r2.direct)
    assert direct_ids_1 == direct_ids_2
