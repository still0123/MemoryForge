"""Shared deterministic primitives for language-specific code index adapters."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from tree_sitter import Node

from memoryforge.code_models import (
    CodeLocation,
    CodeRelation,
    CodeRelationType,
    make_code_relation_id,
)
from memoryforge.workspace import CurrentGitSourceVersion

RelationEvidence = dict[tuple[CodeRelationType, str, str], list[CodeLocation]]


class CodeIndexError(ValueError):
    """Raised when immutable source cannot produce a trustworthy code index."""


@dataclass(frozen=True)
class ParsedCodeSource:
    record: CurrentGitSourceVersion
    text: str
    content: bytes


def new_relation_evidence() -> RelationEvidence:
    return defaultdict(list)


def append_relation_evidence(
    relations: RelationEvidence,
    relation_type: CodeRelationType,
    source_symbol_id: str,
    target_symbol_id: str,
    evidence: CodeLocation,
) -> None:
    relations[(relation_type, source_symbol_id, target_symbol_id)].append(evidence)


def build_relations(
    repository_id: str,
    commit_sha: str,
    relation_evidence: RelationEvidence,
) -> tuple[CodeRelation, ...]:
    relations: list[CodeRelation] = []
    for (relation_type, source_id, target_id), locations in sorted(
        relation_evidence.items(),
        key=lambda item: (item[0][0].value, item[0][1], item[0][2]),
    ):
        unique = {
            (location.source_id, location.source_version, location.locator): location
            for location in locations
        }
        evidence = tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    item.relative_path,
                    item.start_line,
                    item.locator,
                ),
            )
        )
        relations.append(
            CodeRelation(
                relation_id=make_code_relation_id(
                    repository_id,
                    relation_type,
                    source_id,
                    target_id,
                ),
                repository_id=repository_id,
                commit_sha=commit_sha,
                type=relation_type,
                source_symbol_id=source_id,
                target_symbol_id=target_id,
                evidence=evidence,
            )
        )
    return tuple(relations)


def source_location(source: ParsedCodeSource) -> CodeLocation:
    return CodeLocation(
        source_id=source.record.source_id,
        source_version=source.record.source_version,
        content_sha256=source.record.content_sha256,
        relative_path=source.record.relative_path,
        locator=f"chars:0-{len(source.text)}",
        start_line=1,
        end_line=max(1, len(source.text.splitlines())),
    )


def node_location(source: ParsedCodeSource, node: Node) -> CodeLocation:
    start = byte_to_character_offset(source.content, node.start_byte)
    end = byte_to_character_offset(source.content, node.end_byte)
    return CodeLocation(
        source_id=source.record.source_id,
        source_version=source.record.source_version,
        content_sha256=source.record.content_sha256,
        relative_path=source.record.relative_path,
        locator=f"chars:{start}-{end}",
        start_line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
    )


def node_text(source: ParsedCodeSource, node: Node) -> str:
    return source.content[node.start_byte : node.end_byte].decode("utf-8")


def node_key(node: Node) -> tuple[str, int, int]:
    return node.type, node.start_byte, node.end_byte


def byte_to_character_offset(content: bytes, byte_offset: int) -> int:
    try:
        return len(content[:byte_offset].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CodeIndexError("Tree-sitter returned an invalid UTF-8 byte boundary") from exc
