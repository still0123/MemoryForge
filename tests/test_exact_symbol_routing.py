from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from memoryforge import query as query_module
from memoryforge.wiki_facts import AppliedCodeSymbolMatch


def test_explicit_code_identifiers_preserve_symbol_boundaries() -> None:
    assert query_module._explicit_code_identifiers(
        "Compare s03_permission.code.check_permission, agent_loop, and `run`."
    ) == (
        "s03_permission.code.check_permission",
        "agent_loop",
        "run",
    )
    assert (
        query_module._explicit_code_identifiers(
            "Which function stores embeddings in a vector database?"
        )
        == ()
    )


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


def _match(repository_id: str, page_path: str) -> AppliedCodeSymbolMatch:
    return AppliedCodeSymbolMatch(
        fact_id="c" * 64,
        page_path=page_path,
        repository_id=repository_id,
        source_id="d" * 64,
        source_version=1,
        locator="chars:0-10",
        section_path="Code: src/service.py",
        quote="`src.service.run` (function): `def run() -> str:`",
        routing_text="",
        symbol="src.service.run",
        relation_type=None,
        identifier="run",
        match_kind="display_name",
    )
