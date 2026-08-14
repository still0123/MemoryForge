"""P0 regressions: process notes don't crowd facts; old-turn table slice is exact."""

from __future__ import annotations

import re

import memoryforge.compiler.compiler as compiler
from memoryforge.compiler.wiki_facts import is_conversation_process_note
from memoryforge.core.models import Sensitivity

_PROCESS = [
    "我先查看当前作业调度状态并列出积压项。",
    "接下来我验证每个作业的最近心跳和最后完成时间。",
    "现在按作业名分组汇总并把失败状态高亮出来。",
]
_TABLE = (
    "| Job | State | Last Run |\n|-----|-------|----------|\n"
    "| JobA | ok | 2026-08-12 |\n| JobB | ok | 2026-08-13 |\n"
    "| JobC | running | 2026-08-14 |\n| JobD | ok | 2026-08-12 |\n"
    "| JobE | failed | 2026-08-11 |\n| JobF | ok | 2026-08-13 |\n"
    "| JobG | scheduled | 2026-08-14 |\n"
)
_JOBS = tuple(f"Job{c}" for c in "ABCDEFG")


def _assistant_turn_with_table() -> str:
    return "\n\n".join(_PROCESS) + "\n\n共发现 7 个后台作业，状态汇总如下：\n\n" + _TABLE


def _conversation_with_table_turn_at_offset(offset: int) -> str:
    """Put the table turn `offset` positions back from the latest Assistant turn.
    offset>=8 places it beyond _CONVERSATION_RECENT_TURN_LIMIT (8)."""
    parts = ["# Synthetic conversation\n"]
    for idx in range(offset + 1):
        parts.append("## User\n\nWhat about the background jobs?\n")
        if idx == 0:
            parts.append("## Assistant (unverified)\n\n" + _assistant_turn_with_table() + "\n")
        else:
            parts.append(
                "## Assistant (unverified)\n\n"
                f"Now checking status {idx}. Current aggregate is stable at {idx} items.\n"
            )
    return "\n".join(parts)


def _source() -> compiler.CurrentSource:
    return compiler.CurrentSource(
        source_id="a" * 64,
        source_version=1,
        title="Synthetic conversation",
        category="notes",
        tags=("conversation", "platform:codex", "unverified"),
        updated="2026-08-14T00:00:00Z",
        snapshot_path="synthetic.md",
        sensitivity=Sensitivity.PUBLIC,
        repository_id=None,
        repository_name=None,
        relative_path=None,
    )


def test_process_notes_do_not_consume_substantive_quota() -> None:
    """Substantive facts must displace process lead-ins, not vice versa."""
    content = "# Session\n\n## Assistant (unverified)\n\n" + _assistant_turn_with_table() + "\n"
    quotes = [f.quote for f in compiler._conversation_facts(content)]
    assert quotes, "expected at least one fact"
    substantive = [q for q in quotes if not is_conversation_process_note(q)]
    assert substantive, "lead-ins crowded out substantive content"
    assert len(substantive) == len(quotes), "process notes consumed quota"


def test_older_turn_retains_seven_job_count_and_exact_source_slice() -> None:
    """Offset=10 table turn (beyond 8-turn recent boundary) keeps count +
    all job names. The merged fact quote must be content[start:start+len]."""
    content = _conversation_with_table_turn_at_offset(10)
    page = compiler._render_conversation_page(_source(), content)

    assert "共发现 7 个后台作业" in page, "explicit count missing"
    for job in _JOBS:
        assert job in page, f"{job} missing from page"
    assert re.search(r"^-.*\| JobA \|.*$", page, re.MULTILINE), "table not cited as fact"

    merged = next(
        (
            f
            for f in compiler._conversation_facts(content)
            if "共发现 7 个后台作业" in f.quote and "JobA" in f.quote
        ),
        None,
    )
    assert merged is not None, "merged count+table fact missing"
    assert content[merged.start : merged.start + len(merged.quote)] == merged.quote, (
        "merged quote is not a literal source slice"
    )
    assert "共发现 7 个后台作业" in merged.quote
    for job in _JOBS:
        assert job in merged.quote, f"{job} absent from merged fact"

    merged_end = merged.start + len(merged.quote)
    assert re.search(
        r"\[\^source-[a-f0-9]+-\d+\]: source .*?"
        rf"chars:{merged.start}-{merged_end}",
        page,
    ), f"no footnote cites chars:{merged.start}-{merged_end}"
    slices = [
        content[int(s) : int(e)]
        for s, e in re.findall(r"\[\^source-[a-f0-9]+-\d+\]: source .*?chars:(\d+)-(\d+)", page)
    ]
    assert merged.quote in slices, "merged quote not reproducible from footnote range"
