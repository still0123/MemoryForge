"""TypeScript/TSX Tree-sitter adapter for deterministic code indexes."""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Tree

from memoryforge.code._code_index_common import (
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
from memoryforge.code.code_models import (
    CodeIndexSnapshot,
    CodeLanguage,
    CodeRelationType,
    CodeSymbol,
    CodeSymbolKind,
    CodeVisibility,
    make_code_index_id,
    make_code_symbol_id,
)
from memoryforge.storage.workspace import (
    Workspace,
    get_git_checkout_readonly,
    list_current_git_source_versions,
    read_source_version_text,
)

_TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
_TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())
_TYPE_PARAMETER_VARIANCE = re.compile(rb"(?<=[<,])\s*(?:(?:in|out)\s+){1,2}(?=[A-Za-z_$])")
_LINE_TYPE_PARAMETER_VARIANCE = re.compile(rb"(?m)^[ \t]*(?:(?:in|out)[ \t]+){1,2}(?=[A-Za-z_$])")
_FUNCTION_NODES = {
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
}
_TYPE_NODES = {
    "class_declaration": CodeSymbolKind.CLASS,
    "abstract_class_declaration": CodeSymbolKind.CLASS,
    "interface_declaration": CodeSymbolKind.INTERFACE,
    "type_alias_declaration": CodeSymbolKind.TYPE_ALIAS,
}


@dataclass(frozen=True)
class _TypeScriptSource(ParsedCodeSource):
    module_scope: str


@dataclass(frozen=True)
class _TypeScriptDefinition:
    node: Node
    symbol: CodeSymbol
    module_symbol_id: str
    class_symbol_id: str | None = None


def _build_typescript_code_index(
    workspace: Workspace | Path | str,
    repository_id: str,
) -> CodeIndexSnapshot:
    """Index current immutable TypeScript and TSX sources."""

    root = workspace.root if isinstance(workspace, Workspace) else Path(workspace)
    opened = Workspace.open_readonly(root)
    repository = get_git_checkout_readonly(opened.root, repository_id)
    if repository.last_synced_commit is None:
        raise CodeIndexError("Git repository must be synced before code indexing")

    records = [
        source
        for source in list_current_git_source_versions(opened.root, repository_id)
        if PurePosixPath(source.relative_path).suffix in {".ts", ".tsx"} and "code" in source.tags
    ]
    if any(source.commit_sha != repository.last_synced_commit for source in records):
        raise CodeIndexError("current TypeScript sources do not belong to the last synced commit")

    parsed_sources: list[tuple[_TypeScriptSource, Tree, CodeSymbol]] = []
    definitions: list[_TypeScriptDefinition] = []
    source_versions: dict[str, int] = {}
    relation_evidence = new_relation_evidence()

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
        language = _TSX_LANGUAGE if record.relative_path.endswith(".tsx") else _TS_LANGUAGE
        tree = _parse_typescript(content, language)
        if tree.root_node.has_error:
            raise CodeIndexError(
                f"TypeScript source contains syntax errors: {record.relative_path}"
            )
        source = _TypeScriptSource(
            record=record,
            text=text,
            content=content,
            module_scope=_typescript_module_scope(record.relative_path),
        )
        module_symbol = _module_symbol(
            repository_id,
            repository.last_synced_commit,
            source,
        )
        parsed_sources.append((source, tree, module_symbol))
        definitions.extend(
            _collect_definitions(
                repository_id,
                repository.last_synced_commit,
                source,
                tree.root_node,
                module_symbol,
            )
        )
        source_versions[record.source_id] = record.source_version

    _add_contains_relations(definitions, relation_evidence)
    import_bindings = _add_import_relations(parsed_sources, definitions, relation_evidence)
    _add_calls(parsed_sources, definitions, import_bindings, relation_evidence)

    symbols = [
        *(module for _source, _tree, module in parsed_sources),
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
        languages=(CodeLanguage.TYPESCRIPT,),
        source_versions=source_versions,
        symbols=tuple(symbols),
        relations=build_relations(
            repository_id,
            repository.last_synced_commit,
            relation_evidence,
        ),
    )


def _parse_typescript(content: bytes, language: Language) -> Tree:
    parser = Parser(language)
    tree = parser.parse(content)
    if not tree.root_node.has_error:
        return tree
    # tree-sitter-typescript 0.23.2 lacks TypeScript 5 variance annotations.
    compatible = _TYPE_PARAMETER_VARIANCE.sub(
        lambda match: b" " * len(match.group()),
        content,
    )
    compatible = _LINE_TYPE_PARAMETER_VARIANCE.sub(
        lambda match: b" " * len(match.group()),
        compatible,
    )
    return parser.parse(compatible) if compatible != content else tree


def _collect_definitions(
    repository_id: str,
    commit_sha: str,
    source: _TypeScriptSource,
    root: Node,
    module_symbol: CodeSymbol,
) -> list[_TypeScriptDefinition]:
    definitions: list[_TypeScriptDefinition] = []
    class_nodes: dict[tuple[str, int, int], CodeSymbol] = {}

    for node in _walk(root):
        kind = _TYPE_NODES.get(node.type)
        if kind is None or not _is_top_level(node):
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = node_text(source, name_node)
        scope = _typescript_definition_scope(source, node)
        symbol = _typescript_symbol(
            repository_id,
            commit_sha,
            source,
            node,
            kind,
            f"{scope}.{name}",
            name,
        )
        definitions.append(
            _TypeScriptDefinition(
                node=node,
                symbol=symbol,
                module_symbol_id=module_symbol.symbol_id,
            )
        )
        if kind is CodeSymbolKind.CLASS:
            class_nodes[node_key(node)] = symbol

    for node in _walk(root):
        if node.type in {"function_declaration", "generator_function_declaration"}:
            if not _is_top_level(node):
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = node_text(source, name_node)
            scope = _typescript_definition_scope(source, node)
            definitions.append(
                _TypeScriptDefinition(
                    node=node,
                    symbol=_typescript_symbol(
                        repository_id,
                        commit_sha,
                        source,
                        node,
                        CodeSymbolKind.FUNCTION,
                        f"{scope}.{name}",
                        name,
                    ),
                    module_symbol_id=module_symbol.symbol_id,
                )
            )
        elif node.type == "variable_declarator" and _is_top_level(node):
            name_node = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name_node is None or name_node.type != "identifier":
                continue
            name = node_text(source, name_node)
            is_function = value is not None and value.type in {
                "arrow_function",
                "function_expression",
            }
            if not is_function and not _is_const_declarator(source, node):
                continue
            kind = CodeSymbolKind.FUNCTION if is_function else CodeSymbolKind.CONSTANT
            scope = _typescript_definition_scope(source, node)
            definitions.append(
                _TypeScriptDefinition(
                    node=node,
                    symbol=_typescript_symbol(
                        repository_id,
                        commit_sha,
                        source,
                        node,
                        kind,
                        f"{scope}.{name}",
                        name,
                    ),
                    module_symbol_id=module_symbol.symbol_id,
                )
            )
        elif node.type == "method_definition":
            class_node = _enclosing_class(node)
            if class_node is None:
                continue
            class_symbol = class_nodes.get(node_key(class_node))
            name_node = node.child_by_field_name("name")
            if class_symbol is None or name_node is None:
                continue
            name = node_text(source, name_node)
            accessor = next(
                (child.type for child in node.children if child.type in {"get", "set"}),
                None,
            )
            qualified_name = f"{class_symbol.qualified_name}.{name}"
            if accessor is not None:
                qualified_name += f"@{accessor}"
            definitions.append(
                _TypeScriptDefinition(
                    node=node,
                    symbol=_typescript_symbol(
                        repository_id,
                        commit_sha,
                        source,
                        node,
                        CodeSymbolKind.METHOD,
                        qualified_name,
                        name,
                    ),
                    module_symbol_id=module_symbol.symbol_id,
                    class_symbol_id=class_symbol.symbol_id,
                )
            )
    return definitions


def _typescript_definition_scope(source: _TypeScriptSource, node: Node) -> str:
    namespaces: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type == "internal_module":
            name = parent.child_by_field_name("name")
            if name is not None:
                namespaces.append(node_text(source, name))
        parent = parent.parent
    return ".".join((source.module_scope, *reversed(namespaces)))


def _add_contains_relations(
    definitions: list[_TypeScriptDefinition],
    relation_evidence: RelationEvidence,
) -> None:
    for definition in definitions:
        append_relation_evidence(
            relation_evidence,
            CodeRelationType.CONTAINS,
            definition.class_symbol_id or definition.module_symbol_id,
            definition.symbol.symbol_id,
            definition.symbol.location,
        )


def _add_import_relations(
    parsed_sources: list[tuple[_TypeScriptSource, Tree, CodeSymbol]],
    definitions: list[_TypeScriptDefinition],
    relation_evidence: RelationEvidence,
) -> dict[str, dict[str, CodeSymbol]]:
    modules_by_key: dict[str, tuple[_TypeScriptSource, CodeSymbol]] = {}
    for source, _tree, module in parsed_sources:
        key = _module_key(source.record.relative_path)
        modules_by_key[key] = (source, module)
        if key.endswith("/index"):
            modules_by_key[key.removesuffix("/index")] = (source, module)

    definitions_by_scope = {
        (
            definition.symbol.qualified_name.rsplit(".", 1)[0],
            definition.symbol.display_name,
        ): definition.symbol
        for definition in definitions
        if definition.symbol.kind is not CodeSymbolKind.METHOD
    }
    bindings: dict[str, dict[str, CodeSymbol]] = {}
    for source, tree, source_module in parsed_sources:
        source_bindings: dict[str, CodeSymbol] = {}
        for node in tree.root_node.children:
            if node.type != "import_statement":
                continue
            specifier = _import_specifier(source, node)
            target = _resolve_import_module(source, specifier, modules_by_key)
            if target is None:
                continue
            target_source, target_module = target
            if target_module.symbol_id != source_module.symbol_id:
                append_relation_evidence(
                    relation_evidence,
                    CodeRelationType.IMPORTS,
                    source_module.symbol_id,
                    target_module.symbol_id,
                    node_location(source, node),
                )
            for imported_name, local_name in _named_imports(source, node):
                target_symbol = definitions_by_scope.get(
                    (target_source.module_scope, imported_name)
                )
                if target_symbol is not None:
                    source_bindings[local_name] = target_symbol
        bindings[source.record.source_id] = source_bindings
    return bindings


def _add_calls(
    parsed_sources: list[tuple[_TypeScriptSource, Tree, CodeSymbol]],
    definitions: list[_TypeScriptDefinition],
    import_bindings: dict[str, dict[str, CodeSymbol]],
    relation_evidence: RelationEvidence,
) -> None:
    definitions_by_node = {
        (definition.symbol.location.source_id, node_key(definition.node)): definition
        for definition in definitions
        if definition.symbol.kind in {CodeSymbolKind.FUNCTION, CodeSymbolKind.METHOD}
    }
    top_level = {
        (
            definition.symbol.qualified_name.rsplit(".", 1)[0],
            definition.symbol.display_name,
        ): definition.symbol
        for definition in definitions
        if definition.symbol.kind is CodeSymbolKind.FUNCTION
    }
    classes = {
        (
            definition.symbol.qualified_name.rsplit(".", 1)[0],
            definition.symbol.display_name,
        ): definition.symbol
        for definition in definitions
        if definition.symbol.kind is CodeSymbolKind.CLASS
    }
    methods = {
        (
            definition.symbol.qualified_name.rsplit(".", 2)[0],
            definition.symbol.qualified_name.rsplit(".", 2)[1],
            definition.symbol.display_name,
        ): definition.symbol
        for definition in definitions
        if definition.symbol.kind is CodeSymbolKind.METHOD
    }

    for source, tree, _module in parsed_sources:
        for node in _walk(tree.root_node):
            if node.type not in {"call_expression", "new_expression"}:
                continue
            caller = _enclosing_definition(node, source, definitions_by_node)
            if caller is None:
                continue
            target = _resolve_call(
                node,
                source,
                caller,
                top_level=top_level,
                classes=classes,
                methods=methods,
                imported=import_bindings.get(source.record.source_id, {}),
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


def _resolve_call(
    call: Node,
    source: _TypeScriptSource,
    caller: _TypeScriptDefinition,
    *,
    top_level: dict[tuple[str, str], CodeSymbol],
    classes: dict[tuple[str, str], CodeSymbol],
    methods: dict[tuple[str, str, str], CodeSymbol],
    imported: dict[str, CodeSymbol],
) -> CodeSymbol | None:
    if call.type == "new_expression":
        constructor = call.child_by_field_name("constructor")
        if constructor is None:
            return None
        name = node_text(source, constructor)
        return classes.get((source.module_scope, name)) or imported.get(name)

    function = call.child_by_field_name("function")
    if function is None:
        return None
    if function.type == "identifier":
        name = node_text(source, function)
        return top_level.get((source.module_scope, name)) or imported.get(name)
    if function.type != "member_expression":
        return None
    owner = function.child_by_field_name("object")
    property_node = function.child_by_field_name("property")
    if owner is None or property_node is None:
        return None
    method_name = node_text(source, property_node)
    if owner.type == "this" and caller.class_symbol_id is not None:
        class_name = caller.symbol.qualified_name.rsplit(".", 2)[1]
        return methods.get((source.module_scope, class_name, method_name))
    if owner.type == "identifier":
        owner_name = node_text(source, owner)
        owner_type = _local_variable_type(call, caller, source, owner_name) or owner_name
        return methods.get((source.module_scope, owner_type, method_name))
    return None


def _local_variable_type(
    call: Node,
    caller: _TypeScriptDefinition,
    source: _TypeScriptSource,
    variable_name: str,
) -> str | None:
    for node in _walk(caller.node):
        if node.type != "variable_declarator" or node.start_byte >= call.start_byte:
            continue
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if (
            name is None
            or node_text(source, name) != variable_name
            or value is None
            or value.type != "new_expression"
        ):
            continue
        constructor = value.child_by_field_name("constructor")
        if constructor is not None and constructor.type in {"identifier", "type_identifier"}:
            return node_text(source, constructor)
    return None


def _enclosing_definition(
    node: Node,
    source: _TypeScriptSource,
    definitions_by_node: dict[
        tuple[str, tuple[str, int, int]],
        _TypeScriptDefinition,
    ],
) -> _TypeScriptDefinition | None:
    parent = node.parent
    while parent is not None:
        if parent.type in _FUNCTION_NODES | {"variable_declarator"}:
            found = definitions_by_node.get((source.record.source_id, node_key(parent)))
            if found is not None:
                return found
        parent = parent.parent
    return None


def _module_symbol(
    repository_id: str,
    commit_sha: str,
    source: _TypeScriptSource,
) -> CodeSymbol:
    signature = f"module {source.module_scope}"
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            repository_id,
            source.record.relative_path,
            CodeLanguage.TYPESCRIPT,
            CodeSymbolKind.MODULE,
            source.module_scope,
        ),
        repository_id=repository_id,
        commit_sha=commit_sha,
        language=CodeLanguage.TYPESCRIPT,
        kind=CodeSymbolKind.MODULE,
        qualified_name=source.module_scope,
        display_name=PurePosixPath(source.record.relative_path).stem,
        visibility=CodeVisibility.PUBLIC,
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(source.content).hexdigest(),
        location=source_location(source),
    )


def _typescript_symbol(
    repository_id: str,
    commit_sha: str,
    source: _TypeScriptSource,
    node: Node,
    kind: CodeSymbolKind,
    qualified_name: str,
    display_name: str,
) -> CodeSymbol:
    signature = _typescript_signature(source, node)
    extent = _typescript_extent(node)
    body = source.content[extent.start_byte : extent.end_byte]
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            repository_id,
            source.record.relative_path,
            CodeLanguage.TYPESCRIPT,
            kind,
            qualified_name,
        ),
        repository_id=repository_id,
        commit_sha=commit_sha,
        language=CodeLanguage.TYPESCRIPT,
        kind=kind,
        qualified_name=qualified_name,
        display_name=display_name,
        visibility=_typescript_visibility(source, node, display_name),
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(body).hexdigest(),
        location=node_location(source, extent),
    )


def _typescript_signature(source: _TypeScriptSource, node: Node) -> str:
    extent = _typescript_extent(node)
    body = node.child_by_field_name("body")
    if node.type == "variable_declarator":
        value = node.child_by_field_name("value")
        if value is not None and value.type in {"arrow_function", "function_expression"}:
            function_body = value.child_by_field_name("body")
            if function_body is not None:
                body = function_body
    end_byte = body.start_byte if body is not None else node.end_byte
    signature = source.content[extent.start_byte : end_byte].decode("utf-8").strip()
    if not signature:
        raise CodeIndexError(
            f"TypeScript definition has no signature: {source.record.relative_path}"
        )
    return signature


def _typescript_extent(node: Node) -> Node:
    extent = node
    if node.type == "variable_declarator":
        parent = node.parent
        if parent is not None and parent.type in {"lexical_declaration", "variable_declaration"}:
            extent = parent
    parent = extent.parent
    if parent is not None and parent.type == "export_statement":
        return parent
    return extent


def _typescript_visibility(
    source: _TypeScriptSource,
    node: Node,
    display_name: str,
) -> CodeVisibility:
    header = _typescript_signature(source, node)
    if header.startswith("private ") or display_name.startswith("#"):
        return CodeVisibility.PRIVATE
    if header.startswith("protected "):
        return CodeVisibility.PROTECTED
    if _has_ancestor(node, "export_statement"):
        return CodeVisibility.PUBLIC
    if _enclosing_class(node) is not None:
        return CodeVisibility.PUBLIC
    return CodeVisibility.INTERNAL


def _named_imports(source: _TypeScriptSource, statement: Node) -> tuple[tuple[str, str], ...]:
    imports: list[tuple[str, str]] = []
    for node in _walk(statement):
        if node.type != "import_specifier":
            continue
        name = node.child_by_field_name("name")
        alias = node.child_by_field_name("alias")
        if name is None:
            continue
        imported_name = node_text(source, name)
        imports.append(
            (imported_name, node_text(source, alias) if alias is not None else imported_name)
        )
    return tuple(imports)


def _import_specifier(source: _TypeScriptSource, statement: Node) -> str:
    source_node = statement.child_by_field_name("source")
    if source_node is None:
        return ""
    raw = node_text(source, source_node)
    return raw[1:-1] if len(raw) >= 2 and raw[0] in {'"', "'"} else raw


def _resolve_import_module(
    source: _TypeScriptSource,
    specifier: str,
    modules_by_key: dict[str, tuple[_TypeScriptSource, CodeSymbol]],
) -> tuple[_TypeScriptSource, CodeSymbol] | None:
    if not specifier.startswith("."):
        return None
    parent = PurePosixPath(source.record.relative_path).parent.as_posix()
    target = posixpath.normpath(posixpath.join(parent, specifier))
    if target == ".." or target.startswith("../"):
        return None
    candidates = [target]
    suffix = PurePosixPath(target).suffix
    if suffix in {".js", ".jsx", ".ts", ".tsx"}:
        candidates.append(target[: -len(suffix)])
    for candidate in candidates:
        resolved = modules_by_key.get(candidate) or modules_by_key.get(f"{candidate}/index")
        if resolved is not None:
            return resolved
    return None


def _module_key(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    return path.with_suffix("").as_posix()


def _typescript_module_scope(relative_path: str) -> str:
    return ".".join(PurePosixPath(relative_path).with_suffix("").parts)


def _is_const_declarator(source: _TypeScriptSource, node: Node) -> bool:
    parent = node.parent
    if parent is None or parent.type != "lexical_declaration":
        return False
    return node_text(source, parent).lstrip().startswith("const ")


def _is_top_level(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {
            "function_declaration",
            "generator_function_declaration",
            "arrow_function",
            "function_expression",
            "method_definition",
            "class_declaration",
            "abstract_class_declaration",
        }:
            return False
        if parent.type == "program":
            return True
        parent = parent.parent
    return False


def _enclosing_class(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None:
        if parent.type in {"class_declaration", "abstract_class_declaration"}:
            return parent
        if parent.type in {
            "function_declaration",
            "generator_function_declaration",
            "arrow_function",
            "function_expression",
        }:
            return None
        parent = parent.parent
    return None


def _has_ancestor(node: Node, node_type: str) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type == node_type:
            return True
        if parent.type == "program":
            return False
        parent = parent.parent
    return False


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)
