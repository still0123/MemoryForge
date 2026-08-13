"""Compile deterministic code plans into reviewable Wiki ChangeSets."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from memoryforge.code_models import (
    ArchitectureEdge,
    ArchitectureGraph,
    CitedStatement,
    CodeIndexSnapshot,
    CodeLocation,
    CodeRelation,
    CodeSymbol,
    CodeSymbolKind,
    ModuleNarrative,
    ModuleNode,
    ModulePlan,
)
from memoryforge.compiler import Compilation, _render_index
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    ChangeSetValidation,
)
from memoryforge.module_planner import build_architecture_graph, build_module_plan
from memoryforge.provider import OpenAICompatibleProvider, ProviderUnavailableError
from memoryforge.workspace import (
    Workspace,
    candidate_page_sources,
    get_git_checkout_readonly,
    list_current_git_source_versions,
    read_source_version_text,
)

_LOCATOR = re.compile(r"^chars:(?P<start>\d+)-(?P<end>\d+)$")
_NARRATIVE_SCHEMA_VERSION = 3
_MAX_NARRATIVE_CITATIONS = 64
_MAX_NARRATIVE_SYMBOLS = 24
_MAX_NARRATIVE_EDGES = 24
_MAX_SOURCE_EXCERPT_CHARS = 600
_MAX_READING_ENTRY_POINTS = 6
_TEST_FILE_SUFFIXES = ("_test.go", "_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
_STRING_LITERAL = re.compile(
    r"(?P<quote>['\"])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)",
    re.DOTALL,
)
_SENSITIVE_LITERAL_CONTEXT = re.compile(
    r"(?:password|passwd|token|secret|access[a-z0-9_]*key[a-z0-9_]*"
    r"|(?:^|[^a-z0-9])(?:ak|sk))\s*=\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _NarrativeCitation:
    index: int
    owner_module_id: str
    location: CodeLocation
    fact: str
    excerpt: str
    symbol_id: str | None = None
    relation_id: str | None = None


class CodeWikiCompilationError(ValueError):
    """Raised when code facts cannot safely produce a reviewable Wiki."""


def compile_code_wiki(
    workspace: Workspace | Path | str,
    snapshot: CodeIndexSnapshot,
    plan: ModulePlan,
    architecture: ArchitectureGraph | None = None,
    provider: OpenAICompatibleProvider | None = None,
    allow_local: bool = False,
) -> Compilation | None:
    """Compile one canonical code snapshot without writing the stable Wiki."""

    root = workspace.root if isinstance(workspace, Workspace) else Path(workspace)
    opened = Workspace.open_readonly(root)
    if provider is not None and not allow_local:
        raise CodeWikiCompilationError(
            "code module synthesis requires explicit local LLM authorization"
        )
    _validate_inputs(opened, snapshot, plan, architecture)
    graph = architecture or build_architecture_graph(snapshot, plan)
    source_texts = _validate_code_evidence(opened, snapshot)
    modules = _flatten_modules(plan.modules)
    modules_by_id = {module.module_id: module for module in modules}
    symbols_by_id = {symbol.symbol_id: symbol for symbol in snapshot.symbols}
    relations_by_id = {relation.relation_id: relation for relation in snapshot.relations}
    outgoing = _outgoing_edges(graph)

    all_candidates: dict[str, str] = {}
    for module in modules:
        if module.symbol_ids:
            all_candidates[module.wiki_path] = _render_source_module_page(
                module,
                symbols_by_id,
                modules_by_id,
                outgoing.get(module.module_id, ()),
                relations_by_id,
                snapshot,
                source_texts,
            )
        else:
            all_candidates[module.wiki_path] = _render_navigation_module_page(
                module,
                snapshot,
            )
    if provider is not None:
        _synthesize_module_narratives(
            opened,
            all_candidates,
            snapshot,
            plan,
            graph,
            modules,
            modules_by_id,
            symbols_by_id,
            relations_by_id,
            outgoing,
            source_texts,
            provider,
        )

    planned_paths = set(all_candidates)
    archived_paths = _stale_code_wiki_paths(opened, snapshot.repository_id) - planned_paths
    all_candidates["wiki/INDEX.md"] = _render_index(
        opened,
        all_candidates,
        removed_paths=archived_paths,
    )
    candidate_files = {
        path: content
        for path, content in all_candidates.items()
        if _stable_text(opened.root / path) != content
    }
    if not candidate_files and not archived_paths:
        return None

    page_sources = candidate_page_sources(candidate_files)
    changed_source_ids = tuple(
        sorted({source_id for source_ids in page_sources.values() for source_id in source_ids})
    )
    if any(source_id not in snapshot.source_versions for source_id in changed_source_ids):
        raise CodeWikiCompilationError("candidate page references a source outside the code index")

    operations: list[ChangeOperation] = []
    module_by_path = {module.wiki_path: module for module in modules}
    for path in sorted(candidate_files):
        if path == "wiki/INDEX.md":
            operations.append(
                ChangeOperation(
                    type=ChangeOperationType.UPDATE_PAGE,
                    path=path,
                    details={
                        "origin": "code_wiki",
                        "code_index_id": snapshot.index_id,
                        "module_plan_id": plan.plan_id,
                        "architecture_graph_id": graph.graph_id,
                    },
                )
            )
            continue
        module = module_by_path[path]
        operation_type = (
            ChangeOperationType.UPDATE_PAGE
            if (opened.root / path).is_file()
            else ChangeOperationType.CREATE_PAGE
        )
        relation_ids = tuple(
            relation_id
            for edge in outgoing.get(module.module_id, ())
            for relation_id in edge.relation_ids
        )
        operations.append(
            ChangeOperation(
                type=operation_type,
                path=path,
                details={
                    "origin": "code_wiki",
                    "code_index_id": snapshot.index_id,
                    "module_plan_id": plan.plan_id,
                    "architecture_graph_id": graph.graph_id,
                    "module_id": module.module_id,
                    "symbol_ids": list(module.symbol_ids),
                    "relation_ids": list(relation_ids),
                },
            )
        )
    for path in sorted(archived_paths):
        operations.append(
            ChangeOperation(
                type=ChangeOperationType.ARCHIVE_PAGE,
                path=path,
                details={
                    "origin": "code_wiki",
                    "code_index_id": snapshot.index_id,
                    "reason": "module is absent from the current deterministic plan",
                },
            )
        )

    base_commit = opened.current_commit()
    identity_parts = [
        base_commit,
        snapshot.index_id,
        plan.plan_id,
        graph.graph_id,
        *(
            f"{path}:{hashlib.sha256(content.encode()).hexdigest()}"
            for path, content in sorted(candidate_files.items())
        ),
        *(f"archive:{path}" for path in sorted(archived_paths)),
    ]
    changeset_id = "chg_" + hashlib.sha256("\n".join(identity_parts).encode()).hexdigest()[:20]
    return Compilation(
        changeset=ChangeSet(
            changeset_id=changeset_id,
            base_commit=base_commit,
            source_ids=changed_source_ids,
            source_versions={
                source_id: snapshot.source_versions[source_id] for source_id in changed_source_ids
            },
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
            validation=ChangeSetValidation(
                citation_coverage=1.0,
                unresolved_conflicts=0,
                schema_errors=0,
            ),
        ),
        candidate_files=candidate_files,
    )


def _synthesize_module_narratives(
    workspace: Workspace,
    all_candidates: dict[str, str],
    snapshot: CodeIndexSnapshot,
    plan: ModulePlan,
    graph: ArchitectureGraph,
    modules: tuple[ModuleNode, ...],
    modules_by_id: dict[str, ModuleNode],
    symbols_by_id: dict[str, CodeSymbol],
    relations_by_id: dict[str, CodeRelation],
    outgoing: dict[str, tuple[ArchitectureEdge, ...]],
    source_texts: dict[str, str],
    provider: OpenAICompatibleProvider,
) -> None:
    symbol_module_ids = {
        symbol_id: module.module_id for module in modules for symbol_id in module.symbol_ids
    }
    fact_hashes: dict[str, str] = {}
    narratives: dict[str, ModuleNarrative] = {}
    root_module_ids = {module.module_id for module in plan.modules}
    for module in _postorder_modules(plan.modules):
        input_hash = _module_narrative_input_hash(
            module,
            symbols_by_id,
            outgoing.get(module.module_id, ()),
            fact_hashes,
            narratives,
        )
        fact_hashes[module.module_id] = input_hash
        if not module.children:
            continue

        stable = _stable_text(workspace.root / module.wiki_path)
        stored_narrative = (
            _stored_module_narrative(stable)
            if stable is not None
            and _can_reuse_module_narrative(stable, snapshot, module, input_hash)
            else None
        )
        if stable is not None and stored_narrative is not None:
            narratives[module.module_id] = stored_narrative
            all_candidates[module.wiki_path] = stable
            continue

        subtree_ids = _subtree_module_ids(module)
        edges = tuple(
            sorted(
                (edge for edge in graph.edges if edge.source_module_id in subtree_ids),
                key=lambda edge: (
                    edge.type.value,
                    modules_by_id[edge.source_module_id].path,
                    modules_by_id[edge.target_module_id].path,
                ),
            )
        )
        citations = _module_narrative_citations(
            module,
            subtree_ids,
            edges,
            symbols_by_id,
            relations_by_id,
            symbol_module_ids,
            source_texts,
        )
        base = all_candidates[module.wiki_path]
        if not citations:
            all_candidates[module.wiki_path] = _with_synthesis_metadata(
                base,
                status="fallback",
                input_hash=input_hash,
                snapshot=snapshot,
                reason="no_owned_evidence",
            )
            continue

        messages = _module_narrative_messages(
            module,
            module.module_id in root_module_ids,
            edges,
            citations,
            modules_by_id,
            symbols_by_id,
            narratives,
        )
        try:
            narrative = _summarize_module_narrative(provider, messages)
        except ProviderUnavailableError:
            all_candidates[module.wiki_path] = _with_synthesis_metadata(
                base,
                status="fallback",
                input_hash=input_hash,
                snapshot=snapshot,
                reason="provider_unavailable",
            )
            continue
        except ValueError:
            all_candidates[module.wiki_path] = _with_synthesis_metadata(
                base,
                status="fallback",
                input_hash=input_hash,
                snapshot=snapshot,
                reason="provider_output_invalid",
            )
            continue
        if not _narrative_citations_are_owned(narrative, citations, subtree_ids):
            all_candidates[module.wiki_path] = _with_synthesis_metadata(
                base,
                status="fallback",
                input_hash=input_hash,
                snapshot=snapshot,
                reason="citation_ownership_invalid",
            )
            continue

        content, used_citations = _render_module_narrative(
            base,
            module,
            module.module_id in root_module_ids,
            narrative,
            edges,
            citations,
            modules_by_id,
            symbols_by_id,
        )
        narratives[module.module_id] = narrative
        all_candidates[module.wiki_path] = _with_synthesis_metadata(
            content,
            status="synthesized",
            input_hash=input_hash,
            snapshot=snapshot,
            citations=used_citations,
            narrative=narrative,
        )


def _summarize_module_narrative(
    provider: OpenAICompatibleProvider,
    messages: list[dict[str, str]],
) -> ModuleNarrative:
    """Retry one malformed or transient local model response before fallback."""
    try:
        return provider.summarize_code_module(messages)
    except (ProviderUnavailableError, ValueError):
        return provider.summarize_code_module(messages)


def _module_narrative_input_hash(
    module: ModuleNode,
    symbols_by_id: dict[str, CodeSymbol],
    outgoing: tuple[ArchitectureEdge, ...],
    child_hashes: dict[str, str],
    child_narratives: dict[str, ModuleNarrative],
) -> str:
    payload = {
        "schema_version": _NARRATIVE_SCHEMA_VERSION,
        "module": {
            "module_id": module.module_id,
            "path": module.path,
            "title": module.title,
            "summary": module.summary,
        },
        "symbols": [
            {
                "symbol_id": symbol.symbol_id,
                "signature_sha256": symbol.signature_sha256,
                "body_sha256": symbol.body_sha256,
                "source_version": symbol.location.source_version,
            }
            for symbol in (symbols_by_id[symbol_id] for symbol_id in module.symbol_ids)
        ],
        "edges": [edge.model_dump(mode="json") for edge in outgoing],
        "children": [
            {
                "module_id": child.module_id,
                "fact_hash": child_hashes[child.module_id],
                "narrative": (
                    child_narratives[child.module_id].model_dump(mode="json")
                    if child.module_id in child_narratives
                    else None
                ),
            }
            for child in module.children
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _can_reuse_module_narrative(
    content: str,
    snapshot: CodeIndexSnapshot,
    module: ModuleNode,
    input_hash: str,
) -> bool:
    fields = _frontmatter_fields(content)
    return (
        fields.get("repository_id") == snapshot.repository_id
        and fields.get("module_id") == module.module_id
        and fields.get("synthesis_input_sha256") == input_hash
        and fields.get("synthesis_status") == "synthesized"
    )


def _stored_module_narrative(content: str) -> ModuleNarrative | None:
    raw_narrative = _frontmatter_fields(content).get("synthesis_narrative")
    if raw_narrative is None:
        return None
    try:
        return ModuleNarrative.model_validate_json(raw_narrative)
    except ValueError:
        return None


def _module_narrative_citations(
    module: ModuleNode,
    subtree_ids: set[str],
    edges: tuple[ArchitectureEdge, ...],
    symbols_by_id: dict[str, CodeSymbol],
    relations_by_id: dict[str, CodeRelation],
    symbol_module_ids: dict[str, str],
    source_texts: dict[str, str],
) -> tuple[_NarrativeCitation, ...]:
    citations: list[_NarrativeCitation] = []
    seen_facts: set[tuple[str, str]] = set()

    def add_symbol(symbol: CodeSymbol) -> None:
        key = ("symbol", symbol.symbol_id)
        owner_module_id = symbol_module_ids[symbol.symbol_id]
        if (
            key in seen_facts
            or owner_module_id not in subtree_ids
            or len(citations) >= _MAX_NARRATIVE_CITATIONS
        ):
            return
        seen_facts.add(key)
        citations.append(
            _NarrativeCitation(
                index=len(citations),
                owner_module_id=owner_module_id,
                location=symbol.location,
                fact=(
                    f"symbol {symbol.qualified_name} ({symbol.kind.value}): "
                    f"{_display_signature(symbol)}"
                ),
                excerpt=_bounded_excerpt(source_texts, symbol.location),
                symbol_id=symbol.symbol_id,
            )
        )

    def add_relation(relation: CodeRelation) -> None:
        key = ("relation", relation.relation_id)
        owner_module_id = symbol_module_ids[relation.source_symbol_id]
        if (
            key in seen_facts
            or owner_module_id not in subtree_ids
            or len(citations) >= _MAX_NARRATIVE_CITATIONS
        ):
            return
        seen_facts.add(key)
        source_symbol = symbols_by_id[relation.source_symbol_id]
        target_symbol = symbols_by_id[relation.target_symbol_id]
        location = relation.evidence[0]
        citations.append(
            _NarrativeCitation(
                index=len(citations),
                owner_module_id=owner_module_id,
                location=location,
                fact=(
                    f"relation {source_symbol.qualified_name} -> "
                    f"{target_symbol.qualified_name} ({relation.type.value})"
                ),
                excerpt=_bounded_excerpt(source_texts, location),
                relation_id=relation.relation_id,
            )
        )

    subtree_symbols = tuple(
        sorted(
            (
                symbol
                for symbol in symbols_by_id.values()
                if symbol_module_ids[symbol.symbol_id] in subtree_ids
            ),
            key=lambda symbol: (
                symbol.location.relative_path,
                symbol.location.start_line,
                symbol.qualified_name,
            ),
        )
    )
    for child in module.children:
        child_ids = _subtree_module_ids(child)
        representative = next(
            (
                symbol
                for symbol in subtree_symbols
                if symbol_module_ids[symbol.symbol_id] in child_ids
            ),
            None,
        )
        if representative is not None:
            add_symbol(representative)
    for symbol_id in module.symbol_ids:
        add_symbol(symbols_by_id[symbol_id])
    for edge in edges:
        for relation_id in edge.relation_ids:
            add_relation(relations_by_id[relation_id])
    for symbol in subtree_symbols:
        add_symbol(symbol)
    return tuple(citations)


def _module_narrative_messages(
    module: ModuleNode,
    is_top_level_module: bool,
    edges: tuple[ArchitectureEdge, ...],
    citations: tuple[_NarrativeCitation, ...],
    modules_by_id: dict[str, ModuleNode],
    symbols_by_id: dict[str, CodeSymbol],
    child_narratives: dict[str, ModuleNarrative],
) -> list[dict[str, str]]:
    selected_symbols = [
        symbols_by_id[citation.symbol_id]
        for citation in citations
        if citation.symbol_id is not None
    ][:_MAX_NARRATIVE_SYMBOLS]
    payload = {
        "module": {
            "title": module.title,
            "path": module.path,
            "summary": module.summary,
            "is_top_level_module": is_top_level_module,
        },
        "children": [
            {
                "title": child.title,
                "path": child.path,
                "summary": child.summary,
                "narrative": (
                    {
                        "purpose": child_narratives[child.module_id].purpose.text,
                        "responsibilities": [
                            statement.text
                            for statement in child_narratives[child.module_id].responsibilities
                        ],
                        "key_flows": [
                            statement.text
                            for statement in child_narratives[child.module_id].key_flows
                        ],
                    }
                    if child.module_id in child_narratives
                    else None
                ),
            }
            for child in module.children
        ],
        "symbols": [
            {
                "qualified_name": symbol.qualified_name,
                "kind": symbol.kind.value,
                "signature": _display_signature(symbol),
            }
            for symbol in selected_symbols
        ],
        "architecture_edges": [
            {
                "source_module": modules_by_id[edge.source_module_id].path,
                "target_module": modules_by_id[edge.target_module_id].path,
                "type": edge.type.value,
                "relation_count": len(edge.relation_ids),
            }
            for edge in edges[:_MAX_NARRATIVE_EDGES]
        ],
        "citations": [
            {
                "index": citation.index,
                "source_path": citation.location.relative_path,
                "fact": citation.fact,
                "source_excerpt": citation.excerpt,
            }
            for citation in citations
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "Summarize only the supplied deterministic code facts. Return one JSON object "
                "with exactly purpose, responsibilities, and key_flows. purpose is a cited "
                "statement object; responsibilities and key_flows are non-empty arrays of cited "
                "statement objects. Every cited statement has exactly text and citation_indexes, "
                "and citation_indexes contains one or more valid zero-based indexes from the "
                "provided citations. Do not invent symbols, relations, module paths, or behavior. "
                "Use direct child narratives when present to synthesize the parent level, while "
                "grounding every new statement in the current citation table. Do not emit "
                "Markdown. Reply in concise Chinese. For a top-level module, purpose describes "
                "module scope, responsibilities cover its main children, and key_flows cover "
                "major data flow."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _narrative_citations_are_owned(
    narrative: ModuleNarrative,
    citations: tuple[_NarrativeCitation, ...],
    subtree_ids: set[str],
) -> bool:
    statements = (
        narrative.purpose,
        *narrative.responsibilities,
        *narrative.key_flows,
    )
    return all(
        index < len(citations) and citations[index].owner_module_id in subtree_ids
        for statement in statements
        for index in statement.citation_indexes
    )


def _render_module_narrative(
    base: str,
    module: ModuleNode,
    is_top_level_module: bool,
    narrative: ModuleNarrative,
    edges: tuple[ArchitectureEdge, ...],
    citations: tuple[_NarrativeCitation, ...],
    modules_by_id: dict[str, ModuleNode],
    symbols_by_id: dict[str, CodeSymbol],
) -> tuple[str, tuple[_NarrativeCitation, ...]]:
    lines = ["## 模块职责", ""]
    used_indexes: set[int] = set()
    if is_top_level_module:
        lines.extend(["### 模块概览", ""])
    _append_narrative_statement(lines, narrative.purpose, citations, used_indexes)
    if is_top_level_module:
        lines.extend(["", "### 主要子模块", ""])
    for statement in narrative.responsibilities:
        _append_narrative_statement(lines, statement, citations, used_indexes)

    entry_citations = [
        citation
        for citation in citations
        if citation.symbol_id is not None
        and symbols_by_id[citation.symbol_id].kind
        in {
            CodeSymbolKind.CLASS,
            CodeSymbolKind.FUNCTION,
            CodeSymbolKind.INTERFACE,
            CodeSymbolKind.METHOD,
            CodeSymbolKind.STRUCT,
        }
    ][:8]
    if entry_citations:
        lines.extend(["", "### 主要入口", ""])
        for citation in entry_citations:
            symbol = symbols_by_id[citation.symbol_id or ""]
            used_indexes.add(citation.index)
            lines.append(
                f"- `{symbol.qualified_name}`: "
                f"{_inline_code(_display_signature(symbol))} "
                f"{_narrative_reference(citation)}"
            )

    lines.extend(["", "## 子模块分工", ""])
    for child in module.children:
        child_ids = _subtree_module_ids(child)
        child_citation = next(
            (item for item in citations if item.owner_module_id in child_ids),
            None,
        )
        if child_citation is None:
            continue
        used_indexes.add(child_citation.index)
        lines.append(
            f"- [{child.title}]({_relative_link(module.wiki_path, child.wiki_path)}): "
            f"{child.summary} {_narrative_reference(child_citation)}"
        )

    lines.extend(["", "## 核心流程", ""])
    if is_top_level_module:
        lines.extend(["### 主要流程", ""])
    for statement in narrative.key_flows:
        _append_narrative_statement(lines, statement, citations, used_indexes)

    relation_citations = {
        citation.relation_id: citation for citation in citations if citation.relation_id is not None
    }
    dependency_lines: list[str] = []
    for edge in edges[:_MAX_NARRATIVE_EDGES]:
        edge_citation = next(
            (
                relation_citations.get(relation_id)
                for relation_id in edge.relation_ids
                if relation_citations.get(relation_id) is not None
            ),
            None,
        )
        if edge_citation is None:
            continue
        used_indexes.add(edge_citation.index)
        source = modules_by_id[edge.source_module_id].path
        target = modules_by_id[edge.target_module_id].path
        dependency_lines.append(
            f"- `{source}` → `{target}` ({edge.type.value}, "
            f"{len(edge.relation_ids)} relations) {_narrative_reference(edge_citation)}"
        )
    lines.extend(["", "## 依赖关系", "", *dependency_lines])

    used_citations = tuple(citations[index] for index in sorted(used_indexes))
    lines.extend(["", "## 依据来源", ""])
    for citation in used_citations:
        lines.append(
            f"[^{_narrative_label(citation)}]: source `{citation.location.source_id}` · "
            f"revision `{citation.location.source_version}` · "
            f"`{citation.location.locator}`"
        )
    return _insert_before_first_section(base, "\n".join(lines)), used_citations


def _append_narrative_statement(
    lines: list[str],
    statement: CitedStatement,
    citations: tuple[_NarrativeCitation, ...],
    used_indexes: set[int],
) -> None:
    indexes = statement.citation_indexes
    used_indexes.update(indexes)
    references = " ".join(_narrative_reference(citations[index]) for index in indexes)
    lines.append(f"- {_escape_narrative_text(statement.text)} {references}")


def _with_synthesis_metadata(
    content: str,
    *,
    status: Literal["synthesized", "fallback"],
    input_hash: str,
    snapshot: CodeIndexSnapshot,
    citations: tuple[_NarrativeCitation, ...] = (),
    narrative: ModuleNarrative | None = None,
    reason: str | None = None,
) -> str:
    updates = {
        "synthesis_status": status,
        "synthesis_input_sha256": input_hash,
    }
    if status == "synthesized":
        if narrative is None:
            raise CodeWikiCompilationError("synthesized module page is missing its narrative")
        source_ids = tuple(sorted({citation.location.source_id for citation in citations}))
        updates["citation_sources"] = json.dumps(source_ids, ensure_ascii=False)
        updates["citation_source_versions"] = json.dumps(
            {source_id: snapshot.source_versions[source_id] for source_id in source_ids},
            ensure_ascii=False,
            sort_keys=True,
        )
        updates["synthesis_narrative"] = narrative.model_dump_json()
        return _update_frontmatter(content, updates, remove=("synthesis_reason",))
    if reason is None:
        raise CodeWikiCompilationError("fallback module page is missing its reason")
    updates["synthesis_reason"] = reason
    return _update_frontmatter(
        _insert_before_first_section(content, _fallback_notice(reason)),
        updates,
        remove=("citation_sources", "citation_source_versions", "synthesis_narrative"),
    )


def _update_frontmatter(
    content: str,
    updates: dict[str, str],
    *,
    remove: tuple[str, ...] = (),
) -> str:
    if not content.startswith("---\n"):
        raise CodeWikiCompilationError("code module page is missing frontmatter")
    closing = content.find("\n---\n", 4)
    if closing < 0:
        raise CodeWikiCompilationError("code module page frontmatter is not closed")
    remaining = dict(updates)
    removed = set(remove)
    lines = []
    for line in content[4:closing].splitlines():
        key, separator, _value = line.partition(":")
        name = key.strip()
        if separator and name in removed:
            continue
        if separator and key.strip() in remaining:
            lines.append(f"{name}: {remaining.pop(name)}")
        else:
            lines.append(line)
    lines.extend(f"{key}: {value}" for key, value in remaining.items())
    return "---\n" + "\n".join(lines) + content[closing:]


def _insert_before_first_section(content: str, addition: str) -> str:
    first_section = content.find("\n## ")
    if first_section < 0:
        raise CodeWikiCompilationError("code module page is missing its reading sections")
    return (
        content[:first_section].rstrip()
        + "\n\n"
        + addition
        + "\n\n"
        + content[first_section + 1 :]
    )


def _fallback_notice(reason: str) -> str:
    messages = {
        "no_owned_evidence": "自动概览缺少本模块的可引用证据；本页保留确定性导航与代码事实。",
        "provider_unavailable": "自动概览暂未生成：模型服务不可用；下次编译会自动重试。",
        "provider_output_invalid": "自动概览未通过结构校验；下次编译会自动重试。",
        "citation_ownership_invalid": "自动概览未通过证据归属校验；下次编译会自动重试。",
    }
    return f"> {messages[reason]}"


def _postorder_modules(modules: tuple[ModuleNode, ...]) -> tuple[ModuleNode, ...]:
    ordered: list[ModuleNode] = []

    def collect(module: ModuleNode) -> None:
        for child in module.children:
            collect(child)
        ordered.append(module)

    for module in modules:
        collect(module)
    return tuple(ordered)


def _subtree_module_ids(module: ModuleNode) -> set[str]:
    return {item.module_id for item in (module, *_flatten_modules(module.children))}


def _bounded_excerpt(texts: dict[str, str], location: CodeLocation) -> str:
    excerpt = _redact_sensitive_literals(_excerpt(texts, location))
    return " ".join(excerpt.split())[:_MAX_SOURCE_EXCERPT_CHARS]


def _redact_sensitive_literals(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        literal = match["value"]
        context = value[max(0, match.start() - 96) : match.start()]
        if _SENSITIVE_LITERAL_CONTEXT.search(context) or _looks_sensitive_literal(literal):
            quote = match["quote"]
            return f"{quote}<redacted>{quote}"
        return match.group(0)

    return _STRING_LITERAL.sub(replace, value)


def _looks_sensitive_literal(value: str) -> bool:
    if value.upper().startswith(("AKLT", "LTAI")):
        return True
    return (
        len(value) >= 8
        and any(character.islower() for character in value)
        and any(character.isupper() for character in value)
        and any(character.isdigit() for character in value)
        and any(character in "!@#$%^&*+=" for character in value)
    )


def _display_signature(symbol: CodeSymbol) -> str:
    return _redact_sensitive_literals(" ".join(symbol.signature.split()))


def _display_symbol_evidence(
    symbol: CodeSymbol,
    source_texts: dict[str, str],
) -> tuple[str, CodeLocation]:
    signature = _display_signature(symbol)
    if "<redacted>" not in signature:
        return signature, symbol.location
    excerpt = _excerpt(source_texts, symbol.location)
    if symbol.kind in {CodeSymbolKind.FUNCTION, CodeSymbolKind.METHOD}:
        pattern = re.compile(rf"(?:async\s+)?def\s+{re.escape(symbol.display_name)}\b")
    elif symbol.kind is CodeSymbolKind.CLASS:
        pattern = re.compile(rf"class\s+{re.escape(symbol.display_name)}\b")
    else:
        pattern = re.compile(rf"\b{re.escape(symbol.display_name)}\b")
    match = pattern.search(excerpt)
    if match is None:
        raise CodeWikiCompilationError(
            f"sensitive code symbol lacks a safe evidence fragment: {symbol.qualified_name}"
        )
    return match.group(0), _narrow_location(
        symbol.location,
        excerpt,
        match.start(),
        match.end(),
    )


def _narrow_location(
    location: CodeLocation,
    excerpt: str,
    relative_start: int,
    relative_end: int,
) -> CodeLocation:
    locator = _LOCATOR.fullmatch(location.locator)
    if locator is None:
        raise CodeWikiCompilationError("code evidence locator is invalid")
    absolute_start = int(locator["start"]) + relative_start
    absolute_end = int(locator["start"]) + relative_end
    line_offset = excerpt[:relative_start].count("\n")
    end_line_offset = excerpt[:relative_end].count("\n")
    return location.model_copy(
        update={
            "locator": f"chars:{absolute_start}-{absolute_end}",
            "start_line": location.start_line + line_offset,
            "end_line": location.start_line + end_line_offset,
        }
    )


def _narrative_label(citation: _NarrativeCitation) -> str:
    return f"narrative-{citation.index + 1}"


def _narrative_reference(citation: _NarrativeCitation) -> str:
    return f"[^{_narrative_label(citation)}]"


def _escape_narrative_text(value: str) -> str:
    return (
        " ".join(value.split())
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _validate_inputs(
    workspace: Workspace,
    snapshot: CodeIndexSnapshot,
    plan: ModulePlan,
    architecture: ArchitectureGraph | None,
) -> None:
    repository = get_git_checkout_readonly(workspace.root, snapshot.repository_id)
    if repository.last_synced_commit != snapshot.commit_sha:
        raise CodeWikiCompilationError("code index is stale for the registered repository")
    expected_plan = build_module_plan(snapshot)
    if plan != expected_plan:
        raise CodeWikiCompilationError("module plan is not the deterministic plan for this index")
    expected_graph = build_architecture_graph(snapshot, plan)
    if architecture is not None and architecture != expected_graph:
        raise CodeWikiCompilationError(
            "architecture graph is not the deterministic graph for this plan"
        )

    current_sources = {
        source.source_id: source
        for source in list_current_git_source_versions(
            workspace.root,
            snapshot.repository_id,
        )
    }
    for source_id, source_version in snapshot.source_versions.items():
        current = current_sources.get(source_id)
        if (
            current is None
            or current.source_version != source_version
            or current.commit_sha != snapshot.commit_sha
        ):
            raise CodeWikiCompilationError("code index SourceVersion is no longer current")


def _validate_code_evidence(
    workspace: Workspace,
    snapshot: CodeIndexSnapshot,
) -> dict[str, str]:
    texts = {
        source_id: read_source_version_text(
            workspace.root,
            source_id=source_id,
            source_version=source_version,
        )
        for source_id, source_version in snapshot.source_versions.items()
    }
    for symbol in snapshot.symbols:
        excerpt = _excerpt(texts, symbol.location)
        if hashlib.sha256(excerpt.encode()).hexdigest() != symbol.body_sha256:
            raise CodeWikiCompilationError(
                f"code symbol evidence hash does not match: {symbol.qualified_name}"
            )
    for relation in snapshot.relations:
        for location in relation.evidence:
            if not _excerpt(texts, location):
                raise CodeWikiCompilationError(
                    f"code relation evidence is empty: {relation.relation_id}"
                )
    return texts


def _render_source_module_page(
    module: ModuleNode,
    symbols_by_id: dict[str, CodeSymbol],
    modules_by_id: dict[str, ModuleNode],
    outgoing: tuple[ArchitectureEdge, ...],
    relations_by_id: dict[str, CodeRelation],
    snapshot: CodeIndexSnapshot,
    source_texts: dict[str, str],
) -> str:
    symbols = [symbols_by_id[symbol_id] for symbol_id in module.symbol_ids]
    source_ids = tuple(sorted({symbol.location.source_id for symbol in symbols}))
    source_versions = {source_id: snapshot.source_versions[source_id] for source_id in source_ids}
    languages = tuple(sorted({symbol.language.value for symbol in symbols}))
    summary = f"{len(symbols)} verified code symbols in module {module.path}."
    lines = [
        "---",
        "generated: code_wiki",
        f"repository_id: {snapshot.repository_id}",
        f"module_id: {module.module_id}",
        f"title: {json.dumps(module.title, ensure_ascii=False)}",
        "type: concept",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        f"tags: {json.dumps(('code', 'module', *languages), ensure_ascii=False)}",
        f"sources: {json.dumps(source_ids, ensure_ascii=False)}",
        f"source_versions: {json.dumps(source_versions, ensure_ascii=False)}",
        "---",
        "",
        f"# {module.title}",
        "",
    ]
    footnotes = {
        symbol.symbol_id: f"code-{index}-{symbol.location.source_id[:8]}"
        for index, symbol in enumerate(symbols, start=1)
    }
    symbol_evidence = {
        symbol.symbol_id: _display_symbol_evidence(symbol, source_texts) for symbol in symbols
    }
    _append_source_reading_guide(lines, module, symbols, footnotes)
    lines.extend(
        [
            "## Module",
            "",
            f"- Path: `{module.path}`",
            f"- Languages: {', '.join(languages)}",
            f"- Verified symbols: {len(symbols)}",
            "",
        ]
    )
    _append_child_modules(lines, module)
    dependency_citations = _append_source_architecture(
        lines,
        module,
        modules_by_id,
        outgoing,
        relations_by_id,
        set(source_ids),
        symbols_by_id,
        source_texts,
    )
    lines.extend(["## Verified symbols", ""])
    for symbol in symbols:
        footnote = footnotes[symbol.symbol_id]
        signature, _location = symbol_evidence[symbol.symbol_id]
        lines.append(
            f"- `{symbol.qualified_name}` ({symbol.kind.value}): "
            f"{_inline_code(signature)} [^{footnote}]"
        )
    lines.extend(["", "## Sources", ""])
    for symbol in symbols:
        footnote = footnotes[symbol.symbol_id]
        _signature, location = symbol_evidence[symbol.symbol_id]
        lines.append(
            f"[^{footnote}]: source `{location.source_id}` · revision "
            f"`{location.source_version}` · `{location.locator}`"
        )
    for label, location in dependency_citations:
        lines.append(
            f"[^{label}]: source `{location.source_id}` · revision "
            f"`{location.source_version}` · `{location.locator}`"
        )
    content = "\n".join(lines) + "\n"
    _validate_rendered_architecture(content, outgoing)
    return content


def _render_navigation_module_page(
    module: ModuleNode,
    snapshot: CodeIndexSnapshot,
) -> str:
    summary = f"Navigation for deterministic code module {module.path}."
    lines = [
        "---",
        "generated: code_module_overview",
        f"repository_id: {snapshot.repository_id}",
        f"module_id: {module.module_id}",
        f"title: {json.dumps(module.title, ensure_ascii=False)}",
        "type: entity",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        "---",
        "",
        f"# {module.title}",
        "",
        "## 快速阅读",
        "",
        "- 这是代码目录页；本页不直接承载代码符号。",
        "- 先从下方子模块进入，再按需要回到本页查看层级关系。",
        "",
        "## Module",
        "",
        f"- Path: `{module.path}`",
        "- This page is generated from the deterministic module hierarchy.",
        "",
    ]
    _append_navigation_architecture(lines, module)
    _append_child_modules(lines, module)
    return "\n".join(lines) + "\n"


def _append_source_reading_guide(
    lines: list[str],
    module: ModuleNode,
    symbols: list[CodeSymbol],
    footnotes: dict[str, str],
) -> None:
    """Put deterministic orientation ahead of the exhaustive symbol ledger."""
    lines.extend(["## 快速阅读", ""])
    if module.children:
        children = list(module.children)
        child_links = ", ".join(
            f"[{child.title}]({_relative_link(module.wiki_path, child.wiki_path)})"
            for child in children[:_MAX_READING_ENTRY_POINTS]
        )
        more = f" 等 {len(children)} 个" if len(children) > _MAX_READING_ENTRY_POINTS else ""
        lines.append(
            f"- 本模块有 {len(symbols)} 个直接代码符号和 {len(children)} 个子模块；"
            f"可先进入 {child_links}{more}。"
        )
    else:
        lines.append("- 这是叶子模块；先看下方优先入口，再按需展开完整符号清单。")

    candidates = [
        symbol
        for symbol in symbols
        if symbol.visibility.value == "public"
        and symbol.kind
        in {
            CodeSymbolKind.CLASS,
            CodeSymbolKind.FUNCTION,
            CodeSymbolKind.INTERFACE,
            CodeSymbolKind.METHOD,
            CodeSymbolKind.STRUCT,
        }
    ]
    if not candidates:
        candidates = [
            symbol
            for symbol in symbols
            if symbol.kind
            in {
                CodeSymbolKind.CLASS,
                CodeSymbolKind.FUNCTION,
                CodeSymbolKind.INTERFACE,
                CodeSymbolKind.METHOD,
                CodeSymbolKind.STRUCT,
            }
        ]
    preferred = [symbol for symbol in candidates if not _is_test_symbol(symbol)] or candidates
    if preferred:
        entry_points = ", ".join(
            f"`{symbol.qualified_name}` [^{footnotes[symbol.symbol_id]}]"
            for symbol in preferred[:_MAX_READING_ENTRY_POINTS]
        )
        lines.append(f"- 优先入口：{entry_points}。")
    lines.append("- 完整符号、依赖关系和源码定位保留在本页后半部分。")
    lines.append("")


def _is_test_symbol(symbol: CodeSymbol) -> bool:
    is_test_file = symbol.location.relative_path.endswith(_TEST_FILE_SUFFIXES)
    return is_test_file or symbol.display_name.startswith(("Test", "test_"))


def _append_child_modules(lines: list[str], module: ModuleNode) -> None:
    if not module.children:
        return
    lines.extend(["## Child modules", ""])
    for child in module.children:
        lines.append(f"- [{child.title}]({_relative_link(module.wiki_path, child.wiki_path)})")
    lines.append("")


def _append_source_architecture(
    lines: list[str],
    module: ModuleNode,
    modules_by_id: dict[str, ModuleNode],
    outgoing: tuple[ArchitectureEdge, ...],
    relations_by_id: dict[str, CodeRelation],
    source_ids: set[str],
    symbols_by_id: dict[str, CodeSymbol],
    source_texts: dict[str, str],
) -> tuple[tuple[str, CodeLocation], ...]:
    if not outgoing:
        return ()
    nodes = {module.module_id: module}
    for edge in outgoing:
        nodes[edge.target_module_id] = modules_by_id[edge.target_module_id]
    lines.extend(["## Architecture", "", "```mermaid", "flowchart LR"])
    for node in sorted(nodes.values(), key=lambda item: item.path):
        lines.append(f"  m_{node.module_id}[{json.dumps(node.title, ensure_ascii=False)}]")
    for edge in outgoing:
        lines.append(f"  %% architecture-edge:{edge.edge_id}")
        lines.append(
            f"  m_{edge.source_module_id} -->|{edge.type.value} "
            f"({len(edge.relation_ids)})| m_{edge.target_module_id}"
        )
    lines.extend(["```", "", "## Verified dependencies", ""])
    citations: list[tuple[str, CodeLocation]] = []
    for edge in outgoing:
        for relation_id in edge.relation_ids:
            relation = relations_by_id.get(relation_id)
            if relation is None or relation.source_symbol_id not in module.symbol_ids:
                raise CodeWikiCompilationError(
                    f"architecture edge contains an invalid relation: {edge.edge_id}"
                )
            evidence = relation.evidence[0]
            if evidence.source_id not in source_ids:
                raise CodeWikiCompilationError(
                    f"architecture evidence is not owned by its source module: {relation_id}"
                )
            label = f"relation-{relation_id}"
            source_symbol = symbols_by_id[relation.source_symbol_id]
            target_symbol = symbols_by_id[relation.target_symbol_id]
            raw_quote = _excerpt(source_texts, evidence)
            evidence_quote = _redact_sensitive_literals(raw_quote)
            display_evidence = evidence
            if evidence_quote != raw_quote:
                start = raw_quote.find(target_symbol.display_name)
                if start < 0:
                    raise CodeWikiCompilationError(
                        f"sensitive code relation lacks a safe evidence fragment: {relation_id}"
                    )
                evidence_quote = target_symbol.display_name
                display_evidence = _narrow_location(
                    evidence,
                    raw_quote,
                    start,
                    start + len(evidence_quote),
                )
            citations.append((label, display_evidence))
            lines.append(
                f"- `{source_symbol.qualified_name} -> {target_symbol.qualified_name}` "
                f"({relation.type.value}): "
                f"{json.dumps(evidence_quote, ensure_ascii=False)} [^{label}]"
            )
    lines.append("")
    return tuple(citations)


def _append_navigation_architecture(lines: list[str], module: ModuleNode) -> None:
    if not module.children:
        return
    lines.extend(["## Architecture", "", "```mermaid", "flowchart TD"])
    lines.append(f"  m_{module.module_id}[{json.dumps(module.title, ensure_ascii=False)}]")
    for child in sorted(module.children, key=lambda item: item.path):
        lines.append(f"  m_{child.module_id}[{json.dumps(child.title, ensure_ascii=False)}]")
        lines.append(f"  m_{module.module_id} -. contains .-> m_{child.module_id}")
    lines.extend(["```", ""])


def _validate_rendered_architecture(
    content: str,
    outgoing: tuple[ArchitectureEdge, ...],
) -> None:
    if not outgoing:
        return
    if "```mermaid\nflowchart LR\n" not in content:
        raise CodeWikiCompilationError("source module architecture is missing Mermaid")
    for edge in outgoing:
        marker = f"architecture-edge:{edge.edge_id}"
        if content.count(marker) != 1:
            raise CodeWikiCompilationError(
                f"architecture edge is not rendered once: {edge.edge_id}"
            )
        for relation_id in edge.relation_ids:
            label = f"relation-{relation_id}"
            if f"[^{label}]" not in content or f"[^{label}]: source " not in content:
                raise CodeWikiCompilationError(
                    f"architecture relation is missing a citation: {relation_id}"
                )


def _outgoing_edges(
    graph: ArchitectureGraph,
) -> dict[str, tuple[ArchitectureEdge, ...]]:
    grouped: dict[str, list[ArchitectureEdge]] = {}
    for edge in graph.edges:
        grouped.setdefault(edge.source_module_id, []).append(edge)
    return {
        source_id: tuple(
            sorted(
                edges,
                key=lambda edge: (edge.type.value, edge.target_module_id),
            )
        )
        for source_id, edges in grouped.items()
    }


def _stale_code_wiki_paths(
    workspace: Workspace,
    repository_id: str,
) -> set[str]:
    pages_root = workspace.root / "wiki/pages/code"
    if not pages_root.is_dir() or pages_root.is_symlink():
        return set()
    stale: set[str] = set()
    for path in pages_root.rglob("*.md"):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_text(encoding="utf-8")
        fields = _frontmatter_fields(content)
        if fields.get("repository_id") == repository_id and fields.get("generated") in {
            "code_wiki",
            "code_module_overview",
        }:
            stale.add(path.relative_to(workspace.root).as_posix())
    return stale


def _frontmatter_fields(content: str) -> dict[str, str]:
    if not content.startswith("---\n"):
        return {}
    closing = content.find("\n---\n", 4)
    if closing < 0:
        return {}
    return {
        key.strip(): value.strip()
        for line in content[4:closing].splitlines()
        for key, separator, value in (line.partition(":"),)
        if separator
    }


def _flatten_modules(modules: tuple[ModuleNode, ...]) -> tuple[ModuleNode, ...]:
    flattened: list[ModuleNode] = []

    def collect(module: ModuleNode) -> None:
        flattened.append(module)
        for child in module.children:
            collect(child)

    for module in modules:
        collect(module)
    return tuple(flattened)


def _excerpt(texts: dict[str, str], location: CodeLocation) -> str:
    text = texts.get(location.source_id)
    match = _LOCATOR.fullmatch(location.locator)
    if text is None or match is None:
        raise CodeWikiCompilationError("code evidence locator is invalid")
    start = int(match["start"])
    end = int(match["end"])
    if start >= end or end > len(text):
        raise CodeWikiCompilationError("code evidence locator is outside SourceVersion")
    return text[start:end]


def _relative_link(source_path: str, target_path: str) -> str:
    source_parent = PurePosixPath(source_path).parent.as_posix()
    return posixpath.relpath(target_path, start=source_parent)


def _stable_text(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return path.read_text(encoding="utf-8")


def _inline_code(value: str) -> str:
    """Use a Markdown delimiter that cannot collide with code contents."""
    delimiter = "`" * (max((len(item) for item in re.findall(r"`+", value)), default=0) + 1)
    return f"{delimiter}{value}{delimiter}"
