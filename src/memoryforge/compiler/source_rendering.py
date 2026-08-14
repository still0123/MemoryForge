"""Deterministic source parsing and Markdown page rendering."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from memoryforge.compiler.index_rendering import PageType
from memoryforge.compiler.wiki_facts import (
    conversation_conclusion_text,
    is_conversation_process_note,
)
from memoryforge.core.models import Sensitivity
from memoryforge.storage.workspace import Workspace

CodeFact = tuple[str, int, str | None]
_CATEGORY_PAGE_TYPES: dict[str, PageType] = {
    "summary": "entity",
    "design": "concept",
    "notes": "concept",
    "refs": "concept",
    "postmortem": "synthesis",
}
_ENTITY_WORDS = (
    "repository",
    "repo",
    "service",
    "module",
    "protocol",
    "仓库",
    "服务",
    "模块",
    "协议",
)
_SYNTHESIS_WORDS = (
    "decision",
    "tradeoff",
    "comparison",
    "postmortem",
    "retro",
    "adr",
    "决策",
    "取舍",
    "对比",
    "复盘",
)
_MARKDOWN_HEADING = re.compile(
    r"^(?P<marks>#{1,6})[ \t]+(?P<title>.+?)(?:[ \t]+#+)?[ \t]*$",
    re.MULTILINE,
)
_MARKDOWN_LIST_ITEM = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+"
    r"(?P<fact>[^\n]+(?:\n(?![ \t]*(?:[-*+]|\d+[.)])[ \t]+)[ \t]+\S[^\n]*)*)[ \t]*$",
    re.MULTILINE,
)
_CONVERSATION_ROLE_HEADING = re.compile(
    r"^## (?P<role>User|Assistant \(unverified\)|用户|Codex)\s*$",
    re.MULTILINE,
)
# Markdown lists are compiled as separate facts. Keep enough facts to retain
# later sections of a normal README instead of truncating after the quick-start.
_LOCAL_FACT_LIMIT = 48
_CONVERSATION_TURN_LIMIT = 128
_CONVERSATION_RECENT_TURN_LIMIT = 8
_CONVERSATION_FACTS_PER_TURN = 3
_CONVERSATION_EARLIER_FACTS_PER_TURN = 1
_CONVERSATION_FACT_CHAR_LIMIT = 600


@dataclass(frozen=True)
class CurrentSource:
    source_id: str
    source_version: int
    title: str
    category: str
    tags: tuple[str, ...]
    updated: str
    snapshot_path: str
    sensitivity: Sensitivity
    repository_id: str | None
    repository_name: str | None
    relative_path: str | None


@dataclass(frozen=True)
class SourceFact:
    """One exact source excerpt plus the Markdown section that owns it."""

    quote: str
    start: int
    section_path: tuple[str, ...]


def _read_source_text(workspace: Workspace, source: CurrentSource) -> str:
    return (workspace.root / source.snapshot_path).read_text(encoding="utf-8")


def _meaningful_paragraphs(content: str) -> list[SourceFact]:
    facts: list[tuple[str, int]] = []
    structured_ranges: list[tuple[int, int]] = []
    for match in re.finditer(
        r"^```[^\n]*\n(?P<fact>.*?)^```[ \t]*$", content, re.MULTILINE | re.DOTALL
    ):
        quote = match.group("fact").strip()
        if quote:
            leading = len(match.group("fact")) - len(match.group("fact").lstrip())
            facts.append((quote, match.start("fact") + leading))
        structured_ranges.append(match.span())
    for match in re.finditer(r"^(?:\|.*\|\n?){2,}", content, re.MULTILINE):
        if any(start < match.end() and match.start() < end for start, end in structured_ranges):
            continue
        quote = match.group().strip()
        if quote:
            facts.append((quote, match.start()))
        structured_ranges.append(match.span())
    for match in _MARKDOWN_LIST_ITEM.finditer(content):
        if any(start < match.end() and match.start() < end for start, end in structured_ranges):
            continue
        quote = match.group("fact").strip()
        if quote:
            leading = len(match.group("fact")) - len(match.group("fact").lstrip())
            facts.append((quote, match.start("fact") + leading))
        structured_ranges.append(match.span())
    for match in re.finditer(
        r"(?:\A|\n[ \t]*\n)(?P<paragraph>.*?)(?=\n[ \t]*\n|\Z)",
        content,
        re.DOTALL,
    ):
        overlaps_structured = any(
            start < match.end("paragraph") and match.start("paragraph") < end
            for start, end in structured_ranges
        )
        if overlaps_structured:
            continue
        paragraph = match.group("paragraph")
        leading = len(paragraph) - len(paragraph.lstrip())
        quote = paragraph.strip()
        if quote and not quote.startswith(("#", "```", "|")):
            facts.append((quote, match.start("paragraph") + leading))
    if facts:
        headings = _markdown_headings(content)
        return [
            SourceFact(
                quote=quote,
                start=start,
                section_path=_section_path_at(headings, start),
            )
            for quote, start in sorted(facts, key=lambda fact: fact[1])[:_LOCAL_FACT_LIMIT]
        ]
    for line in content.splitlines():
        candidate = line.lstrip("#").strip()
        if candidate:
            start = content.index(candidate)
            return [
                SourceFact(
                    quote=candidate,
                    start=start,
                    section_path=_section_path_at(_markdown_headings(content), start),
                )
            ]
    raise ValueError("source contains no meaningful text")


def _markdown_headings(content: str) -> list[tuple[int, tuple[str, ...]]]:
    stack: list[str] = []
    headings: list[tuple[int, tuple[str, ...]]] = []
    for match in _MARKDOWN_HEADING.finditer(content):
        level = len(match["marks"])
        title = match["title"].strip()
        if not title:
            continue
        stack[level - 1 :] = [title]
        headings.append((match.start(), tuple(stack)))
    return headings


def _section_path_at(
    headings: list[tuple[int, tuple[str, ...]]],
    position: int,
) -> tuple[str, ...]:
    return next((path for start, path in reversed(headings) if start <= position), ())


def _is_markdown_table(quote: str) -> bool:
    return quote.lstrip().startswith("|") and "\n" in quote.rstrip()


def _order_assistant_facts(raw_facts: list[SourceFact], body: str) -> list[SourceFact]:
    """Order substantive facts while preserving exact adjacent paragraph-table slices."""
    merged: list[SourceFact] = []
    tables: list[SourceFact] = []
    other: list[SourceFact] = []
    skip_next = False
    for index, fact in enumerate(raw_facts):
        if skip_next:
            skip_next = False
            continue
        is_table = _is_markdown_table(fact.quote)
        following = raw_facts[index + 1] if index + 1 < len(raw_facts) else None
        if not is_table and following is not None and _is_markdown_table(following.quote):
            gap = body[fact.start + len(fact.quote) : following.start]
            if gap.strip() == "":
                merged.append(
                    SourceFact(
                        quote=body[fact.start : following.start + len(following.quote)],
                        start=fact.start,
                        section_path=fact.section_path,
                    )
                )
                skip_next = True
                continue
        if is_table:
            tables.append(fact)
        else:
            other.append(fact)
    return merged + tables + other


def _page_type(source: CurrentSource) -> PageType:
    normalized_title = source.title.lower()
    if any(word in normalized_title for word in _SYNTHESIS_WORDS):
        return "synthesis"
    if any(word in normalized_title for word in _ENTITY_WORDS):
        return "entity"
    return _CATEGORY_PAGE_TYPES.get(source.category, "concept")


def _wiki_path(source: CurrentSource) -> str:
    return f"wiki/pages/{source.source_id}.md"


def _canonical_page_path(source_ids: tuple[str, ...]) -> str:
    """Return the stable physical page path for a source ownership set."""
    ordered = tuple(sorted(source_ids))
    if len(ordered) == 1:
        filename = f"{ordered[0]}.md"
    else:
        prefixes = "-".join(source_id[:8] for source_id in ordered)
        filename = f"merged-{prefixes}.md"
    return f"wiki/pages/{filename}"


def _render_page(
    source: CurrentSource,
    facts: list[SourceFact],
    *,
    section_title: str = "Verified facts",
    summary_fact: SourceFact | None = None,
    section_preamble: tuple[str, ...] = (),
) -> str:
    displayed_facts = [
        SourceFact(
            quote=" ".join(line.strip() for line in fact.quote.splitlines()),
            start=fact.start,
            section_path=fact.section_path,
        )
        for fact in facts
    ]
    raw_summary = summary_fact or facts[0]
    first_fact = SourceFact(
        quote=" ".join(line.strip() for line in raw_summary.quote.splitlines()),
        start=raw_summary.start,
        section_path=raw_summary.section_path,
    )
    summary_prefix = " / ".join(first_fact.section_path)
    summary = f"{summary_prefix}: {first_fact.quote}" if summary_prefix else first_fact.quote
    tags = tuple(dict.fromkeys((source.category, *source.tags)))
    lines = [
        "---",
        f"title: {json.dumps(source.title, ensure_ascii=False)}",
        f"type: {_page_type(source)}",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"sources: {json.dumps((source.source_id,), ensure_ascii=False)}",
        f"source_version: {source.source_version}",
        f"updated: {source.updated}",
        "---",
        "",
        f"# {source.title}",
        "",
        f"## {section_title}",
        "",
        *section_preamble,
    ]
    section_path: tuple[str, ...] = ()
    for index, fact in enumerate(displayed_facts, start=1):
        if fact.section_path != section_path:
            section_path = fact.section_path
            if section_path:
                lines.extend(["", f"### {' / '.join(section_path)}", ""])
        lines.append(f"- {fact.quote} [^source-{source.source_id[:8]}-{index}]")
    lines.append("")
    for index, fact in enumerate(displayed_facts, start=1):
        end = fact.start + len(facts[index - 1].quote)
        lines.append(
            f"[^source-{source.source_id[:8]}-{index}]: source `{source.source_id}` · revision "
            f"`{source.source_version}` · `chars:{fact.start}-{end}`"
        )
    return "\n".join(lines) + "\n"


def _render_conversation_page(source: CurrentSource, content: str) -> str:
    """Keep recent, unverified conversation notes; retain full transcript as evidence."""
    facts = _conversation_facts(content)
    if not facts:
        return _render_page(source, _meaningful_paragraphs(content))
    assistant_facts = [
        SourceFact(conversation_conclusion_text(fact.quote), fact.start, ("Assistant conclusions",))
        for fact in facts
        if "assistant" in fact.section_path[-1].lower()
    ]
    user_facts = [
        SourceFact(fact.quote, fact.start, ("User prompts (search only)",))
        for fact in facts
        if "user" in fact.section_path[-1].lower()
    ]
    title_terms = {
        term.casefold()
        for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,}", source.title)
    }
    eligible_facts = [
        fact
        for fact in assistant_facts
        if len(fact.quote) >= 40
        and not fact.quote.lstrip().startswith(("```", "func ", "def "))
        and not any(marker in fact.quote for marker in ("你偏好", "值得“记忆”", "你常做的是"))
        and not is_conversation_process_note(fact.quote)
    ]
    summary_fact = max(
        eligible_facts,
        key=lambda fact: sum(term in fact.quote.casefold() for term in title_terms),
        default=assistant_facts[0] if assistant_facts else user_facts[0],
    )
    assistant_facts = [summary_fact, *(fact for fact in assistant_facts if fact != summary_fact)]
    displayed_facts = assistant_facts + user_facts
    summary_quote = summary_fact.quote
    if len(summary_quote) > 180:
        summary_quote = summary_quote[:179].rstrip() + "…"
    summary_fact = SourceFact(
        quote=summary_quote,
        start=summary_fact.start,
        section_path=(),
    )
    return _render_page(
        source,
        displayed_facts,
        section_title="Conversation notes (unverified)",
        summary_fact=summary_fact,
        section_preamble=(
            "> Assistant conclusions may answer questions. User prompts are search clues only. "
            "Full transcript remains in immutable raw evidence.",
            "",
        ),
    )


def _conversation_facts(content: str) -> list[SourceFact]:
    matches = list(_CONVERSATION_ROLE_HEADING.finditer(content))
    turns: list[tuple[str, list[SourceFact]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        raw_body = content[match.end() : end]
        leading = len(raw_body) - len(raw_body.lstrip())
        body = raw_body.strip()
        if not body:
            continue
        body_start = match.end() + leading
        try:
            body_facts = _meaningful_paragraphs(body)
        except ValueError:
            continue
        role = "Assistant" if match.group("role") in {"Assistant (unverified)", "Codex"} else "User"
        if role == "Assistant":
            ordered = _order_assistant_facts(body_facts, body)
            substantive = [fact for fact in ordered if not is_conversation_process_note(fact.quote)]
            body_facts = substantive or [
                fact for fact in ordered if is_conversation_process_note(fact.quote)
            ]
        body_facts = body_facts[:_CONVERSATION_FACTS_PER_TURN]
        turns.append(
            (
                role,
                [
                    SourceFact(
                        quote=fact.quote[:_CONVERSATION_FACT_CHAR_LIMIT].rstrip(),
                        start=body_start + fact.start,
                        section_path=(),
                    )
                    for fact in body_facts
                ],
            )
        )
    retained = turns[-_CONVERSATION_TURN_LIMIT:]
    facts: list[SourceFact] = []
    for recency, (role, turn_facts) in enumerate(reversed(retained)):
        label = f"{'Latest' if recency == 0 else 'Earlier'} {role.lower()} message"
        fact_limit = (
            _CONVERSATION_FACTS_PER_TURN
            if recency < _CONVERSATION_RECENT_TURN_LIMIT
            else _CONVERSATION_EARLIER_FACTS_PER_TURN
        )
        facts.extend(
            SourceFact(
                quote=fact.quote,
                start=fact.start,
                section_path=(label,),
            )
            for fact in turn_facts[:fact_limit]
        )
    return facts


def _render_code_page(source: CurrentSource, content: str) -> str:
    """Render a small, citable outline without pretending to fully understand code."""
    language = "Go" if "go" in source.tags else "Python"
    facts = _code_facts(content, language)
    symbols = [quote for quote, _, _ in facts[1:]]
    summary = f"{language} code"
    if facts:
        summary += f": {facts[0][0]}"
    if symbols:
        summary += "; exports " + ", ".join(symbols[:6])
    tags = tuple(dict.fromkeys((source.category, *source.tags)))
    lines = [
        "---",
        f"title: {json.dumps(source.title, ensure_ascii=False)}",
        "type: concept",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"sources: {json.dumps((source.source_id,), ensure_ascii=False)}",
        f"source_version: {source.source_version}",
        f"updated: {source.updated}",
        "---",
        "",
        f"# {source.title}",
        "",
        "## Code outline",
        "",
        f"- Language: {language}",
        f"- File: `{source.relative_path or source.title}`",
        "",
        "## Verified facts",
        "",
        f"### {source.relative_path or source.title}",
        "",
    ]
    current_section: str | None = None
    for index, (quote, _, section) in enumerate(facts, start=1):
        if section and section != current_section:
            lines.extend([f"#### {section}", ""])
            current_section = section
        lines.append(f"- {quote} [^source-{index}]")
    lines.append("")
    for index, (quote, start, _) in enumerate(facts, start=1):
        lines.append(
            f"[^source-{index}]: source `{source.source_id}` · revision "
            f"`{source.source_version}` · `chars:{start}-{start + len(quote)}`"
        )
    return "\n".join(lines) + "\n"


def _code_facts(content: str, language: str) -> list[CodeFact]:
    if language == "Go":
        return _go_code_facts(content)
    facts: list[CodeFact] = []
    for match in re.finditer(r"^(?:class|def)\s+[A-Za-z]\w*", content, re.MULTILINE):
        quote = match.group().strip()
        if quote:
            leading = len(match.group()) - len(match.group().lstrip())
            facts.append((quote, match.start() + leading, None))
    if facts:
        return facts[:8]
    for match in re.finditer(r"^.+$", content, re.MULTILINE):
        quote = match.group().strip()
        if quote:
            return [(quote, match.start() + len(match.group()) - len(match.group().lstrip()), None)]
    raise ValueError("source contains no meaningful code")


def _go_code_facts(content: str) -> list[CodeFact]:
    """Extract declarations and struct fields without pretending to parse Go."""
    facts: list[CodeFact] = []
    in_struct = False
    brace_depth = 0
    for line_match in re.finditer(r"^.*$", content, re.MULTILINE):
        raw_line = line_match.group()
        line = raw_line.strip()
        if not line:
            continue
        start = line_match.start() + len(raw_line) - len(raw_line.lstrip())
        package = re.match(r"package\s+[A-Za-z_]\w*\s*$", line)
        type_decl = re.match(r"type\s+[A-Za-z_]\w*(?:\s+struct\s*\{)?", line)
        func_decl = re.match(
            r"func\s+(?P<receiver>\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(",
            line,
        )
        if package or type_decl or func_decl:
            if type_decl:
                quote = f"type {type_decl.group(0).split()[1]}"
            elif func_decl:
                receiver = (func_decl.group("receiver") or "").strip()
                function_name = func_decl.group("name")
                quote = f"func {receiver + ' ' if receiver else ''}{function_name}"
            else:
                quote = line.split("{")[0].rstrip()
                function_name = None
            facts.append((quote, start, function_name if func_decl else None))
        if type_decl and "struct" in line and "{" in line:
            in_struct = True
            brace_depth = line.count("{") - line.count("}")
            if brace_depth <= 0:
                in_struct = False
            continue
        if in_struct:
            field = re.match(
                r"([A-Za-z_]\w*)\s+([^{}]+?)(?:\s+`[^`]+`)?$",
                line,
            )
            if field and not line.startswith(("//", "func ")):
                facts.append((f"Field {field.group(1)} {field.group(2).strip()}", start, None))
        brace_depth += line.count("{") - line.count("}")
        if in_struct and brace_depth <= 0:
            in_struct = False
    if facts:
        return [*facts, *_go_function_body_facts(content)]
    for match in re.finditer(r"^.+$", content, re.MULTILINE):
        quote = match.group().strip()
        if quote:
            return [(quote, match.start() + len(match.group()) - len(match.group().lstrip()), None)]
    raise ValueError("source contains no meaningful code")


def _go_function_body_facts(content: str) -> list[CodeFact]:
    """Keep a few exact body lines so method questions have implementation evidence."""
    lines = list(re.finditer(r"^.*$", content, re.MULTILINE))
    facts: list[CodeFact] = []
    declaration = re.compile(r"func\s+(?P<receiver>\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")
    for index, match in enumerate(lines):
        line = match.group().strip()
        function = declaration.match(line)
        if function is None or "{" not in line:
            continue
        function_name = function.group("name")
        depth = line.count("{") - line.count("}")
        if depth <= 0:
            continue
        included = 0
        for body_match in lines[index + 1 :]:
            body_raw = body_match.group()
            body_line = body_raw.strip()
            body_start = body_match.start() + len(body_raw) - len(body_raw.lstrip())
            if (
                body_line not in {"{", "}"}
                and bool(body_line)
                and not body_line.startswith("//")
                and included < 6
            ):
                facts.append((body_line, body_start, function_name))
                included += 1
            depth += body_line.count("{") - body_line.count("}")
            if depth <= 0:
                break
    return facts
