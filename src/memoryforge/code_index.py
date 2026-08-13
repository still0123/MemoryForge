"""Tree-sitter adapters that build deterministic code index snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

import tree_sitter_python
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

_PYTHON_LANGUAGE = Language(tree_sitter_python.language())


@dataclass(frozen=True)
class _ParsedDefinition:
    node: Node
    symbol: CodeSymbol
    parent_symbol_id: str
    class_symbol_id: str | None


@dataclass(frozen=True)
class _PythonSource(ParsedCodeSource):
    module_name: str


@dataclass(frozen=True)
class _PythonAnalysis:
    source: _PythonSource
    tree: Tree
    module_symbol: CodeSymbol
    definitions: tuple[_ParsedDefinition, ...]


def build_code_index(
    workspace: Workspace | Path | str,
    repository_id: str,
) -> CodeIndexSnapshot:
    """Build the canonical index from every implemented language adapter."""

    from memoryforge.go_index import _build_go_code_index
    from memoryforge.typescript_index import _build_typescript_code_index

    snapshots = (
        _build_python_code_index(workspace, repository_id),
        _build_go_code_index(workspace, repository_id),
        _build_typescript_code_index(workspace, repository_id),
    )
    repository_ids = {snapshot.repository_id for snapshot in snapshots}
    commit_shas = {snapshot.commit_sha for snapshot in snapshots}
    index_ids = {snapshot.index_id for snapshot in snapshots}
    if len(repository_ids) != 1 or len(commit_shas) != 1 or len(index_ids) != 1:
        raise CodeIndexError("language adapters did not index the same repository revision")

    source_versions: dict[str, int] = {}
    for snapshot in snapshots:
        for source_id, source_version in snapshot.source_versions.items():
            existing = source_versions.get(source_id)
            if existing is not None and existing != source_version:
                raise CodeIndexError("language adapters disagree on a SourceVersion")
            source_versions[source_id] = source_version
    symbols = tuple(
        sorted(
            (symbol for snapshot in snapshots for symbol in snapshot.symbols),
            key=lambda symbol: (
                symbol.location.relative_path,
                symbol.location.start_line,
                symbol.qualified_name,
            ),
        )
    )
    relations = tuple(
        sorted(
            (relation for snapshot in snapshots for relation in snapshot.relations),
            key=lambda relation: (
                relation.type.value,
                relation.source_symbol_id,
                relation.target_symbol_id,
            ),
        )
    )
    return CodeIndexSnapshot(
        index_id=snapshots[0].index_id,
        repository_id=snapshots[0].repository_id,
        commit_sha=snapshots[0].commit_sha,
        languages=(CodeLanguage.PYTHON, CodeLanguage.GO, CodeLanguage.TYPESCRIPT),
        source_versions=source_versions,
        symbols=symbols,
        relations=relations,
    )


def _build_python_code_index(
    workspace: Workspace | Path | str,
    repository_id: str,
) -> CodeIndexSnapshot:
    """Index current immutable Python sources from one completed Git sync."""

    root = workspace.root if isinstance(workspace, Workspace) else Path(workspace)
    opened = Workspace.open_readonly(root)
    repository = get_git_checkout_readonly(opened.root, repository_id)
    if repository.last_synced_commit is None:
        raise CodeIndexError("Git repository must be synced before code indexing")

    records = [
        source
        for source in list_current_git_source_versions(opened.root, repository_id)
        if source.relative_path.endswith(".py") and "code" in source.tags
    ]
    if any(source.commit_sha != repository.last_synced_commit for source in records):
        raise CodeIndexError("current Python sources do not belong to the last synced commit")

    symbols: list[CodeSymbol] = []
    analyses: list[_PythonAnalysis] = []
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
        module_name = _python_module_name(record.relative_path)
        if any(
            part != part.strip() or "\n" in part or "\r" in part
            for part in module_name.split(".")
        ):
            continue
        source = _PythonSource(
            record=record,
            text=text,
            content=text.encode(),
            module_name=module_name,
        )
        parsed = _parse_python_source(
            repository_id,
            repository.last_synced_commit,
            source,
        )
        if parsed is None:
            continue
        analysis, parsed_relations = parsed
        analyses.append(analysis)
        canonical_symbols = {
            definition.symbol.symbol_id: definition.symbol for definition in analysis.definitions
        }
        symbols.extend(
            [
                analysis.module_symbol,
                *canonical_symbols.values(),
            ]
        )
        for key, evidence in parsed_relations.items():
            relation_evidence[key].extend(evidence)
        source_versions[record.source_id] = record.source_version

    _add_python_imports_and_calls(analyses, relation_evidence)
    symbols.sort(
        key=lambda symbol: (
            symbol.location.relative_path,
            symbol.location.start_line,
            symbol.qualified_name,
        )
    )
    relations = build_relations(
        repository_id,
        repository.last_synced_commit,
        relation_evidence,
    )
    return CodeIndexSnapshot(
        index_id=make_code_index_id(repository_id, repository.last_synced_commit),
        repository_id=repository_id,
        commit_sha=repository.last_synced_commit,
        languages=(CodeLanguage.PYTHON,),
        source_versions=source_versions,
        symbols=tuple(symbols),
        relations=relations,
    )


def _parse_python_source(
    repository_id: str,
    commit_sha: str,
    source: _PythonSource,
) -> tuple[
    _PythonAnalysis,
    RelationEvidence,
] | None:
    parser = Parser(_PYTHON_LANGUAGE)
    tree = parser.parse(source.content)
    if tree.root_node.has_error:
        return None

    module_symbol = _module_symbol(repository_id, commit_sha, source)
    definitions: list[_ParsedDefinition] = []
    relation_evidence = new_relation_evidence()

    _collect_definitions(
        tree.root_node,
        repository_id=repository_id,
        commit_sha=commit_sha,
        source=source,
        module_symbol=module_symbol,
        parent_symbol=module_symbol,
        class_qualified_name=None,
        class_symbol_id=None,
        inside_function=False,
        definitions=definitions,
        relation_evidence=relation_evidence,
    )
    definitions = _canonicalize_python_definitions(definitions)
    return (
        _PythonAnalysis(
            source=source,
            tree=tree,
            module_symbol=module_symbol,
            definitions=tuple(definitions),
        ),
        relation_evidence,
    )


def _canonicalize_python_definitions(
    definitions: list[_ParsedDefinition],
) -> list[_ParsedDefinition]:
    """Represent overloads and conditional definitions as one logical symbol."""

    selected: dict[str, CodeSymbol] = {}
    for definition in definitions:
        current = selected.get(definition.symbol.symbol_id)
        if current is None or (
            _is_overload_signature(current.signature)
            and not _is_overload_signature(definition.symbol.signature)
        ):
            selected[definition.symbol.symbol_id] = definition.symbol
    return [
        replace(definition, symbol=selected[definition.symbol.symbol_id])
        for definition in definitions
    ]


def _is_overload_signature(signature: str) -> bool:
    return any(
        line.lstrip().startswith("@")
        and line.strip().removeprefix("@").split("(", 1)[0].endswith("overload")
        for line in signature.splitlines()
    )


def _collect_definitions(
    node: Node,
    *,
    repository_id: str,
    commit_sha: str,
    source: _PythonSource,
    module_symbol: CodeSymbol,
    parent_symbol: CodeSymbol,
    class_qualified_name: str | None,
    class_symbol_id: str | None,
    inside_function: bool,
    definitions: list[_ParsedDefinition],
    relation_evidence: RelationEvidence,
) -> None:
    if node.type == "class_definition":
        if inside_function:
            for child in node.children:
                _collect_definitions(
                    child,
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    source=source,
                    module_symbol=module_symbol,
                    parent_symbol=parent_symbol,
                    class_qualified_name=class_qualified_name,
                    class_symbol_id=class_symbol_id,
                    inside_function=True,
                    definitions=definitions,
                    relation_evidence=relation_evidence,
                )
            return
        name = _definition_name(node, source)
        local_name = f"{class_qualified_name}.{name}" if class_qualified_name else name
        symbol = _definition_symbol(
            repository_id,
            commit_sha,
            source,
            node,
            CodeSymbolKind.CLASS,
            f"{source.module_name}.{local_name}",
            name,
        )
        definitions.append(
            _ParsedDefinition(
                node=node,
                symbol=symbol,
                parent_symbol_id=parent_symbol.symbol_id,
                class_symbol_id=symbol.symbol_id,
            )
        )
        append_relation_evidence(
            relation_evidence,
            CodeRelationType.CONTAINS,
            parent_symbol.symbol_id,
            symbol.symbol_id,
            symbol.location,
        )
        for child in node.children:
            _collect_definitions(
                child,
                repository_id=repository_id,
                commit_sha=commit_sha,
                source=source,
                module_symbol=module_symbol,
                parent_symbol=symbol,
                class_qualified_name=local_name,
                class_symbol_id=symbol.symbol_id,
                inside_function=False,
                definitions=definitions,
                relation_evidence=relation_evidence,
            )
        return

    if node.type == "function_definition":
        if not inside_function:
            name = _definition_name(node, source)
            local_name = f"{class_qualified_name}.{name}" if class_qualified_name else name
            kind = CodeSymbolKind.METHOD if class_symbol_id else CodeSymbolKind.FUNCTION
            symbol = _definition_symbol(
                repository_id,
                commit_sha,
                source,
                node,
                kind,
                f"{source.module_name}.{local_name}",
                name,
            )
            definitions.append(
                _ParsedDefinition(
                    node=node,
                    symbol=symbol,
                    parent_symbol_id=parent_symbol.symbol_id,
                    class_symbol_id=class_symbol_id,
                )
            )
            append_relation_evidence(
                relation_evidence,
                CodeRelationType.CONTAINS,
                parent_symbol.symbol_id,
                symbol.symbol_id,
                symbol.location,
            )
        for child in node.children:
            _collect_definitions(
                child,
                repository_id=repository_id,
                commit_sha=commit_sha,
                source=source,
                module_symbol=module_symbol,
                parent_symbol=parent_symbol,
                class_qualified_name=class_qualified_name,
                class_symbol_id=class_symbol_id,
                inside_function=True,
                definitions=definitions,
                relation_evidence=relation_evidence,
            )
        return

    for child in node.children:
        _collect_definitions(
            child,
            repository_id=repository_id,
            commit_sha=commit_sha,
            source=source,
            module_symbol=module_symbol,
            parent_symbol=parent_symbol,
            class_qualified_name=class_qualified_name,
            class_symbol_id=class_symbol_id,
            inside_function=inside_function,
            definitions=definitions,
            relation_evidence=relation_evidence,
        )


def _add_python_imports_and_calls(
    analyses: list[_PythonAnalysis],
    relation_evidence: RelationEvidence,
) -> None:
    modules = {analysis.source.module_name: analysis.module_symbol for analysis in analyses}
    definitions = {
        (
            definition.symbol.qualified_name.rsplit(".", 1)[0],
            definition.symbol.display_name,
        ): definition.symbol
        for analysis in analyses
        for definition in analysis.definitions
        if definition.symbol.kind is not CodeSymbolKind.METHOD
    }
    for analysis in analyses:
        imported: dict[str, CodeSymbol] = {}
        imported_modules: dict[str, str] = {}
        for statement in analysis.tree.root_node.children:
            if statement.type == "import_from_statement":
                module_node = statement.child_by_field_name("module_name")
                if module_node is None:
                    continue
                module_name = node_text(analysis.source, module_node)
                target_module = modules.get(module_name)
                if target_module is None:
                    continue
                append_relation_evidence(
                    relation_evidence,
                    CodeRelationType.IMPORTS,
                    analysis.module_symbol.symbol_id,
                    target_module.symbol_id,
                    node_location(analysis.source, statement),
                )
                for imported_name, local_name in _python_imported_names(
                    analysis.source,
                    statement,
                    excluded=module_node,
                ):
                    target = definitions.get((module_name, imported_name))
                    if target is not None:
                        imported[local_name] = target
            elif statement.type == "import_statement":
                for module_name, local_name in _python_imported_names(
                    analysis.source,
                    statement,
                ):
                    target_module = modules.get(module_name)
                    if target_module is None:
                        continue
                    append_relation_evidence(
                        relation_evidence,
                        CodeRelationType.IMPORTS,
                        analysis.module_symbol.symbol_id,
                        target_module.symbol_id,
                        node_location(analysis.source, statement),
                    )
                    imported_modules[local_name] = module_name
        _collect_python_calls(
            analysis.tree.root_node,
            source=analysis.source,
            definitions=list(analysis.definitions),
            imported=imported,
            imported_modules=imported_modules,
            definitions_by_scope=definitions,
            relation_evidence=relation_evidence,
        )


def _python_imported_names(
    source: _PythonSource,
    statement: Node,
    *,
    excluded: Node | None = None,
) -> tuple[tuple[str, str], ...]:
    items: list[tuple[str, str]] = []
    excluded_key = node_key(excluded) if excluded is not None else None
    for child in statement.children:
        if excluded_key is not None and node_key(child) == excluded_key:
            continue
        if child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if name_node is not None and alias is not None:
                items.append((node_text(source, name_node), node_text(source, alias)))
        elif child.type in {"dotted_name", "identifier"}:
            imported_name = node_text(source, child)
            items.append((imported_name, imported_name.rsplit(".", 1)[-1]))
    return tuple(items)


def _collect_python_calls(
    root: Node,
    *,
    source: _PythonSource,
    definitions: list[_ParsedDefinition],
    imported: dict[str, CodeSymbol],
    imported_modules: dict[str, str],
    definitions_by_scope: dict[tuple[str, str], CodeSymbol],
    relation_evidence: RelationEvidence,
) -> None:
    by_node = {
        node_key(definition.node): definition
        for definition in definitions
        if definition.symbol.kind in {CodeSymbolKind.FUNCTION, CodeSymbolKind.METHOD}
    }
    top_level = {
        definition.symbol.display_name: definition.symbol
        for definition in definitions
        if definition.parent_symbol_id
        == make_code_symbol_id(
            definition.symbol.repository_id,
            source.record.relative_path,
            CodeLanguage.PYTHON,
            CodeSymbolKind.MODULE,
            source.module_name,
        )
    }
    classes_by_name = {
        definition.symbol.display_name: definition
        for definition in definitions
        if definition.symbol.kind is CodeSymbolKind.CLASS
    }
    methods_by_class: dict[str, dict[str, CodeSymbol]] = {}
    for definition in definitions:
        if (
            definition.symbol.kind is CodeSymbolKind.METHOD
            and definition.class_symbol_id is not None
        ):
            methods_by_class.setdefault(definition.class_symbol_id, {})[
                definition.symbol.display_name
            ] = definition.symbol

    def visit(
        node: Node,
        current_caller: CodeSymbol | None,
        current_class_id: str | None,
    ) -> None:
        definition = by_node.get(node_key(node))
        if definition is not None:
            current_caller = definition.symbol
            current_class_id = definition.class_symbol_id
        if node.type == "call" and current_caller is not None:
            target = _resolve_local_call(
                node,
                source,
                current_class_id=current_class_id,
                top_level=top_level,
                classes_by_name=classes_by_name,
                methods_by_class=methods_by_class,
                imported=imported,
                imported_modules=imported_modules,
                definitions_by_scope=definitions_by_scope,
            )
            if target is not None:
                append_relation_evidence(
                    relation_evidence,
                    CodeRelationType.CALLS,
                    current_caller.symbol_id,
                    target.symbol_id,
                    node_location(source, node),
                )
        for child in node.children:
            visit(child, current_caller, current_class_id)

    visit(root, None, None)


def _resolve_local_call(
    call: Node,
    source: _PythonSource,
    *,
    current_class_id: str | None,
    top_level: dict[str, CodeSymbol],
    classes_by_name: dict[str, _ParsedDefinition],
    methods_by_class: dict[str, dict[str, CodeSymbol]],
    imported: dict[str, CodeSymbol],
    imported_modules: dict[str, str],
    definitions_by_scope: dict[tuple[str, str], CodeSymbol],
) -> CodeSymbol | None:
    function = call.child_by_field_name("function")
    if function is None:
        return None
    if function.type == "identifier":
        name = node_text(source, function)
        return top_level.get(name) or imported.get(name)
    if function.type != "attribute":
        return None
    owner = function.child_by_field_name("object")
    attribute = function.child_by_field_name("attribute")
    if owner is None or attribute is None:
        return None
    owner_name = node_text(source, owner)
    method_name = node_text(source, attribute)
    imported_module = imported_modules.get(owner_name)
    if imported_module is not None:
        return definitions_by_scope.get((imported_module, method_name))
    if owner_name in {"self", "cls"} and current_class_id is not None:
        return methods_by_class.get(current_class_id, {}).get(method_name)
    class_definition = classes_by_name.get(owner_name)
    if class_definition is None:
        return None
    return methods_by_class.get(class_definition.symbol.symbol_id, {}).get(method_name)


def _module_symbol(
    repository_id: str,
    commit_sha: str,
    source: _PythonSource,
) -> CodeSymbol:
    signature = f"module {source.module_name}"
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            repository_id,
            source.record.relative_path,
            CodeLanguage.PYTHON,
            CodeSymbolKind.MODULE,
            source.module_name,
        ),
        repository_id=repository_id,
        commit_sha=commit_sha,
        language=CodeLanguage.PYTHON,
        kind=CodeSymbolKind.MODULE,
        qualified_name=source.module_name,
        display_name=source.module_name.rsplit(".", 1)[-1],
        visibility=CodeVisibility.PUBLIC,
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(source.content).hexdigest(),
        location=source_location(source),
    )


def _definition_symbol(
    repository_id: str,
    commit_sha: str,
    source: _PythonSource,
    node: Node,
    kind: CodeSymbolKind,
    qualified_name: str,
    display_name: str,
) -> CodeSymbol:
    signature = _definition_signature(source, node)
    extent = _definition_extent(node)
    body = source.content[extent.start_byte : extent.end_byte]
    return CodeSymbol(
        symbol_id=make_code_symbol_id(
            repository_id,
            source.record.relative_path,
            CodeLanguage.PYTHON,
            kind,
            qualified_name,
        ),
        repository_id=repository_id,
        commit_sha=commit_sha,
        language=CodeLanguage.PYTHON,
        kind=kind,
        qualified_name=qualified_name,
        display_name=display_name,
        visibility=(
            CodeVisibility.PRIVATE if display_name.startswith("_") else CodeVisibility.PUBLIC
        ),
        signature=signature,
        signature_sha256=hashlib.sha256(signature.encode()).hexdigest(),
        body_sha256=hashlib.sha256(body).hexdigest(),
        location=node_location(source, extent),
    )


def _definition_signature(source: _PythonSource, node: Node) -> str:
    extent = _definition_extent(node)
    body = node.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else node.end_byte
    signature_bytes = source.content[extent.start_byte : end_byte]
    parameters = node.child_by_field_name("parameters")
    if parameters is not None:
        replacements: list[tuple[int, int]] = []
        for parameter in parameters.named_children:
            name = parameter.child_by_field_name("name")
            value = parameter.child_by_field_name("value")
            if (
                name is not None
                and value is not None
                and value.type == "string"
                and node_text(source, value) not in {'""', "''"}
                and _is_sensitive_parameter_name(node_text(source, name))
            ):
                replacements.append(
                    (
                        value.start_byte - extent.start_byte,
                        value.end_byte - extent.start_byte,
                    )
                )
        for start, end in reversed(replacements):
            signature_bytes = signature_bytes[:start] + b'"<redacted>"' + signature_bytes[end:]
    signature = signature_bytes.decode("utf-8").strip()
    if not signature:
        raise CodeIndexError(f"Python definition has no signature: {source.record.relative_path}")
    return signature


def _is_sensitive_parameter_name(name: str) -> bool:
    compact = "".join(character for character in name.lower() if character.isalnum())
    parts = name.lower().split("_")
    return (
        any(
            marker in compact
            for marker in (
                "password",
                "passwd",
                "token",
                "secret",
                "accesskey",
                "credential",
                "authorization",
            )
        )
        or "ak" in parts
        or "sk" in parts
    )


def _definition_extent(node: Node) -> Node:
    parent = node.parent
    if parent is not None and parent.type == "decorated_definition":
        return parent
    return node


def _definition_name(node: Node, source: _PythonSource) -> str:
    name = node.child_by_field_name("name")
    if name is None:
        raise CodeIndexError(f"Python definition has no name: {source.record.relative_path}")
    return node_text(source, name)


def _python_module_name(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    without_suffix = path.with_suffix("")
    parts = list(without_suffix.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else "__root__"
