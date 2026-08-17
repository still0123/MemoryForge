"""In-memory catalog for the read-only local knowledge portal."""

from __future__ import annotations

import json
import posixpath
import re
import sqlite3
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from memoryforge.portal.showcase import _markdown_document
from memoryforge.storage.database import connect_readonly as _connect_readonly
from memoryforge.storage.workspace import (
    Workspace,
    find_applied_page_paths,
    find_applied_wiki_fact_page_paths,
)

_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HEADING = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((?P<target>[^)#]+\.md)(?:#[^)]*)?\)")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_./@-]+")
_CODE_EXTENSIONS = {
    ".go": "Go",
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}


@dataclass(frozen=True)
class SourceEntry:
    source_id: str
    title: str
    kind: str
    updated: str
    version: int
    applied: bool
    repository_id: str | None
    relative_path: str | None
    tags: tuple[str, ...]
    page_paths: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "ref": self.source_id[:16],
            "name": self.title,
            "kind": self.kind,
            "updated": self.updated,
            "status": "已应用" if self.applied else "待应用",
            "current_version": self.version,
            "page_count": len(self.page_paths),
        }

    def applied_public(self) -> dict[str, Any]:
        return {
            **self.public(),
            "source_id": self.source_id,
            "source_version": self.version,
        }


@dataclass(frozen=True)
class PageEntry:
    path: str
    title: str
    summary: str
    kind: str
    subtype: str
    repository_id: str | None
    repository_name: str | None
    updated: str
    status: str
    tags: tuple[str, ...]
    source_ids: tuple[str, ...]
    freshness_state: str
    based_on_commit: str
    current_commit: str
    module_path: str | None = None
    relative_path: str | None = None
    headings: tuple[tuple[int, str], ...] = ()

    def public(self) -> dict[str, Any]:
        project = None
        if self.repository_id is not None:
            project = {
                "repository_id": self.repository_id,
                "name": self.repository_name or "未命名项目",
            }
        return {
            "path": self.path,
            "title": self.title,
            "summary": self.summary,
            "kind": self.kind,
            "template": self.subtype,
            "project": project,
            "module_path": self.module_path,
            "relative_path": self.relative_path,
            "updated": self.updated,
            "status": self.status,
            "freshness": {
                "state": self.freshness_state,
                "based_on_commit": self.based_on_commit[:12],
                "current_commit": self.current_commit[:12],
                "label": (
                    f"{_freshness_label(self.freshness_state)} · "
                    f"基于 {self.based_on_commit[:7]} · 当前 {self.current_commit[:7]}"
                ),
            },
        }


@dataclass
class RepositoryEntry:
    repository_id: str
    name: str
    commit: str
    page_paths: list[str] = field(default_factory=list)
    module_paths: set[str] = field(default_factory=set)
    languages: set[str] = field(default_factory=set)
    evidence_file_count: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "name": self.name,
            "commit": self.commit[:12],
            "page_count": len(self.page_paths),
            "knowledge_page_count": len(self.page_paths),
            "module_count": len(self.module_paths),
            "evidence_file_count": self.evidence_file_count,
            "languages": sorted(self.languages),
            "top_modules": _top_modules(self.module_paths),
        }


@dataclass(frozen=True)
class Relation:
    path: str
    relationship: str
    detail: str


class PortalCatalog:
    """Metadata and deterministic relations for one Workspace Commit."""

    def __init__(self, workspace: Workspace, commit: str) -> None:
        self.workspace = workspace
        self.commit = commit
        self.pages: dict[str, PageEntry] = {}
        self.sources: dict[str, SourceEntry] = {}
        self.applied_sources: dict[str, SourceEntry] = {}
        self.repositories: dict[str, RepositoryEntry] = {}
        self.direct: dict[str, tuple[Relation, ...]] = {}
        self.mentions: dict[str, tuple[Relation, ...]] = {}
        self.parent_by_page: dict[str, str] = {}
        self.current_source_count = 0
        self.source_identity_count = 0
        self.pending_source_count = 0
        self._build()

    def summary(self) -> dict[str, Any]:
        kind_counts: dict[str, int] = defaultdict(int)
        for page in self.pages.values():
            kind_counts[page.kind] += 1
        applied_sources = sum(source.applied for source in self.sources.values())
        project_count = sum(
            bool(repository.page_paths) for repository in self.repositories.values()
        )
        return {
            "workspace_commit": self.commit,
            "current_source_count": self.current_source_count,
            "applied_page_count": len(self.pages),
            "source_identity_count": self.source_identity_count,
            "projects": project_count,
            "code_pages": kind_counts["code"],
            "conversation_pages": kind_counts["conversation"],
            "feishu_pages": kind_counts["feishu"],
            "note_pages": kind_counts["note"],
            "pending_sources": self.pending_source_count,
            "page_count": len(self.pages),
            "source_count": self.current_source_count,
            "applied_source_count": applied_sources,
        }

    def list_projects(self) -> dict[str, Any]:
        items = [
            repository.public()
            for repository in sorted(self.repositories.values(), key=lambda item: item.name.lower())
            if repository.page_paths
        ]
        return {"workspace_commit": self.commit, "items": items}

    def project(self, repository_id: str) -> dict[str, Any]:
        repository = self.repositories.get(repository_id)
        if repository is None:
            raise ValueError("unknown project")
        pages = [self.pages[path] for path in repository.page_paths if path in self.pages]
        related_paths: set[str] = set()
        project_paths = set(repository.page_paths)
        for path in project_paths:
            for relation in (*self.direct.get(path, ()), *self.mentions.get(path, ())):
                related = self.pages.get(relation.path)
                if (
                    related is not None
                    and related.path not in project_paths
                    and related.kind in {"conversation", "feishu", "note"}
                ):
                    related_paths.add(related.path)
        all_project_pages = [
            page for page in self.pages.values() if page.repository_id == repository_id
        ]
        file_pages = [
            page
            for page in all_project_pages
            if page.kind == "code" and page.subtype == "code_file"
        ]
        module_pages = [
            page
            for page in pages
            if page.kind == "code"
            and page.subtype == "code_module"
            and page.module_path is not None
            and page.path.startswith("wiki/pages/code/")
        ]
        return {
            "workspace_commit": self.commit,
            **repository.public(),
            "overview": [page.public() for page in pages if page.kind == "project"],
            "modules": [
                page.public()
                for page in pages
                if page.kind == "code"
                and page.subtype == "code_module"
                and page.module_path is not None
                and "/" not in page.module_path
            ],
            "module_tree": _module_tree(module_pages),
            # A large repository can contain thousands of file pages. The portal
            # starts from architecture and modules; it keeps only a small set of
            # file examples in this response and leaves exhaustive lookup to search.
            "file_count": len(file_pages),
            "files": [page.public() for page in file_pages[:12]],
            "related": [self.pages[path].public() for path in sorted(related_paths)],
        }

    def list_sources(self, kind: str, *, offset: int, limit: int) -> dict[str, Any]:
        if kind not in {"code", "feishu", "conversation", "note"}:
            raise ValueError("invalid source kind")
        matches = sorted(
            (source for source in self.sources.values() if source.kind == kind),
            key=lambda source: (source.updated, source.title.lower()),
            reverse=True,
        )
        return {
            "workspace_commit": self.commit,
            "total": len(matches),
            "offset": offset,
            "limit": limit,
            "items": [source.public() for source in matches[offset : offset + limit]],
        }

    def source_details(self, source_ref: str) -> dict[str, Any]:
        if re.fullmatch(r"[a-f0-9]{16}", source_ref) is None:
            raise ValueError("invalid source")
        matches = [
            source for source in self.sources.values() if source.source_id.startswith(source_ref)
        ]
        if len(matches) != 1:
            raise ValueError("unknown source")
        source = matches[0]
        project = self.repositories.get(source.repository_id or "")
        return {
            "workspace_commit": self.commit,
            **source.public(),
            "source_id": source.source_id,
            "tags": list(source.tags),
            "project": project.public() if project is not None else None,
            "refreshable": source.repository_id is not None or "feishu" in source.tags,
        }

    def require_applied_source_version(self, source_id: str, source_version: int) -> None:
        source = self.applied_sources.get(source_id)
        if source is None or source.version != source_version:
            raise FileNotFoundError("source version is not applied")

    def list_pages(
        self,
        query: str,
        *,
        kind: str,
        project: str,
        parent: str,
        view: str = "all",
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if kind and kind not in {"project", "code", "feishu", "conversation", "note"}:
            raise ValueError("invalid page kind")
        if view not in {"all", "published", "evidence"}:
            raise ValueError("invalid page view")
        if project and project not in self.repositories:
            raise ValueError("unknown project")
        matches = list(self.pages.values())
        if view != "all":
            matches = [page for page in matches if _page_in_view(page, view)]
        if view == "published" and not any((query.strip(), kind, project, parent)):
            matches = [page for page in matches if page.kind != "code"]
        if kind:
            matches = [page for page in matches if page.kind == kind]
        if project:
            matches = [page for page in matches if page.repository_id == project]
        if parent:
            matches = [
                page
                for page in matches
                if self.parent_by_page.get(page.path) == parent
                or (page.module_path is not None and _module_parent(page.module_path) == parent)
            ]
        needle = query.strip().casefold()
        if needle:
            fts_paths: set[str] = set()
            with suppress(ValueError, sqlite3.Error):
                fts_paths.update(
                    find_applied_page_paths(
                        self.workspace.root,
                        query,
                        limit=100,
                        repository_id=project or None,
                        require_all_terms=False,
                    )
                )
            with suppress(ValueError, sqlite3.Error):
                fts_paths.update(
                    find_applied_wiki_fact_page_paths(
                        self.workspace.root,
                        query.split(),
                        limit=100,
                        repository_id=project or None,
                    )
                )
            matches = [
                page
                for page in matches
                if needle in " ".join((page.title, page.path, page.summary)).casefold()
                or page.path in fts_paths
            ]
        matches.sort(key=lambda page: (page.kind, page.title.casefold(), page.path))
        return {
            "workspace_commit": self.commit,
            "total": len(matches),
            "offset": offset,
            "limit": limit,
            "items": [page.public() for page in matches[offset : offset + limit]],
        }

    def page_details(self, page_path: str) -> dict[str, Any]:
        page = self.pages.get(page_path)
        if page is None:
            raise FileNotFoundError("page not found")
        direct_paths = {relation.path for relation in self.direct.get(page_path, ())}
        mention_paths = {relation.path for relation in self.mentions.get(page_path, ())}
        excluded = direct_paths | mention_paths | {page_path}
        same_project = []
        if page.repository_id is not None:
            same_project = [
                Relation(path=path, relationship="同项目", detail="共享 repository_id")
                for path in self.repositories[page.repository_id].page_paths
                if path not in excluded
            ][:12]
        return {
            **page.public(),
            "workspace_commit": self.commit,
            "breadcrumbs": self._breadcrumbs(page),
            "structure": [{"level": level, "title": title} for level, title in page.headings],
            "sources": [
                self.applied_sources[source_id].applied_public()
                for source_id in page.source_ids
                if source_id in self.applied_sources
            ],
            "related": {
                "direct": self._public_relations(self.direct.get(page_path, ())),
                "exact_mentions": self._public_relations(self.mentions.get(page_path, ())),
                "same_project": self._public_relations(same_project),
            },
            "template": page.subtype,
            "module_path": page.module_path,
            "relative_path": page.relative_path,
        }

    def _build(self) -> None:
        page_paths = tuple(
            path
            for path in self.workspace.version_store.list_wiki_paths_at(self.commit)
            if _is_page_path(path)
        )
        contents = self.workspace.version_store.read_wiki_texts_at(self.commit, paths=page_paths)
        page_commits = self.workspace.version_store.latest_wiki_commits(
            self.commit,
            page_paths,
        )
        with _connect_readonly(self.workspace.index_path) as connection:
            repositories = connection.execute(
                """
                SELECT repository_id, name, COALESCE(last_synced_commit, '') AS synced_commit
                FROM git_repositories
                ORDER BY name, repository_id
                """
            ).fetchall()
            source_rows = connection.execute(
                """
                SELECT
                    sources.source_id, sources.source_path, versions.id AS source_version,
                    versions.title, versions.observed_at, versions.tags_json,
                    applied.source_version_id AS applied_version,
                    revisions.repository_id, revisions.relative_path, revisions.commit_sha
                FROM source_versions AS versions
                JOIN sources ON sources.id = versions.source_id
                LEFT JOIN applied_source_versions AS applied
                  ON applied.source_id = sources.source_id
                LEFT JOIN git_source_revisions AS revisions
                  ON revisions.source_version_id = versions.id
                WHERE versions.is_current = 1
                ORDER BY sources.source_id
                """
            ).fetchall()
            applied_source_rows = connection.execute(
                """
                SELECT
                    sources.source_id, sources.source_path, versions.id AS source_version,
                    versions.title, versions.observed_at, versions.tags_json,
                    revisions.repository_id, revisions.relative_path
                FROM applied_source_versions AS applied
                JOIN sources ON sources.source_id = applied.source_id
                JOIN source_versions AS versions ON versions.id = applied.source_version_id
                LEFT JOIN git_source_revisions AS revisions
                  ON revisions.source_version_id = versions.id
                ORDER BY sources.source_id
                """
            ).fetchall()
            page_source_rows = connection.execute(
                """
                SELECT
                    page_sources.page_path, sources.source_id, sources.source_path,
                    versions.title, versions.observed_at, versions.tags_json,
                    revisions.repository_id, revisions.relative_path
                FROM page_sources
                JOIN sources ON sources.source_id = page_sources.source_id
                JOIN applied_source_versions AS applied
                  ON applied.source_id = sources.source_id
                JOIN source_versions AS versions ON versions.id = applied.source_version_id
                LEFT JOIN git_source_revisions AS revisions
                  ON revisions.source_version_id = versions.id
                ORDER BY page_sources.page_path, sources.source_id
                """
            ).fetchall()
            fact_rows = connection.execute(
                """
                SELECT page_path, repository_id, symbol
                FROM wiki_facts
                WHERE repository_id IS NOT NULL OR symbol IS NOT NULL
                ORDER BY page_path, id
                """
            ).fetchall()
            module_rows = connection.execute(
                "SELECT repository_id, relative_path FROM git_code_modules"
            ).fetchall()
            self.source_identity_count = int(
                connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            )

        for row in repositories:
            repo_id = str(row["repository_id"])
            self.repositories[repo_id] = RepositoryEntry(
                repository_id=repo_id,
                name=str(row["name"]),
                commit=str(row["synced_commit"]),
            )

        source_page_paths: dict[str, list[str]] = defaultdict(list)
        for row in page_source_rows:
            source_page_paths[str(row["source_id"])].append(str(row["page_path"]))
        self.current_source_count = len(source_rows)
        self.pending_source_count = sum(
            int(row["applied_version"] or 0) != int(row["source_version"]) for row in source_rows
        )
        source_paths: dict[str, str] = {}
        for row in source_rows:
            source_id = str(row["source_id"])
            tags = _tags(str(row["tags_json"]))
            source_path = str(row["source_path"])
            source_repository_id = (
                str(row["repository_id"]) if row["repository_id"] is not None else None
            )
            source_paths[source_id] = source_path
            self.sources[source_id] = SourceEntry(
                source_id=source_id,
                title=str(row["title"]),
                kind=_source_kind(tags, source_path, source_repository_id),
                updated=str(row["observed_at"]),
                version=int(row["source_version"]),
                applied=int(row["applied_version"] or 0) == int(row["source_version"]),
                repository_id=source_repository_id,
                relative_path=(
                    str(row["relative_path"]) if row["relative_path"] is not None else None
                ),
                tags=tags,
                page_paths=tuple(source_page_paths[source_id]),
            )
        for row in applied_source_rows:
            source_id = str(row["source_id"])
            tags = _tags(str(row["tags_json"]))
            source_path = str(row["source_path"])
            repository_id = (
                str(row["repository_id"]) if row["repository_id"] is not None else None
            )
            self.applied_sources[source_id] = SourceEntry(
                source_id=source_id,
                title=str(row["title"]),
                kind=_source_kind(tags, source_path, repository_id),
                updated=str(row["observed_at"]),
                version=int(row["source_version"]),
                applied=True,
                repository_id=repository_id,
                relative_path=(
                    str(row["relative_path"]) if row["relative_path"] is not None else None
                ),
                tags=tags,
                page_paths=tuple(source_page_paths[source_id]),
            )

        page_sources: dict[str, list[str]] = defaultdict(list)
        page_repositories: dict[str, set[str]] = defaultdict(set)
        page_relative_paths: dict[str, list[str]] = defaultdict(list)
        page_tags: dict[str, set[str]] = defaultdict(set)
        page_updates: dict[str, list[str]] = defaultdict(list)
        for row in page_source_rows:
            page_path = str(row["page_path"])
            source_id = str(row["source_id"])
            page_sources[page_path].append(source_id)
            page_tags[page_path].update(_tags(str(row["tags_json"])))
            page_updates[page_path].append(str(row["observed_at"]))
            if row["repository_id"] is not None:
                page_repositories[page_path].add(str(row["repository_id"]))
            if row["relative_path"] is not None:
                page_relative_paths[page_path].append(str(row["relative_path"]))
        symbols_by_page: dict[str, set[str]] = defaultdict(set)
        for row in fact_rows:
            page_path = str(row["page_path"])
            if row["repository_id"] is not None:
                page_repositories[page_path].add(str(row["repository_id"]))
            if row["symbol"] is not None:
                symbols_by_page[page_path].add(str(row["symbol"]))
        for row in module_rows:
            repository = self.repositories.get(str(row["repository_id"]))
            if repository is not None:
                repository.module_paths.add(str(row["relative_path"]))

        scan_texts: dict[str, str] = {}
        for page_path in page_paths:
            content = contents.get(page_path)
            if content is None:
                continue
            metadata, body = _markdown_document(content)
            metadata_tags = _metadata_tags(metadata.get("tags", ""))
            tags = tuple(sorted(set(metadata_tags) | page_tags[page_path]))
            page_repository_id = _single_repository(
                metadata.get("repository_id"), page_repositories[page_path]
            )
            generated = metadata.get("generated", "")
            source_ids = tuple(page_sources[page_path])
            if not source_ids:
                freshness_state = "unknown"
            elif all(
                source_id in self.sources and self.sources[source_id].applied
                for source_id in source_ids
            ):
                freshness_state = "fresh"
            else:
                freshness_state = "stale"
            source_kinds = {self.sources[source_id].kind for source_id in source_ids}
            kind = _page_kind(tags, generated, source_kinds, page_repository_id)
            relative_paths = page_relative_paths[page_path]
            relative_path = relative_paths[0] if len(relative_paths) == 1 else None
            module_path = _module_path(page_path, page_repository_id) if kind == "code" else None
            subtype = _page_subtype(
                kind,
                generated,
                relative_path,
                metadata.get("module_id"),
            )
            title_match = _TITLE.search(body)
            title = metadata.get("title") or (
                title_match.group(1) if title_match is not None else PurePosixPath(page_path).stem
            )
            repository = self.repositories.get(page_repository_id or "")
            page = PageEntry(
                path=page_path,
                title=title,
                summary=metadata.get("summary", ""),
                kind=kind,
                subtype=subtype,
                repository_id=page_repository_id,
                repository_name=repository.name if repository is not None else None,
                updated=metadata.get("updated") or max(page_updates[page_path], default=""),
                status="未验证会话记忆" if kind == "conversation" else "已应用",
                tags=tags,
                source_ids=source_ids,
                freshness_state=freshness_state,
                based_on_commit=page_commits.get(page_path, self.commit),
                current_commit=self.commit,
                module_path=module_path,
                relative_path=relative_path,
                headings=tuple(
                    (len(match.group(1)), match.group(2)) for match in _HEADING.finditer(body)
                ),
            )
            self.pages[page_path] = page
            if repository is not None:
                repository.page_paths.append(page_path)
                if page.kind == "code" and page.subtype == "code_file":
                    repository.evidence_file_count += 1
                if module_path:
                    repository.module_paths.add(module_path)
                for path in relative_paths:
                    language = _CODE_EXTENSIONS.get(PurePosixPath(path).suffix.lower())
                    if language:
                        repository.languages.add(language)
            if kind in {"conversation", "feishu", "note", "project"}:
                scan_texts[page_path] = body

        for repository in self.repositories.values():
            current_code_pages = [
                path for path in repository.page_paths if self.pages[path].module_path is not None
            ]
            if current_code_pages:
                # New CodeWiki pages use repository-scoped paths. Keep this
                # current hierarchy as the project view; legacy hash-named
                # pages remain searchable but no longer bury the reader.
                repository.page_paths = current_code_pages

        direct: dict[str, dict[str, Relation]] = defaultdict(dict)
        mentions: dict[str, dict[str, Relation]] = defaultdict(dict)
        for page_path, body in scan_texts.items():
            for match in _MARKDOWN_LINK.finditer(body):
                target = _resolve_link(page_path, match.group("target"))
                if target in self.pages and target != page_path:
                    _add_pair(direct, page_path, target, "Markdown 内部链接")

        pages_by_source_path: dict[str, list[str]] = defaultdict(list)
        for page in self.pages.values():
            for source_id in page.source_ids:
                linked_source_path = source_paths.get(source_id)
                if linked_source_path:
                    pages_by_source_path[linked_source_path].append(page.path)
        for source_path, child_pages in pages_by_source_path.items():
            parent_path = _feishu_parent_source_path(source_path)
            if parent_path is None:
                continue
            for child in child_pages:
                for parent in pages_by_source_path.get(parent_path, ()):
                    if child != parent:
                        self.parent_by_page[child] = parent
                        _add_pair(direct, child, parent, "飞书父文档与章节")

        identifiers: dict[str, set[str]] = defaultdict(set)
        for page in self.pages.values():
            if page.kind != "code":
                continue
            if page.relative_path:
                identifiers[page.relative_path].add(page.path)
            if page.module_path:
                identifiers[page.module_path].add(page.path)
        for page_path, symbols in symbols_by_page.items():
            for symbol in symbols:
                identifiers[symbol].add(page_path)
        for source_path, body in scan_texts.items():
            for identifier in set(_IDENTIFIER.findall(body)):
                targets = identifiers.get(identifier, ())
                for target in targets:
                    if target != source_path and target in self.pages:
                        _add_pair(mentions, source_path, target, f"完整标识：{identifier}")

        self.direct = _freeze_relations(direct)
        self.mentions = _freeze_relations(mentions)

    def _breadcrumbs(self, page: PageEntry) -> list[dict[str, str]]:
        if page.repository_id is not None:
            crumbs = [
                {"label": "项目", "route": "#projects"},
                {
                    "label": page.repository_name or "未命名项目",
                    "route": f"#project={page.repository_id}",
                },
            ]
            if page.module_path:
                parent = _module_parent(page.module_path)
                if parent:
                    crumbs.append({"label": parent, "route": f"#project={page.repository_id}"})
            crumbs.append({"label": page.title, "route": f"#page={page.path}"})
            return crumbs
        section = {
            "conversation": ("AI 会话", "#sources=conversation"),
            "feishu": ("飞书资料", "#sources=feishu"),
            "note": ("其他知识", "#sources=note"),
        }.get(page.kind, ("知识", "#home"))
        return [
            {"label": section[0], "route": section[1]},
            {"label": page.title, "route": f"#page={page.path}"},
        ]

    def _public_relations(
        self,
        relations: tuple[Relation, ...] | list[Relation],
    ) -> list[dict[str, Any]]:
        items = []
        for relation in relations:
            page = self.pages.get(relation.path)
            if page is None:
                continue
            items.append(
                {
                    **page.public(),
                    "relationship": relation.relationship,
                    "detail": relation.detail,
                }
            )
        return items


def _tags(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(item) for item in decoded if isinstance(item, str))


def _metadata_tags(value: str) -> tuple[str, ...]:
    return _tags(value) if value else ()


def _freshness_label(state: str) -> str:
    return {
        "fresh": "新鲜",
        "stale": "过期",
        "superseded": "已取代",
        "conflicted": "有冲突",
        "unknown": "未知",
    }.get(state, "未知")


def _source_kind(tags: tuple[str, ...], source_path: str, repository_id: str | None) -> str:
    if "conversation" in tags:
        return "conversation"
    if "feishu" in tags or source_path.startswith("feishu/"):
        return "feishu"
    if repository_id is not None:
        return "code"
    return "note"


def _page_kind(
    tags: tuple[str, ...],
    generated: str,
    source_kinds: set[str],
    repository_id: str | None,
) -> str:
    if "conversation" in tags or "conversation" in source_kinds:
        return "conversation"
    if "feishu" in tags or "feishu" in source_kinds:
        return "feishu"
    if "repository" in tags and generated == "repository_overview":
        return "project"
    if repository_id is not None:
        return "code"
    return "note"


def _page_subtype(
    kind: str,
    generated: str,
    relative_path: str | None,
    module_id: str | None,
) -> str:
    if kind == "project":
        return "project"
    if kind == "code":
        if generated == "code_wiki" and module_id:
            return "code_module"
        if relative_path is not None:
            return "code_file"
        return "code_module"
    return kind


def _single_repository(metadata_id: str | None, repository_ids: set[str]) -> str | None:
    if metadata_id:
        return metadata_id
    if len(repository_ids) == 1:
        return next(iter(repository_ids))
    return None


def _module_path(page_path: str, repository_id: str | None) -> str | None:
    if repository_id is None:
        return None
    prefix = f"wiki/pages/code/{repository_id[:12]}/"
    if not page_path.startswith(prefix):
        return None
    return page_path.removeprefix(prefix).removesuffix(".md")


def _module_parent(path: str) -> str:
    parent = PurePosixPath(path).parent
    return "" if parent == PurePosixPath(".") else parent.as_posix()


def _top_modules(modules: set[str]) -> list[str]:
    return sorted({path.split("/", 1)[0] for path in modules if path and path != "."})


def _page_in_view(page: PageEntry, view: str) -> bool:
    """Keep the default portal view focused on current narrative knowledge."""
    canonical_module = (
        page.kind == "code"
        and page.subtype == "code_module"
        and page.path.startswith("wiki/pages/code/")
    )
    if view == "published":
        return page.kind != "code" or canonical_module
    if view == "evidence":
        return page.kind == "code" and page.subtype == "code_file"
    return True


def _module_tree(pages: list[PageEntry]) -> list[dict[str, Any]]:
    nodes = {
        page.module_path: {**page.public(), "children": []}
        for page in pages
        if page.module_path
    }
    roots: list[dict[str, Any]] = []
    for path in sorted(nodes):
        parent = _module_parent(path)
        node = nodes[path]
        if parent in nodes:
            nodes[parent]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _resolve_link(page_path: str, target: str) -> str | None:
    if target.startswith("/") or "\\" in target:
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), target))
    return resolved if _is_page_path(resolved) else None


def _feishu_parent_source_path(source_path: str) -> str | None:
    parts = PurePosixPath(source_path).parts
    if len(parts) != 3 or parts[0] != "feishu":
        return None
    return f"feishu/{parts[1]}.md"


def _add_pair(
    relations: dict[str, dict[str, Relation]],
    source: str,
    target: str,
    detail: str,
) -> None:
    relationship = "直接关联" if "链接" in detail or "飞书" in detail else "精确提及"
    relations[source][target] = Relation(target, relationship, detail)
    reverse_detail = "被页面引用" if "链接" in detail else "反向关联：" + detail
    relations[target][source] = Relation(source, relationship, reverse_detail)


def _freeze_relations(
    relations: dict[str, dict[str, Relation]],
) -> dict[str, tuple[Relation, ...]]:
    return {
        path: tuple(sorted(items.values(), key=lambda item: (item.path, item.detail)))
        for path, items in relations.items()
    }


def _is_page_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        len(parts) >= 3
        and parts[:2] == ("wiki", "pages")
        and path.endswith(".md")
        and all(part not in {"", ".", ".."} for part in parts)
        and "\\" not in path
        and str(PurePosixPath(path)) == path
    )
