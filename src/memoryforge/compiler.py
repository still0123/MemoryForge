"""Deterministic source-page compiler for the first reviewable Wiki workflow."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from memoryforge.changesets import ChangeSetStore, StoredChangeSet
from memoryforge.errors import LifecycleError
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import (
    ChangeOperation,
    ChangeOperationType,
    ChangeSet,
    ChangeSetStatus,
    ChangeSetValidation,
    SourceDocument,
)
from memoryforge.workspace import Workspace

HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class CompileResult:
    """A newly staged ChangeSet and the sources it covers."""

    stored: StoredChangeSet
    source_ids: tuple[str, ...]


class WikiCompiler:
    """Compiles pending immutable sources into reviewable, evidence-backed pages."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.manifests = SourceManifestStore(workspace.manifest_dir)
        self.changesets = ChangeSetStore(workspace)

    def compile_pending(self, source_ids: tuple[str, ...] = ()) -> CompileResult:
        """Stage deterministic source pages without mutating the stable Wiki."""

        selected = self._select_sources(source_ids)
        if not selected:
            raise LifecycleError("No pending sources to compile.")

        candidates: dict[str, str] = {}
        operations: list[ChangeOperation] = []
        for source in selected:
            path = f"wiki/sources/{source.source_id}.md"
            candidates[path] = self._render_source_page(source)
            operations.append(
                ChangeOperation(
                    type=(
                        ChangeOperationType.UPDATE_PAGE
                        if (self.workspace.root / path).is_file()
                        else ChangeOperationType.CREATE_PAGE
                    ),
                    path=path,
                    details={"source_id": source.source_id},
                )
            )

        index_path = "wiki/INDEX.md"
        candidates[index_path] = self._render_index(selected)
        operations.append(
            ChangeOperation(
                type=ChangeOperationType.UPDATE_PAGE,
                path=index_path,
                details={"source_ids": [source.source_id for source in selected]},
            )
        )

        base_commit = self.workspace.current_commit()
        changeset_id = self._changeset_id(base_commit, selected)
        changeset = ChangeSet(
            changeset_id=changeset_id,
            base_commit=base_commit,
            source_ids=tuple(source.source_id for source in selected),
            status=ChangeSetStatus.PROPOSED,
            operations=tuple(operations),
            validation=ChangeSetValidation(
                citation_coverage=1.0,
                unresolved_conflicts=0,
                schema_errors=0,
            ),
        )
        stored = self.changesets.create(changeset, candidates)
        return CompileResult(stored=stored, source_ids=changeset.source_ids)

    def _select_sources(self, requested_ids: tuple[str, ...]) -> list[SourceDocument]:
        all_sources = self.manifests.list_all()
        by_id = {source.source_id: source for source in all_sources}
        if requested_ids:
            missing = sorted(set(requested_ids) - set(by_id))
            if missing:
                raise LifecycleError(f"Unknown source IDs: {', '.join(missing)}")
            selected = [by_id[source_id] for source_id in set(requested_ids)]
        else:
            staged_ids = {
                source_id
                for stored in self.changesets.list_all()
                for source_id in stored.changeset.source_ids
            }
            selected = [source for source in all_sources if source.source_id not in staged_ids]
        return sorted(selected, key=lambda source: source.source_id)

    def _render_source_page(self, source: SourceDocument) -> str:
        raw_path = self.workspace.root / source.uri
        content = raw_path.read_text(encoding="utf-8")
        title_match = HEADING_PATTERN.search(content)
        title = title_match.group(1).strip() if title_match else Path(source.uri).stem
        line_count = max(1, len(content.splitlines()))
        body = content.rstrip()
        return (
            "---\n"
            f'title: "{_yaml_string(title)}"\n'
            "kind: source\n"
            f"source_id: {source.source_id}\n"
            f"source_sha256: {source.content_sha256}\n"
            f"source_uri: {source.uri}\n"
            f"observed_at: {source.observed_at.isoformat() if source.observed_at else 'null'}\n"
            "---\n\n"
            f"# {title}\n\n"
            f"> Evidence: `{source.source_id}` · `{source.uri}:L1-L{line_count}` · "
            f"`sha256:{source.content_sha256}`\n\n"
            "## Source content\n\n"
            f"{body}\n"
        )

    def _render_index(self, selected: list[SourceDocument]) -> str:
        source_ids = {
            path.stem
            for path in (self.workspace.wiki_dir / "sources").glob("src_*.md")
        }
        source_ids.update(source.source_id for source in selected)
        lines = ["# Knowledge Index", "", "## Sources", ""]
        lines.extend(
            f"- [[sources/{source_id}|{source_id}]]" for source_id in sorted(source_ids)
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _changeset_id(base_commit: str, sources: list[SourceDocument]) -> str:
        identity = "\n".join(
            [base_commit, *(f"{source.source_id}:{source.content_sha256}" for source in sources)]
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"chg_{digest}"


def _yaml_string(value: str) -> str:
    """Escape a short title for a double-quoted YAML scalar."""

    return value.replace("\\", "\\\\").replace('"', '\\"')
