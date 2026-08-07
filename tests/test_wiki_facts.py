from __future__ import annotations

import json

import pytest

from memoryforge.wiki_facts import parse_page_citations, parse_page_facts

SOURCE_ID = "a" * 64


def test_page_facts_preserve_grounded_text_and_code_metadata() -> None:
    relation_evidence = "call target with `literal`  spacing"
    content = (
        "---\n"
        'title: "Code: pkg/service.py"\n'
        "type: concept\n"
        "---\n"
        "# Service\n\n"
        "## Verified symbols\n\n"
        "### Code: pkg/service.py\n\n"
        "- `pkg.service.run` (function): `def run(value: int) -> str:` [^code-1]\n\n"
        "## Verified dependencies\n\n"
        "### Code: pkg/service.py\n\n"
        f"- `pkg.service.run -> pkg.target.call` (calls): "
        f"{json.dumps(relation_evidence)} [^code-2]\n\n"
        "## Sources\n\n"
        f"[^code-1]: source `{SOURCE_ID}` · revision `3` · `chars:0-40`\n"
        f"[^code-2]: source `{SOURCE_ID}` · revision `3` · `chars:41-80`\n"
    )

    citations = parse_page_citations(content)
    facts = parse_page_facts("wiki/pages/code/repository/pkg/service.md", content)

    assert [citation["quote"] for citation in citations] == [
        "`pkg.service.run` (function): `def run(value: int) -> str:`",
        relation_evidence,
    ]
    assert [fact.symbol for fact in facts] == ["pkg.service.run", None]
    assert [fact.relation_type for fact in facts] == [None, "calls"]
    assert facts[1].routing_text == "`pkg.service.run -> pkg.target.call` (calls)"
    assert facts[1].quote == relation_evidence
    assert all(fact.section_path == "Code: pkg/service.py" for fact in facts)
    assert all(fact.source_version == 3 for fact in facts)
    assert len({fact.fact_id for fact in facts}) == 2


def test_page_fact_identity_is_deterministic() -> None:
    content = (
        "# Note\n\n"
        "## Verified facts\n\n"
        f"- Stable fact [^source-1]\n\n"
        f"[^source-1]: source `{SOURCE_ID}` · revision `1` · `chars:0-11`\n"
    )

    first = parse_page_facts("wiki/pages/note.md", content)
    second = parse_page_facts("wiki/pages/note.md", content)

    assert first == second


def test_page_facts_reject_paths_outside_stable_wiki_pages() -> None:
    with pytest.raises(ValueError, match="below wiki/pages"):
        parse_page_facts("../wiki/pages/note.md", "# Note")
