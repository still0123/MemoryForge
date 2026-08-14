from __future__ import annotations

import json

import pytest

from memoryforge.query import query as query_module
from memoryforge.compiler.wiki_facts import (
    citation_quote_matches_excerpt,
    conversation_conclusion_text,
    is_conversation_process_note,
    parse_page_citations,
    parse_page_facts,
)

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


def test_code_fact_grounding_handles_markdown_backticks_without_relaxing_evidence() -> None:
    excerpt = 'Page struct { Offset int `json:"Offset,omitempty"` }'
    assert citation_quote_matches_excerpt(
        '`common.Page` (struct): ``Page struct { Offset int `json:"Offset,omitempty"` }``',
        excerpt,
    )
    assert citation_quote_matches_excerpt(
        '`common.Page` (struct): `Page struct { Offset int \\`json:"Offset,omitempty"\\` }`',
        excerpt,
    )


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


def test_page_facts_expand_multiple_citations_for_one_statement() -> None:
    second_source_id = "b" * 64
    content = (
        "# Module\n\n"
        "## 模块职责\n\n"
        "- 汇总两个子模块。 [^source-1] [^source-2]\n\n"
        f"[^source-1]: source `{SOURCE_ID}` · revision `1` · `chars:0-10`\n"
        f"[^source-2]: source `{second_source_id}` · revision `2` · `chars:20-30`\n"
    )

    citations = parse_page_citations(content)

    assert [citation["source_id"] for citation in citations] == [
        SOURCE_ID,
        second_source_id,
    ]
    assert {citation["quote"] for citation in citations} == {"汇总两个子模块。"}


def test_unverified_conversation_notes_remain_searchable() -> None:
    content = (
        "---\n"
        'title: "Conversation"\n'
        "type: concept\n"
        "---\n"
        "# Conversation\n\n"
        "## Conversation notes (unverified)\n\n"
        "### Latest assistant message\n\n"
        "- Candidate 19 is accepted. [^source-1]\n\n"
        f"[^source-1]: source `{SOURCE_ID}` · revision `2` · `chars:10-35`\n"
    )

    citations = parse_page_citations(content)

    assert citations == [
        {
            "source_id": SOURCE_ID,
            "source_version": 2,
            "locator": "chars:10-35",
            "quote": "Candidate 19 is accepted.",
            "grounding": "exact",
            "section_path": "Latest assistant message",
            "is_summary": True,
        }
    ]


def test_conversation_user_prompts_are_search_clues_not_answers() -> None:
    content = (
        "# Conversation\n\n"
        "## Conversation notes (unverified)\n\n"
        "### User prompts (search only)\n\n"
        "- Did cleanup run automatically? [^source-1]\n\n"
        f"[^source-1]: source `{SOURCE_ID}` · revision `2` · `chars:10-40`\n"
    )

    citation = parse_page_citations(content)[0]

    assert citation["section_path"] == "User prompts (search only)"
    assert query_module._is_conversation_search_clue(citation)


def test_conversation_process_notes_are_not_answers() -> None:
    assert is_conversation_process_note("我顺手查一下当前流水线配置。")
    assert is_conversation_process_note("收到，继续完成跳板机认证并登录物理机。")
    assert is_conversation_process_note("现在验证票据并立即开始只读定位。")
    assert not is_conversation_process_note("流水线失败后，后续清理步骤会被跳过。")
    assert not is_conversation_process_note(
        "明白，登录链路是本机 Kerberos、跳板机、物理机。"
    )
    assert not is_conversation_process_note(
        "流水线使用 stop_on_error，中间失败后 delete 会被 skip。现在我继续看其他配置。"
    )
    assert (
        conversation_conclusion_text("流水线失败后 delete 会被 skip。现在我继续看其他配置。")
        == "流水线失败后 delete 会被 skip。"
    )
    assert (
        conversation_conclusion_text("远程有两个目录。我先看仓库信息，判断哪一个要改 6.6.2。")
        == "远程有两个目录。"
    )


def test_page_facts_reject_paths_outside_stable_wiki_pages() -> None:
    with pytest.raises(ValueError, match="below wiki/pages"):
        parse_page_facts("../wiki/pages/note.md", "# Note")
