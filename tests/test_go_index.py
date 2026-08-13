from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from memoryforge.code_index import build_code_index
from memoryforge.code_models import (
    CodeIndexSnapshot,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
)
from memoryforge.workspace import (
    init_workspace,
    read_source_excerpt,
    register_git_checkout,
    register_git_code_module,
    sync_git_checkout,
)

MODEL_SOURCE = """package meter

// 计量器记录用量。
type Meter struct{}

type Store interface {
    Save() error
}

func NewMeter() *Meter {
    return &Meter{}
}

func helper() {}

func (m *Meter) Reset() {}
"""

SERVICE_SOURCE = """package meter

func (m *Meter) Record() {
    helper()
    m.Reset()
}

func Build() *Meter {
    return NewMeter()
}
"""


def test_go_index_extracts_package_types_receivers_and_cross_file_calls(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_go_repository(
        tmp_path,
        model_source=MODEL_SOURCE,
        service_source=SERVICE_SOURCE,
    )

    snapshot = build_code_index(workspace, repository_id)

    symbols = {symbol.qualified_name: symbol for symbol in snapshot.symbols}
    assert {
        "internal.meter@model.go",
        "internal.meter@service.go",
        "internal.meter.Meter",
        "internal.meter.Store",
        "internal.meter.NewMeter",
        "internal.meter.helper",
        "internal.meter.Meter.Reset",
        "internal.meter.Meter.Record",
        "internal.meter.Build",
    } == set(symbols)
    assert symbols["internal.meter@model.go"].kind is CodeSymbolKind.PACKAGE
    assert symbols["internal.meter.Meter"].kind is CodeSymbolKind.STRUCT
    assert symbols["internal.meter.Store"].kind is CodeSymbolKind.INTERFACE
    assert symbols["internal.meter.Meter.Record"].kind is CodeSymbolKind.METHOD

    edges = {
        (
            relation.type,
            _symbol_name(snapshot, relation.source_symbol_id),
            _symbol_name(snapshot, relation.target_symbol_id),
        )
        for relation in snapshot.relations
    }
    assert (
        CodeRelationType.CONTAINS,
        "internal.meter.Meter",
        "internal.meter.Meter.Record",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "internal.meter.Meter.Record",
        "internal.meter.helper",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "internal.meter.Meter.Record",
        "internal.meter.Meter.Reset",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "internal.meter.Build",
        "internal.meter.NewMeter",
    ) in edges

    for symbol in snapshot.symbols:
        excerpt = read_source_excerpt(
            workspace,
            source_id=symbol.location.source_id,
            source_version=symbol.location.source_version,
            locator=symbol.location.locator,
        )
        assert hashlib.sha256(excerpt.encode()).hexdigest() == symbol.body_sha256


def test_go_symbol_ids_survive_body_changes_across_commits(tmp_path: Path) -> None:
    checkout, workspace, repository_id = _synced_go_repository(
        tmp_path,
        model_source=MODEL_SOURCE,
        service_source=SERVICE_SOURCE,
    )
    first = build_code_index(workspace, repository_id)
    first_helper = _symbol(first, "internal.meter.helper")

    updated = MODEL_SOURCE.replace("func helper() {}", 'func helper() { println("updated") }')
    (checkout / "internal/meter/model.go").write_text(updated, encoding="utf-8")
    _commit_all(checkout, "Update Go helper")
    sync_git_checkout(workspace, repository_id)
    second = build_code_index(workspace, repository_id)
    second_helper = _symbol(second, "internal.meter.helper")

    assert first.index_id != second.index_id
    assert first_helper.symbol_id == second_helper.symbol_id
    assert first_helper.body_sha256 != second_helper.body_sha256
    assert first_helper.location.source_version != second_helper.location.source_version


def test_go_index_ignores_function_local_types(tmp_path: Path) -> None:
    model_source = """package meter

type Meter struct{}

func first() {
    type key struct{}
}

func second() {
    type key struct{}
}
"""
    _checkout, workspace, repository_id = _synced_go_repository(
        tmp_path,
        model_source=model_source,
        service_source="package meter\n",
    )

    snapshot = build_code_index(workspace, repository_id)
    qualified_names = {symbol.qualified_name for symbol in snapshot.symbols}

    assert "internal.meter.first" in qualified_names
    assert "internal.meter.second" in qualified_names
    assert "internal.meter.key" not in qualified_names


def test_go_index_keeps_complete_declarations_when_a_function_body_is_invalid(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_go_repository(
        tmp_path,
        model_source="""package meter

func Before() {}

func Broken() {
    _ = new(call())
}

func After() {}
""",
        service_source=SERVICE_SOURCE,
    )

    snapshot = build_code_index(workspace, repository_id)

    names = {symbol.qualified_name for symbol in snapshot.symbols}
    assert "internal.meter.Before" in names
    assert "internal.meter.After" in names


def _synced_go_repository(
    tmp_path: Path,
    *,
    model_source: str,
    service_source: str,
) -> tuple[Path, Path, str]:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Meter\n", encoding="utf-8")
    module = checkout / "internal/meter"
    module.mkdir(parents=True)
    (module / "model.go").write_text(model_source, encoding="utf-8")
    (module / "service.go").write_text(service_source, encoding="utf-8")
    _commit_all(checkout, "Add Go meter")

    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    sync_git_checkout(workspace, repository.repository_id)
    register_git_code_module(workspace, repository.repository_id, "internal/meter")
    sync_git_checkout(workspace, repository.repository_id)
    return checkout, workspace, repository.repository_id


def _symbol(snapshot: CodeIndexSnapshot, qualified_name: str) -> CodeSymbol:
    return next(symbol for symbol in snapshot.symbols if symbol.qualified_name == qualified_name)


def _symbol_name(snapshot: CodeIndexSnapshot, symbol_id: str) -> str:
    return next(
        symbol.qualified_name for symbol in snapshot.symbols if symbol.symbol_id == symbol_id
    )


def _commit_all(checkout: Path, message: str) -> None:
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-m", message)


def _git(checkout: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
