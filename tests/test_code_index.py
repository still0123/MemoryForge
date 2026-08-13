from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from memoryforge.code_index import build_code_index
from memoryforge.code_models import (
    CodeIndexSnapshot,
    CodeLanguage,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
)
from memoryforge.workspace import (
    Workspace,
    init_workspace,
    read_source_excerpt,
    register_git_checkout,
    register_git_code_module,
    sync_git_checkout,
)

PYTHON_SOURCE = '''"""服务模块。"""

def logged(function):
    return function


@logged
def helper(name: str) -> str:
    return f"Hello {name}"


class Greeter:
    def greet(self, name: str) -> str:
        return helper(name)

    def repeat(self, name: str) -> str:
        return self.greet(name)
'''


def test_python_index_extracts_symbols_relations_and_exact_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, workspace, repository_id = _synced_python_repository(tmp_path, PYTHON_SOURCE)

    snapshot = build_code_index(workspace, repository_id)

    assert snapshot.languages == (
        CodeLanguage.PYTHON,
        CodeLanguage.GO,
        CodeLanguage.TYPESCRIPT,
    )
    symbols = {symbol.qualified_name: symbol for symbol in snapshot.symbols}
    assert {
        "src.service",
        "src.service.logged",
        "src.service.helper",
        "src.service.Greeter",
        "src.service.Greeter.greet",
        "src.service.Greeter.repeat",
    } == set(symbols)
    assert symbols["src.service"].kind is CodeSymbolKind.MODULE
    assert symbols["src.service.Greeter"].kind is CodeSymbolKind.CLASS
    assert symbols["src.service.Greeter.greet"].kind is CodeSymbolKind.METHOD
    helper_excerpt = read_source_excerpt(
        workspace,
        source_id=symbols["src.service.helper"].location.source_id,
        source_version=symbols["src.service.helper"].location.source_version,
        locator=symbols["src.service.helper"].location.locator,
    )
    assert helper_excerpt.startswith("@logged\ndef helper")

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
        "src.service",
        "src.service.helper",
    ) in edges
    assert (
        CodeRelationType.CONTAINS,
        "src.service.Greeter",
        "src.service.Greeter.greet",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "src.service.Greeter.greet",
        "src.service.helper",
    ) in edges
    assert (
        CodeRelationType.CALLS,
        "src.service.Greeter.repeat",
        "src.service.Greeter.greet",
    ) in edges

    for symbol in snapshot.symbols:
        excerpt = read_source_excerpt(
            workspace,
            source_id=symbol.location.source_id,
            source_version=symbol.location.source_version,
            locator=symbol.location.locator,
        )
        assert hashlib.sha256(excerpt.encode()).hexdigest() == symbol.body_sha256

    def reject_writable_open(_cls: type[Workspace], _root: Path) -> Workspace:
        raise AssertionError("code indexing must not open a writable workspace")

    monkeypatch.setattr(Workspace, "open", classmethod(reject_writable_open))
    (checkout / "src/service.py").write_text("def uncommitted():\n    pass\n", encoding="utf-8")
    assert build_code_index(workspace, repository_id) == snapshot


def test_python_symbol_ids_survive_body_changes_across_commits(tmp_path: Path) -> None:
    checkout, workspace, repository_id = _synced_python_repository(tmp_path, PYTHON_SOURCE)
    first = build_code_index(workspace, repository_id)
    first_helper = _symbol(first, "src.service.helper")

    updated = PYTHON_SOURCE.replace('return f"Hello {name}"', 'return f"Welcome {name}"')
    (checkout / "src/service.py").write_text(updated, encoding="utf-8")
    _commit_all(checkout, "Update helper body")
    sync_git_checkout(workspace, repository_id)
    second = build_code_index(workspace, repository_id)
    second_helper = _symbol(second, "src.service.helper")

    assert first.index_id != second.index_id
    assert first_helper.symbol_id == second_helper.symbol_id
    assert first_helper.body_sha256 != second_helper.body_sha256
    assert first_helper.location.source_version != second_helper.location.source_version


def test_python_index_canonicalizes_overloads_and_conditional_definitions(
    tmp_path: Path,
) -> None:
    source = """from typing import TYPE_CHECKING, overload

@overload
def choose(value: int) -> int: ...

@overload
def choose(value: str) -> str: ...

def choose(value):
    return value

if TYPE_CHECKING:
    def platform_name() -> str:
        return "typing"
else:
    def platform_name() -> str:
        return "runtime"
"""
    _checkout, workspace, repository_id = _synced_python_repository(tmp_path, source)

    snapshot = build_code_index(workspace, repository_id)
    symbols = [symbol for symbol in snapshot.symbols if symbol.qualified_name != "src.service"]

    assert [symbol.qualified_name for symbol in symbols] == [
        "src.service.choose",
        "src.service.platform_name",
    ]
    assert symbols[0].signature.startswith("def choose(value)")
    assert "@overload" not in symbols[0].signature


def test_python_index_skips_syntax_errors_in_synced_evidence(tmp_path: Path) -> None:
    checkout, workspace, repository_id = _synced_python_repository(
        tmp_path,
        "def broken(:\n    pass\n",
    )

    snapshot = build_code_index(workspace, repository_id)

    assert (checkout / "src/service.py").is_file()
    assert snapshot.symbols == ()
    assert snapshot.source_versions == {}


def test_python_index_skips_noncanonical_module_paths(tmp_path: Path) -> None:
    checkout, workspace, repository_id = _synced_python_repository(
        tmp_path,
        "def healthy():\n    return True\n",
        relative_path="src/ service.py",
    )

    snapshot = build_code_index(workspace, repository_id)

    assert (checkout / "src/ service.py").is_file()
    assert snapshot.symbols == ()
    assert snapshot.source_versions == {}


def test_python_index_redacts_sensitive_string_defaults(tmp_path: Path) -> None:
    source = """def connect(
    password="private",
    client_token="token-value",
    s3_ak: str="access-key",
    region="cn-test",
    optional_token=None,
):
    return region
"""
    _checkout, workspace, repository_id = _synced_python_repository(tmp_path, source)

    signature = _symbol(build_code_index(workspace, repository_id), "src.service.connect").signature

    assert 'password="<redacted>"' in signature
    assert 'client_token="<redacted>"' in signature
    assert 's3_ak: str="<redacted>"' in signature
    assert 'region="cn-test"' in signature
    assert "optional_token=None" in signature
    assert "private" not in signature
    assert "token-value" not in signature
    assert "access-key" not in signature


def _synced_python_repository(
    tmp_path: Path,
    source: str,
    *,
    relative_path: str = "src/service.py",
) -> tuple[Path, Path, str]:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test User")
    (checkout / "README.md").write_text("# Service\n", encoding="utf-8")
    source_path = checkout / relative_path
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    _commit_all(checkout, "Add Python service")

    workspace = init_workspace(tmp_path / "workspace")
    repository = register_git_checkout(workspace, checkout)
    sync_git_checkout(workspace, repository.repository_id)
    register_git_code_module(workspace, repository.repository_id, "src")
    sync_git_checkout(workspace, repository.repository_id)
    return checkout, workspace, repository.repository_id


def _symbol(snapshot: CodeIndexSnapshot, qualified_name: str) -> CodeSymbol:
    return next(symbol for symbol in snapshot.symbols if symbol.qualified_name == qualified_name)


def _symbol_name(snapshot: CodeIndexSnapshot, symbol_id: str) -> str:
    return _symbol_by_id(snapshot, symbol_id).qualified_name


def _symbol_by_id(snapshot: CodeIndexSnapshot, symbol_id: str) -> CodeSymbol:
    return next(symbol for symbol in snapshot.symbols if symbol.symbol_id == symbol_id)


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
