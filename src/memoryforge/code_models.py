"""Deterministic contracts for repository code analysis and Wiki planning."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from memoryforge.models import SourceId

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
CommitId = Annotated[str, Field(pattern=r"^[a-f0-9]{40,64}$")]
SourceVersionId = Annotated[int, Field(ge=1)]

_CHAR_LOCATOR = re.compile(r"^chars:(?P<start>\d+)-(?P<end>\d+)$")
_MODULE_PATH = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$")


class CodeLanguage(StrEnum):
    """Languages supported by the first CodeWiki integration milestone."""

    PYTHON = "python"
    GO = "go"
    TYPESCRIPT = "typescript"


class CodeSymbolKind(StrEnum):
    MODULE = "module"
    PACKAGE = "package"
    CLASS = "class"
    INTERFACE = "interface"
    STRUCT = "struct"
    FUNCTION = "function"
    METHOD = "method"
    TYPE_ALIAS = "type_alias"
    CONSTANT = "constant"


class CodeVisibility(StrEnum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class CodeRelationType(StrEnum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    TESTS = "tests"


def make_code_symbol_id(
    repository_id: str,
    relative_path: str,
    language: CodeLanguage | str,
    kind: CodeSymbolKind | str,
    qualified_name: str,
) -> str:
    """Build a commit-independent identity for one repository symbol."""

    return _digest(
        "code-symbol",
        repository_id,
        relative_path,
        str(language),
        str(kind),
        qualified_name,
    )


def make_code_relation_id(
    repository_id: str,
    relation_type: CodeRelationType | str,
    source_symbol_id: str,
    target_symbol_id: str,
) -> str:
    """Build a stable identity for one directed symbol relationship."""

    return _digest(
        "code-relation",
        repository_id,
        str(relation_type),
        source_symbol_id,
        target_symbol_id,
    )


def make_code_index_id(repository_id: str, commit_sha: str) -> str:
    return _digest("code-index", repository_id, commit_sha)


def make_module_id(repository_id: str, path: str) -> str:
    return _digest("code-module", repository_id, path)


def make_module_plan_id(code_index_id: str) -> str:
    return _digest("module-plan", code_index_id)


def make_architecture_edge_id(
    repository_id: str,
    relation_type: CodeRelationType | str,
    source_module_id: str,
    target_module_id: str,
    relation_ids: tuple[str, ...],
) -> str:
    return _digest(
        "architecture-edge",
        repository_id,
        str(relation_type),
        source_module_id,
        target_module_id,
        *sorted(relation_ids),
    )


def make_architecture_graph_id(code_index_id: str, module_plan_id: str) -> str:
    return _digest("architecture-graph", code_index_id, module_plan_id)


class CodeLocation(BaseModel):
    """Exact immutable SourceVersion range backing a code fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: SourceId
    source_version: SourceVersionId
    content_sha256: Sha256
    relative_path: str = Field(min_length=1)
    locator: str = Field(pattern=r"^chars:\d+-\d+$")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_location(self) -> CodeLocation:
        _validate_repository_path(self.relative_path)
        match = _CHAR_LOCATOR.fullmatch(self.locator)
        if match is None or int(match["end"]) <= int(match["start"]):
            raise ValueError("code locator must contain a non-empty character range")
        if self.end_line < self.start_line:
            raise ValueError("code end_line must not precede start_line")
        return self


class CodeSymbol(BaseModel):
    """One parser-derived symbol with stable identity and immutable evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol_id: Sha256
    repository_id: Sha256
    commit_sha: CommitId
    language: CodeLanguage
    kind: CodeSymbolKind
    qualified_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    visibility: CodeVisibility = CodeVisibility.UNKNOWN
    signature: str = Field(min_length=1)
    signature_sha256: Sha256
    body_sha256: Sha256
    location: CodeLocation

    @model_validator(mode="after")
    def validate_symbol(self) -> CodeSymbol:
        _validate_single_line(self.qualified_name, "qualified_name")
        _validate_single_line(self.display_name, "display_name")
        expected_id = make_code_symbol_id(
            self.repository_id,
            self.location.relative_path,
            self.language,
            self.kind,
            self.qualified_name,
        )
        if self.symbol_id != expected_id:
            raise ValueError("symbol_id does not match the canonical symbol identity")
        if hashlib.sha256(self.signature.encode()).hexdigest() != self.signature_sha256:
            raise ValueError("signature_sha256 does not match the exact signature")
        return self


class CodeRelation(BaseModel):
    """One parser-derived directed edge backed by at least one source range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_id: Sha256
    repository_id: Sha256
    commit_sha: CommitId
    type: CodeRelationType
    source_symbol_id: Sha256
    target_symbol_id: Sha256
    evidence: tuple[CodeLocation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relation(self) -> CodeRelation:
        expected_id = make_code_relation_id(
            self.repository_id,
            self.type,
            self.source_symbol_id,
            self.target_symbol_id,
        )
        if self.relation_id != expected_id:
            raise ValueError("relation_id does not match the canonical relation identity")
        if (
            self.source_symbol_id == self.target_symbol_id
            and self.type is not CodeRelationType.CALLS
        ):
            raise ValueError("only recursive CALLS relations may target their source symbol")
        evidence_keys = {
            (item.source_id, item.source_version, item.locator) for item in self.evidence
        }
        if len(evidence_keys) != len(self.evidence):
            raise ValueError("relation evidence must not contain duplicates")
        return self


class CodeIndexSnapshot(BaseModel):
    """Complete deterministic code index for one repository commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    index_id: Sha256
    repository_id: Sha256
    commit_sha: CommitId
    languages: tuple[CodeLanguage, ...] = Field(min_length=1)
    source_versions: dict[SourceId, SourceVersionId] = Field(default_factory=dict)
    symbols: tuple[CodeSymbol, ...] = ()
    relations: tuple[CodeRelation, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> CodeIndexSnapshot:
        if self.index_id != make_code_index_id(self.repository_id, self.commit_sha):
            raise ValueError("index_id does not match repository_id and commit_sha")
        if len(self.languages) != len(set(self.languages)):
            raise ValueError("snapshot languages must not contain duplicates")

        symbol_ids = [symbol.symbol_id for symbol in self.symbols]
        if len(symbol_ids) != len(set(symbol_ids)):
            raise ValueError("snapshot symbols must not contain duplicate IDs")
        symbols_by_id = {symbol.symbol_id: symbol for symbol in self.symbols}
        for symbol in self.symbols:
            if symbol.repository_id != self.repository_id or symbol.commit_sha != self.commit_sha:
                raise ValueError("snapshot symbol belongs to another repository revision")
            if symbol.language not in self.languages:
                raise ValueError("snapshot symbol language is not declared")
            self._validate_source_version(symbol.location)

        relation_ids = [relation.relation_id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("snapshot relations must not contain duplicate IDs")
        for relation in self.relations:
            if (
                relation.repository_id != self.repository_id
                or relation.commit_sha != self.commit_sha
            ):
                raise ValueError("snapshot relation belongs to another repository revision")
            if (
                relation.source_symbol_id not in symbols_by_id
                or relation.target_symbol_id not in symbols_by_id
            ):
                raise ValueError("snapshot relation references an unknown symbol")
            for evidence in relation.evidence:
                self._validate_source_version(evidence)

        used_sources = {symbol.location.source_id for symbol in self.symbols} | {
            evidence.source_id for relation in self.relations for evidence in relation.evidence
        }
        if used_sources != set(self.source_versions):
            raise ValueError("source_versions must exactly cover indexed code evidence")
        return self

    def _validate_source_version(self, location: CodeLocation) -> None:
        if self.source_versions.get(location.source_id) != location.source_version:
            raise ValueError("code evidence does not match the snapshot source version")


class ModuleNode(BaseModel):
    """One stable module in the hierarchical CodeWiki plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module_id: Sha256
    repository_id: Sha256
    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    wiki_path: str = Field(min_length=1)
    symbol_ids: tuple[Sha256, ...] = ()
    children: tuple[ModuleNode, ...] = ()

    @model_validator(mode="after")
    def validate_module(self) -> ModuleNode:
        _validate_module_path(self.path)
        _validate_single_line(self.title, "module title")
        _validate_single_line(self.summary, "module summary")
        if self.module_id != make_module_id(self.repository_id, self.path):
            raise ValueError("module_id does not match repository_id and module path")
        if self.wiki_path != f"wiki/pages/code/{self.path}.md":
            raise ValueError("module wiki_path must be derived from its module path")
        if not self.symbol_ids and not self.children:
            raise ValueError("module must own symbols or contain child modules")
        if len(self.symbol_ids) != len(set(self.symbol_ids)):
            raise ValueError("module symbol_ids must not contain duplicates")
        child_ids = [child.module_id for child in self.children]
        if len(child_ids) != len(set(child_ids)):
            raise ValueError("module children must not contain duplicates")
        for child in self.children:
            if child.repository_id != self.repository_id:
                raise ValueError("child module belongs to another repository")
            if child.path.rpartition("/")[0] != self.path:
                raise ValueError("child module path must be directly below its parent")
        return self


class ModulePlan(BaseModel):
    """Complete unique assignment of indexed symbols to hierarchical modules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    plan_id: Sha256
    repository_id: Sha256
    commit_sha: CommitId
    code_index_id: Sha256
    symbol_ids: tuple[Sha256, ...] = Field(min_length=1)
    modules: tuple[ModuleNode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_plan(self) -> ModulePlan:
        if self.code_index_id != make_code_index_id(self.repository_id, self.commit_sha):
            raise ValueError("module plan code_index_id belongs to another repository revision")
        if self.plan_id != make_module_plan_id(self.code_index_id):
            raise ValueError("plan_id does not match code_index_id")
        if len(self.symbol_ids) != len(set(self.symbol_ids)):
            raise ValueError("plan symbol_ids must not contain duplicates")

        assigned: list[str] = []
        module_ids: list[str] = []

        def collect(module: ModuleNode) -> None:
            if module.repository_id != self.repository_id:
                raise ValueError("module plan contains another repository")
            module_ids.append(module.module_id)
            assigned.extend(module.symbol_ids)
            for child in module.children:
                collect(child)

        for module in self.modules:
            if "/" in module.path:
                raise ValueError("top-level module paths must contain one segment")
            collect(module)
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("module plan contains duplicate module IDs")
        if len(assigned) != len(set(assigned)):
            raise ValueError("one symbol cannot belong to multiple modules")
        if set(assigned) != set(self.symbol_ids):
            raise ValueError("module plan must assign every declared symbol exactly once")
        return self


class ArchitectureNode(BaseModel):
    """Module-level node rendered into a deterministic architecture diagram."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module_id: Sha256
    repository_id: Sha256
    path: str = Field(min_length=1)
    label: str = Field(min_length=1)
    wiki_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_node(self) -> ArchitectureNode:
        _validate_module_path(self.path)
        _validate_single_line(self.label, "architecture node label")
        if self.module_id != make_module_id(self.repository_id, self.path):
            raise ValueError("architecture node module_id does not match its module path")
        if self.wiki_path != f"wiki/pages/code/{self.path}.md":
            raise ValueError("architecture node wiki_path must match its module path")
        return self


class ArchitectureEdge(BaseModel):
    """Aggregated module edge justified by concrete CodeRelation IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: Sha256
    repository_id: Sha256
    type: CodeRelationType
    source_module_id: Sha256
    target_module_id: Sha256
    relation_ids: tuple[Sha256, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_edge(self) -> ArchitectureEdge:
        if len(self.relation_ids) != len(set(self.relation_ids)):
            raise ValueError("architecture relation_ids must not contain duplicates")
        expected_id = make_architecture_edge_id(
            self.repository_id,
            self.type,
            self.source_module_id,
            self.target_module_id,
            self.relation_ids,
        )
        if self.edge_id != expected_id:
            raise ValueError("edge_id does not match the canonical architecture edge")
        return self


class ArchitectureGraph(BaseModel):
    """Validated module graph ready for deterministic Mermaid compilation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    graph_id: Sha256
    repository_id: Sha256
    commit_sha: CommitId
    code_index_id: Sha256
    module_plan_id: Sha256
    nodes: tuple[ArchitectureNode, ...] = Field(min_length=1)
    edges: tuple[ArchitectureEdge, ...] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> ArchitectureGraph:
        if self.code_index_id != make_code_index_id(self.repository_id, self.commit_sha):
            raise ValueError("architecture code_index_id belongs to another repository revision")
        if self.module_plan_id != make_module_plan_id(self.code_index_id):
            raise ValueError("architecture module_plan_id does not match code_index_id")
        if self.graph_id != make_architecture_graph_id(
            self.code_index_id,
            self.module_plan_id,
        ):
            raise ValueError("graph_id does not match its index and module plan")
        node_ids = [node.module_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("architecture nodes must not contain duplicate module IDs")
        if any(node.repository_id != self.repository_id for node in self.nodes):
            raise ValueError("architecture node belongs to another repository")
        known_nodes = set(node_ids)
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("architecture edges must not contain duplicate IDs")
        for edge in self.edges:
            if edge.repository_id != self.repository_id:
                raise ValueError("architecture edge belongs to another repository")
            if edge.source_module_id not in known_nodes or edge.target_module_id not in known_nodes:
                raise ValueError("architecture edge references an unknown module")
        return self


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _validate_repository_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        "\\" in value
        or "\x00" in value
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError("code path must be a canonical repository-relative POSIX path")


def _validate_module_path(value: str) -> None:
    if _MODULE_PATH.fullmatch(value) is None:
        raise ValueError("module path must contain lowercase kebab-case segments")


def _validate_single_line(value: str, field_name: str) -> None:
    if value != value.strip() or len(value.splitlines()) != 1 or "\x00" in value:
        raise ValueError(f"{field_name} must be one canonical non-empty line")
