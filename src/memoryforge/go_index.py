"""Go Tree-sitter adapter for deterministic MemoryForge code indexes."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import tree_sitter_go
from tree_sitter import Language, Node, Parser, Tree

from memoryforge._code_index_common import (
    CodeIndexError,
    ParsedCodeSource,
    RelationEvidence,
    append_relation_evidence,
    build_relations,
    new_relation_evidence,
    node_key,
    node_location,
    node_text,
    source_location,
)
from memoryforge.code_models import (
    CodeIndexSnapshot,
    CodeLanguage,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
    CodeVisibility,
    make_code_index_id,
    make_code_symbol_id,
)
from memoryforge.workspace import (
    Workspace,
    get_git_checkout_readonly,
    list_current_git_source_versions,
    read_source_version_text,
)

_GO_LANGUAGE = Language(tree_sitter_go.language())


@dataclass(frozen=True)
class _GoSource(ParsedCodeSource):
    package_name: str
    package_scope: str


@dataclass(frozen=True)
class _GoDefinition:
    node: Node
    symbol: CodeSymbol
    package_symbol_id: str
    receiver_name: str | None = None
    receiver_type: str | None = None


def _build_go_code_index(
    workspace: Workspace | Path | str,
    repository_id: str,
) -> CodeIndexSnapshot:
    """Index current immutable Go sources from one completed Git sync."""

    root = workspace.root if isinstance(workspace, Workspace) else Path(workspace)
    opened = Workspace.open_readonly(root)
    repository = get_git_checkout_readonly(opened.root, repository_id)
    if repository.last_synced_commit is None:
        raise CodeIndexError("Git repository must be synced before code indexing")

    records = [
        source
        for source in list_current_git_source_versions(opened.root, repository_id)
        if source.relative_path.endswith(".go") and "code" in source.tags
    ]
    if any(source.commit_sha != repository.last_synced_commit for source in records):
        raise CodeIndexError("current Go sources do not belong to the last synced commit")

    parsed_sources: list[tuple[_GoSource, Tree, CodeSymbol]] = []
    definitions: list[_GoDefinition] = []
    relation_evidence = new_relation_evidence()
    source_versions: dict[str, int] = {}

    for record in records:
        text = read_source_version_text(
            opened.root,
            source_id=record.source_id,
            source_version=record.source_version,
        )
        if hashlib.sha256(text.encode()).hexdigest() != record.content_sha256:
            raise CodeIndexError("verified SourceVersion text does not match Git source metadata")
        if not text.strip():
            continue
        content = text.encode()
        tree = Parser(_GO_LANGUAGE).parse(content)
        if tree.root_node.has_error:
            raise CodeIndexError(f"Go source contains syntax errors: {record.relative_path}")
        package_name = _package_name(tree.root_node, content, record.relative_path)
        source = _GoSource(
            record=record,
            text=text,
            content=content,
            package_name=package_name,
            package_scope=_package_scope(record.relative_path, package_name),
        )
        package_symbol = _package_symbol(
            repository_id,
            repository.last_synced_commit,
            source,
        )
        parsed_sources.append((source, tree, package_symbol))
        definitions.extend(
            _collect_go_definitions(
                repository_id,
                repository.last_synced_commit,
                source,
                tree.root_node,
                package_symbol,
            )
        )
        source_versions[record.source_id] = record.source_version

    _add_contains_relations(definitions, relation_evidence)
    _add_go_calls(parsed_sources, definitions, relation_evidence)

    symbols = [
        *(package_symbol for _source, _tree, package_symbol in parsed_sources),
        *(definition.symbol for definition in definitions),
    ]
    symbols.sort(
        key=lambda symbol: (
            symbol.location.relative_path,
            symbol.location.start_line,
            symbol.qualified_name,
        )
    )
    return CodeIndexSnapshot(
        index_id=make_code_index_id(repository_id, repository.last_synced_commit),
        repository_id=repository_id,
        commit_sha=repository.last_synced_commit,
        languages=(CodeLanguage.GO,),
        source_versions=source_versions,
        symbols=tuple(symbols),
        relations=build_relations(
            repository_id,
            repository.last_synced_commit,
            relation_evidence,
        ),
    )


def _collect_go_definitions(
    repository_id: str,
    commit_sha: str,
    source: _GoSource,
    root: Node,
    package_symbol: CodeSymbol,
) -> list[_GoDefinition]:
    definitions: list[_GoDefinition] = []
    for node in _walk(root):
        if node.type == "type_spec":
            name_node = node.child_by_field_name("name")
            type_node = node.child_by_field_name("type")
            if name_node is None or type_node is None:
                continue
            name = node_text(source, name_node)
            kind = {
                "struct_type": CodeSymbolKind.STRUCT,
                "interface_type": CodeSymbolKind.INTERFACE,
            }.get(type_node.type, CodeSymbolKind.TYPE_ALIAS)
            definitions.append(
                _GoDefinition(
                    node=node,
                    symbol=_go_symbol(
                        repository_id,
                        commit_sha,
                        source,
                        node,
                        kind,
                        f"{source.package_scope}.{name}",
                        name,
                    ),
                    package_symbol_id=package_symbol.symbol_id,
                )
            )
        elif node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(source, name_node)
            definitions.append(
                _GoDefinition(
                    node=node,
                    symbol=_go_symbol(
                        repository_id,
                        commit_sha,
                        source,
                        node,
                        CodeSymbolKind.FUNCTION,
                        f"{source.package_scope}.{name}",
                        name,
                    ),
                    package_symbol_id=package_symbol.symbol_id,
                )
            )
        elif node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            receiver = node.child_by_field_name("receiver")
            if name_node is None or receiver is None:
                continue
            receiver_name, receiver_type = _receiver(source, receiver)
            if receiver_type is None:
                continue
            name = node_text(source, name_node)
            definitions.append(
                _GoDefinition(
                    node=node,
                    symbol=_go_symbol(
                        repository_id,
                        commit_sha,
                        source,
                        node,
                        CodeSymbolKind.METHOD,
                        f"{source.package_scope}.{receiver_type}.{name}",
                        name,
                    ),
                    package_symbol_id=package_symbol.symbol_id,
                    receiver_name=receiver_name,
                    receiver_type=receiver_type,
                )
            )
    return definitions


def _add_contains_relations(
    definitions: list[_GoDefinition],
    relation_evidence: RelationEvidence,
) -> None:
    types = {
        (
            definition.symbol.qualified_name.rsplit(".", 1)[0],
            definition.symbol.display_name,
        ): definition.symbol
        for definition in definitions
        if definition.symbol.kind in {CodeSymbolKind.STRUCT, CodeSymbolKind.INTERFACE}
    }
    for definition in definitions:
        parent_id = definition.package_symbol_id
        if definition.symbol.kind is CodeSymbolKind.METHOD and definition.receiver_type is not None:
            package_scope = definition.symbol.qualified_name.rsplit(".", 2)[0]
            receiver = types.get((package_scope, definition.receiver_type))
            if receiver is not None:
                parent_id = receiver.symbol_id
        append_relation_evidence(
            relation_evidence,
            CodeRelationType.CONTAINS,
            parent_id,
            definition.symbol.symbol_id,
            definition.symbol.location,
        )


def _add_go_calls(
    parsed_sources: list[tuple[_GoSource, Tree, CodeSymbol]],
    definitions: list[_GoDefinition],
    relation_evidence: RelationEvidence,
) -> None:
    definitions_by_node = {
        (
            definition.symbol.location.source_id,
            node_key(definition.node),
        ): definition
        for definition in definitions
        if definition.symbol.kind in {CodeSymbolKind.FUNCTION, CodeSymbolKind.METHOD}
    }
    functions = {
        (
            definition.symbol.qualified_name.rsplit(".", 1)[0],
            definition.symbol.display_name,
        ): definition.symbol
        for definition in definitions
        if definition.symbol.kind is CodeSymbolKind.FUNCTION
    }
    methods: dict[tuple[str, str | None, str], CodeSymbol] = {
        (
            definition.symbol.qualified_name.rsplit(".", 2)[0],
            definition.receiver_type,
            definition.symbol.display_name,
        ): definition.symbol
        for definition in definitions
        if definition.symbol.kind is CodeSymbolKind.METHOD and definition.receiver_type is not None
    }

    for source, tree, _package_symbol in parsed_sources:
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            caller = _enclosing_go_definition(node, source, definitions_by_node)
            if caller is None:
                continue
            target = _resolve_go_call(
                node,
                source,
                caller,
                functions=functions,
                methods=methods,
            )
            if target is None:
                continue
            append_relation_evidence(
                relation_evidence,
                CodeRelationType.CALLS,
                caller.symbol.symbol_id,
                target.symbol_id,
                node_location(source, node),
            )


def _resolve_go_call(
    call: Node,
    source: _GoSource,
    caller: _GoDefinition,
    *,
    functions: dict[tuple[str, str], CodeSymbol],
    methods: dict[tuple[str, str | None, str], CodeSymbol],
) -> CodeSymbol | None:
    function = call.child_by_field_name("function")
    if function is None:
        return None
    if function.type == "identifier":
        return functions.get((source.package_scope, node_text(source, function)))
    if function.type != "selector_expression":
        return None
    operand = function.child_by_field_name("operand")
    field = function.child_by_field_name("field")
    if operand is None or field is None:
        return None
    operand_name = node_text(source, operand)
    method_name = node_text(source, field)
    if caller.receiver_name is not None and operand_name == caller.receiver_name:
        return methods.get((source.package_scope, caller.receiver_type, method_name))
    return methods.get((source.package_scope, operand_name.lstrip("*"), method_name))


def _enclosing_go_definition(
    node: Node,
    source: _GoSource,
    definitions_by_node: dict[tuple[str, tuple[str, int, int]], _GoDefinition],
) -> _GoDefinition | None:
    parent = node.parent
    while parent is not None:
        if parent.type in {"function_declaration", "method_declaration"}:
            return definitions_by_node.get((source.record.source_id, node_key(parent)))
        parent = parent.parent
    return None


def _package_symbol(
    repository_id: str,
    commit_sha: str,
    source: _GoSource,
) -> CodeSymbol:
    filename = PurePosixPath(source.record.relative_path).name
    qualified_name = f"{source.package_scope}@{filename}"
    signature = f"package {source.package_name}"
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            repository_id,
            source.record.relative_path,
            CodeLanguage.GO,
            CodeSymbolKind.PACKAGE,
            qualified_name,
        ),
        repository_id=repository_id,
        commit_sha=commit_sha,
        language=CodeLanguage.GO,
        kind=CodeSymbolKind.PACKAGE,
        qualified_name=qualified_name,
        display_name=source.package_name,
        visibility=CodeVisibility.PUBLIC,
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(source.content).hexdigest(),
        location=source_location(source),
    )


def _go_symbol(
    repository_id: str,
    commit_sha: str,
    source: _GoSource,
    node: Node,
    kind: CodeSymbolKind,
    qualified_name: str,
    display_name: str,
) -> CodeSymbol:
    signature = _go_signature(source, node)
    body = source.content[node.start_byte : node.end_byte]
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            repository_id,
            source.record.relative_path,
            CodeLanguage.GO,
            kind,
            qualified_name,
        ),
        repository_id=repository_id,
        commit_sha=commit_sha,
        language=CodeLanguage.GO,
        kind=kind,
        qualified_name=qualified_name,
        display_name=display_name,
        visibility=(
            CodeVisibility.PUBLIC if display_name[:1].isupper() else CodeVisibility.PRIVATE
        ),
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(body).hexdigest(),
        location=node_location(source, node),
    )


def _go_signature(source: _GoSource, node: Node) -> str:
    body = node.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else node.end_byte
    signature = source.content[node.start_byte : end_byte].decode("utf-8").strip()
    if not signature:
        raise CodeIndexError(f"Go definition has no signature: {source.record.relative_path}")
    return signature


def _package_name(root: Node, content: bytes, relative_path: str) -> str:
    for child in root.children:
        if child.type != "package_clause":
            continue
        for package_child in child.children:
            if package_child.type == "package_identifier":
                return content[package_child.start_byte : package_child.end_byte].decode("utf-8")
    raise CodeIndexError(f"Go source has no package clause: {relative_path}")


def _package_scope(relative_path: str, package_name: str) -> str:
    parent = PurePosixPath(relative_path).parent
    return ".".join(parent.parts) if parent.parts else package_name


def _receiver(source: _GoSource, receiver: Node) -> tuple[str | None, str | None]:
    name_node = _first_descendant(receiver, {"identifier"})
    type_node = _first_descendant(receiver, {"type_identifier"})
    return (
        node_text(source, name_node) if name_node is not None else None,
        node_text(source, type_node) if type_node is not None else None,
    )


def _first_descendant(node: Node, types: set[str]) -> Node | None:
    for child in node.children:
        if child.type in types:
            return child
        nested = _first_descendant(child, types)
        if nested is not None:
            return nested
    return None


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)
