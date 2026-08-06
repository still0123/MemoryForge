from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

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

HELPER_SOURCE = """// 问候语辅助函数。
export function helper(name: string): string {
  return `Hello ${name}`;
}

export const version = 1;
"""

SERVICE_SOURCE = """import { helper as runHelper } from "./helper.js";

export interface Store {
  save(): void;
}

export type Name = string;

export const wrap = (name: string): string => runHelper(name);

export function build(name: string): string {
  return wrap(name);
}

export class Service {
  greet(name: string): string {
    return runHelper(name);
  }

  repeat(name: string): string {
    return this.greet(name);
  }

  static create(): Service {
    return new Service();
  }
}
"""

VIEW_SOURCE = """export const View = () => <section>Ready</section>;
"""


def test_typescript_index_extracts_modules_imports_calls_and_tsx(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_typescript_repository(
        tmp_path,
        helper_source=HELPER_SOURCE,
        service_source=SERVICE_SOURCE,
    )

    snapshot = build_code_index(workspace, repository_id)

    symbols = {symbol.qualified_name: symbol for symbol in snapshot.symbols}
    assert {
        "src.helper",
        "src.helper.helper",
        "src.helper.version",
        "src.service",
        "src.service.Store",
        "src.service.Name",
        "src.service.wrap",
        "src.service.build",
        "src.service.Service",
        "src.service.Service.greet",
        "src.service.Service.repeat",
        "src.service.Service.create",
        "src.view",
        "src.view.View",
    } == set(symbols)
    assert symbols["src.service.Store"].kind is CodeSymbolKind.INTERFACE
    assert symbols["src.service.Name"].kind is CodeSymbolKind.TYPE_ALIAS
    assert symbols["src.helper.version"].kind is CodeSymbolKind.CONSTANT
    assert symbols["src.service.wrap"].kind is CodeSymbolKind.FUNCTION
    assert symbols["src.service.Service.greet"].kind is CodeSymbolKind.METHOD

    edges = {
        (
            relation.type,
            _symbol_name(snapshot, relation.source_symbol_id),
            _symbol_name(snapshot, relation.target_symbol_id),
        )
        for relation in snapshot.relations
    }
    assert (
        CodeRelationType.IMPORTS,
        "src.service",
        "src.helper",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "src.service.wrap",
        "src.helper.helper",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "src.service.build",
        "src.service.wrap",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "src.service.Service.repeat",
        "src.service.Service.greet",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "src.service.Service.create",
        "src.service.Service",
    ) in edges
    assert (
        CodeRelationType.CONTAINS,
        "src.service.Service",
        "src.service.Service.greet",
    ) in edges

    for symbol in snapshot.symbols:
        if symbol.language.value != "typescript":
            continue
        excerpt = read_source_excerpt(
            workspace,
            source_id=symbol.location.source_id,
            source_version=symbol.location.source_version,
            locator=symbol.location.locator,
        )
        assert hashlib.sha256(excerpt.encode()).hexdigest() == symbol.body_sha256


def test_typescript_symbol_ids_survive_body_changes_across_commits(
    tmp_path: Path,
) -> None:
    checkout, workspace, repository_id = _synced_typescript_repository(
        tmp_path,
        helper_source=HELPER_SOURCE,
        service_source=SERVICE_SOURCE,
    )
    first = build_code_index(workspace, repository_id)
    first_helper = _symbol(first, "src.helper.helper")

    updated = HELPER_SOURCE.replace("Hello", "Welcome")
    (checkout / "src/helper.ts").write_text(updated, encoding="utf-8")
    _commit_all(checkout, "Update TypeScript helper")
    sync_git_checkout(workspace, repository_id)
    second = build_code_index(workspace, repository_id)
    second_helper = _symbol(second, "src.helper.helper")

    assert first.index_id != second.index_id
    assert first_helper.symbol_id == second_helper.symbol_id
    assert first_helper.body_sha256 != second_helper.body_sha256
    assert first_helper.location.source_version != second_helper.location.source_version


def test_typescript_index_accepts_variance_annotations_with_exact_evidence(
    tmp_path: Path,
) -> None:
    helper_source = """export interface Sink<in T> {
  write(value: T): void;
}

export interface Source<
  /** variance annotation after a comment */
  out T,
> {
  read(): T;
}

export interface Transform<in out T> {
  apply(value: T): T;
}

export namespace Input {
  export interface Props {}
}

export namespace Output {
  export interface Props {}
}

export class Counter {
  get value(): number {
    return 1;
  }

  set value(value: number) {}
}
"""
    _checkout, workspace, repository_id = _synced_typescript_repository(
        tmp_path,
        helper_source=helper_source,
        service_source="export const ready = true;\n",
    )

    snapshot = build_code_index(workspace, repository_id)
    sink = _symbol(snapshot, "src.helper.Sink")
    source = _symbol(snapshot, "src.helper.Source")
    transform = _symbol(snapshot, "src.helper.Transform")
    input_props = _symbol(snapshot, "src.helper.Input.Props")
    output_props = _symbol(snapshot, "src.helper.Output.Props")
    getter = _symbol(snapshot, "src.helper.Counter.value@get")
    setter = _symbol(snapshot, "src.helper.Counter.value@set")

    assert sink.kind is CodeSymbolKind.INTERFACE
    assert source.kind is CodeSymbolKind.INTERFACE
    assert transform.kind is CodeSymbolKind.INTERFACE
    assert input_props.symbol_id != output_props.symbol_id
    assert getter.symbol_id != setter.symbol_id
    assert read_source_excerpt(
        workspace,
        source_id=sink.location.source_id,
        source_version=sink.location.source_version,
        locator=sink.location.locator,
    ).startswith("export interface Sink<in T>")


def test_typescript_index_rejects_syntax_errors_in_synced_evidence(
    tmp_path: Path,
) -> None:
    _checkout, workspace, repository_id = _synced_typescript_repository(
        tmp_path,
        helper_source="export function broken(: string {\n",
        service_source=SERVICE_SOURCE,
    )

    with pytest.raises(ValueError, match="syntax errors"):
        build_code_index(workspace, repository_id)


def _synced_typescript_repository(
    tmp_path: Path,
    *,
    helper_source: str,
    service_source: str,
) -> tuple[Path, Path, str]:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# TypeScript service\n", encoding="utf-8")
    source_dir = checkout / "src"
    source_dir.mkdir()
    (source_dir / "helper.ts").write_text(helper_source, encoding="utf-8")
    (source_dir / "service.ts").write_text(service_source, encoding="utf-8")
    (source_dir / "view.tsx").write_text(VIEW_SOURCE, encoding="utf-8")
    _commit_all(checkout, "Add TypeScript service")

    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    sync_git_checkout(workspace, repository.repository_id)
    register_git_code_module(workspace, repository.repository_id, "src")
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
