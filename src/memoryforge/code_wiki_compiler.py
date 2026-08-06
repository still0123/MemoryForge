"""Compile deterministic code plans into reviewable Wiki ChangeSets."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from pathlib import Path, PurePosixPath

from memoryforge.code_models import (
    ArchitectureEdge,
    ArchitectureGraph,
    CodeIndexSnapshot,
    CodeLocation,
    CodeRelation,
    CodeSymbol,
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
from memoryforge.workspace import (
    Workspace,
    candidate_page_sources,
    get_git_checkout_readonly,
    list_current_git_source_versions,
    read_source_version_text,
)

_LOCATOR = re.compile(r"^chars:(?P<start>\d+)-(?P<end>\d+)$")


class CodeWikiCompilationError(ValueError):
    """Raised when code facts cannot safely produce a reviewable Wiki."""


def compile_code_wiki(
    workspace: Workspace | Path | str,
    snapshot: CodeIndexSnapshot,
    plan: ModulePlan,
    architecture: ArchitectureGraph | None = None,
) -> Compilation | None:
    """Compile one canonical code snapshot without writing the stable Wiki."""

    root = workspace.root if isinstance(workspace, Workspace) else Path(workspace)
    opened = Workspace.open_readonly(root)
    _validate_inputs(opened, snapshot, plan, architecture)
    graph = architecture or build_architecture_graph(snapshot, plan)
    _validate_code_evidence(opened, snapshot)
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
            )
        else:
            all_candidates[module.wiki_path] = _render_navigation_module_page(
                module,
                snapshot,
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
) -> None:
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


def _render_source_module_page(
    module: ModuleNode,
    symbols_by_id: dict[str, CodeSymbol],
    modules_by_id: dict[str, ModuleNode],
    outgoing: tuple[ArchitectureEdge, ...],
    relations_by_id: dict[str, CodeRelation],
    snapshot: CodeIndexSnapshot,
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
        "## Module",
        "",
        f"- Path: `{module.path}`",
        f"- Languages: {', '.join(languages)}",
        f"- Verified symbols: {len(symbols)}",
        "",
    ]
    _append_child_modules(lines, module)
    dependency_citations = _append_source_architecture(
        lines,
        module,
        modules_by_id,
        outgoing,
        relations_by_id,
        set(source_ids),
    )
    lines.extend(["## Verified symbols", ""])
    for index, symbol in enumerate(symbols, start=1):
        footnote = f"code-{index}-{symbol.location.source_id[:8]}"
        signature = " ".join(symbol.signature.split())
        lines.append(
            f"- `{symbol.qualified_name}` ({symbol.kind.value}): "
            f"`{_escape_inline_code(signature)}` [^{footnote}]"
        )
    lines.extend(["", "## Sources", ""])
    for index, symbol in enumerate(symbols, start=1):
        footnote = f"code-{index}-{symbol.location.source_id[:8]}"
        lines.append(
            f"[^{footnote}]: source `{symbol.location.source_id}` · revision "
            f"`{symbol.location.source_version}` · `{symbol.location.locator}`"
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
        "## Module",
        "",
        f"- Path: `{module.path}`",
        "- This page is generated from the deterministic module hierarchy.",
        "",
    ]
    _append_navigation_architecture(lines, module)
    _append_child_modules(lines, module)
    return "\n".join(lines) + "\n"


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
        target = modules_by_id[edge.target_module_id]
        references: list[str] = []
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
            references.append(f"[^{label}]")
            citations.append((label, evidence))
        lines.append(
            f"- `{edge.type.value}` → "
            f"[{target.title}]({_relative_link(module.wiki_path, target.wiki_path)}) "
            f"({len(edge.relation_ids)} verified relation"
            f"{'s' if len(edge.relation_ids) != 1 else ''}) " + " ".join(references)
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


def _escape_inline_code(value: str) -> str:
    return value.replace("`", "\\`")
