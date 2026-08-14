from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from memoryforge.query import query as query_module
from memoryforge.compiler.wiki_facts import AppliedCodeSymbolMatch


def test_explicit_code_identifiers_preserve_symbol_boundaries() -> None:
    assert query_module._explicit_code_identifiers(
        "Compare s03_permission.code.check_permission, agent_loop, and `run`."
    ) == (
        "s03_permission.code.check_permission",
        "agent_loop",
        "run",
    )
    assert query_module._explicit_code_identifiers(
        "foo_bar foo_bar foo_bar foo_bar foo_bar foo_bar foo_bar foo_bar target_symbol $fetch"
    ) == ("foo_bar", "target_symbol", "$fetch")
    assert (
        query_module._explicit_code_identifiers(
            "Which function stores embeddings in a vector database?"
        )
        == ()
    )
    assert query_module._requested_symbol_kinds("Which function is Client.run?") == {
        "function",
        "method",
    }


def test_unscoped_symbol_routes_fail_closed_across_repositories(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    (tmp_path / "raw").mkdir()
    internal = tmp_path / ".memoryforge"
    internal.mkdir()
    (internal / "index.sqlite").touch()
    first = _match("a" * 64, "wiki/pages/code/a/service.md")
    second = _match("b" * 64, "wiki/pages/code/b/service.md")
    monkeypatch.setattr(
        query_module,
        "find_applied_code_symbol_facts",
        lambda *args, **kwargs: (first, second),
    )

    assert (
        query_module._applied_code_symbol_matches(
            tmp_path,
            "What is the signature of `run`?",
            repository_id=None,
        )
        == ()
    )


def test_symbol_routes_use_explicit_module_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    (tmp_path / "raw").mkdir()
    internal = tmp_path / ".memoryforge"
    internal.mkdir()
    (internal / "index.sqlite").touch()
    repository_id = "a" * 64
    matches = (
        _match(
            repository_id,
            "wiki/pages/code/a/s01.md",
            symbol="s01_agent_loop.code.agent_loop",
            identifier="agent_loop",
        ),
        _match(
            repository_id,
            "wiki/pages/code/a/s03.md",
            symbol="s03_permission.code.agent_loop",
            identifier="agent_loop",
        ),
        _match(
            repository_id,
            "wiki/pages/code/a/s12.md",
            symbol="s12_task_system.code.agent_loop",
            identifier="agent_loop",
        ),
    )
    monkeypatch.setattr(
        query_module,
        "find_applied_code_symbol_facts",
        lambda *args, **kwargs: matches,
    )

    selected = query_module._applied_code_symbol_matches(
        tmp_path,
        "Compare agent_loop in s01_agent_loop and s12_task_system.",
        repository_id=repository_id,
    )

    assert [match.page_path for match in selected] == [
        "wiki/pages/code/a/s01.md",
        "wiki/pages/code/a/s12.md",
    ]


def test_requested_symbol_kind_rejects_a_module_context(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    (tmp_path / "raw").mkdir()
    internal = tmp_path / ".memoryforge"
    internal.mkdir()
    (internal / "index.sqlite").touch()
    module = _match(
        "a" * 64,
        "wiki/pages/code/a/s12.md",
        symbol="s12_task_system.code",
        identifier="s12_task_system.code",
        kind="module",
    )
    monkeypatch.setattr(
        query_module,
        "find_applied_code_symbol_facts",
        lambda *args, **kwargs: (module,),
    )

    assert (
        query_module._applied_code_symbol_matches(
            tmp_path,
            "Which class represents a task in s12_task_system.code?",
            repository_id="a" * 64,
        )
        == ()
    )


def test_relation_questions_do_not_use_the_symbol_shortcut(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    (tmp_path / "raw").mkdir()
    internal = tmp_path / ".memoryforge"
    internal.mkdir()
    (internal / "index.sqlite").touch()
    monkeypatch.setattr(
        query_module,
        "find_applied_code_symbol_facts",
        lambda *args, **kwargs: (_match("a" * 64, "wiki/pages/code/a/service.md"),),
    )

    assert (
        query_module._applied_code_symbol_matches(
            tmp_path,
            "Which module does src.service import?",
            repository_id="a" * 64,
        )
        == ()
    )


def _match(
    repository_id: str,
    page_path: str,
    *,
    symbol: str = "src.service.run",
    identifier: str = "run",
    kind: str = "function",
) -> AppliedCodeSymbolMatch:
    return AppliedCodeSymbolMatch(
        fact_id="c" * 64,
        page_path=page_path,
        repository_id=repository_id,
        source_id="d" * 64,
        source_version=1,
        locator="chars:0-10",
        section_path="Code: src/service.py",
        quote=f"`{symbol}` ({kind}): `def run() -> str:`",
        routing_text="",
        symbol=symbol,
        relation_type=None,
        identifier=identifier,
        match_kind=("qualified_name" if "." in identifier else "display_name"),
    )
