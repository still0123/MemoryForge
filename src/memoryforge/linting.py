"""Read-only structural checks for generated Wiki pages."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
import stat
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from memoryforge.wiki_facts import (
    citation_quote_matches_excerpt,
    parse_page_citations,
    parse_page_facts,
)
from memoryforge.workspace import (
    WorkspaceIntegrityError,
    WorkspaceSecurityError,
    _blob_relative_path,
    is_generated_navigation_page,
)

_INDEX_ENTRY = re.compile(
    r"^- \[(?:\\.|[^\]])+\]\((?P<path>[^)]+)\) — .+$",
    re.MULTILINE,
)
_RELATED_PAGE_LINK = re.compile(r"^- \[[^\]]+\]\((?P<path>[^)]+\.md)\)$", re.MULTILINE)
_PAGE_TYPES = {"entity", "concept", "synthesis"}


class LintIssue(TypedDict):
    code: str
    path: str
    message: str


class LintPayload(TypedDict):
    status: Literal["clean", "issues"]
    checked_pages: int
    issues: list[LintIssue]


def lint_workspace(
    workspace_root: Path,
    *,
    evidence_root: Path | None = None,
    require_navigation: bool = True,
) -> LintPayload:
    """Check generated pages, citations, and INDEX.md without writing anything."""
    workspace_root = workspace_root.absolute()
    evidence_root = (evidence_root or workspace_root).absolute()
    wiki_root = workspace_root / "wiki"
    pages_root = wiki_root / "pages"
    issues: list[LintIssue] = []
    if wiki_root.is_symlink() or not wiki_root.is_dir():
        issues.append(
            _issue(
                "invalid_wiki_path",
                "wiki",
                "wiki must be a real directory inside the workspace",
            )
        )
        return {"status": "issues", "checked_pages": 0, "issues": issues}
    if pages_root.is_symlink() or not pages_root.is_dir():
        issues.append(
            _issue(
                "invalid_pages_path",
                "wiki/pages",
                "pages must be a real directory inside wiki",
            )
        )
        return {"status": "issues", "checked_pages": 0, "issues": issues}
    indexed_paths = _indexed_page_paths(workspace_root, issues) if require_navigation else set()
    if issues:
        return {"status": "issues", "checked_pages": 0, "issues": issues}

    try:
        index = _open_readonly_index(workspace_root)
    except (OSError, sqlite3.Error):
        issues.append(
            _issue(
                "invalid_workspace_index",
                ".memoryforge/index.sqlite",
                "workspace index is unavailable for read-only linting",
            )
        )
        return {"status": "issues", "checked_pages": 0, "issues": issues}

    pages = sorted(
        path for path in pages_root.rglob("*.md") if path.is_file() and not path.is_symlink()
    )
    for path in sorted(path for path in pages_root.rglob("*.md") if path.is_symlink()):
        issues.append(
            _issue(
                "invalid_page_path",
                str(path.relative_to(workspace_root)),
                "page must be a real Markdown file inside wiki/pages",
            )
        )
    page_paths = {str(path.relative_to(workspace_root)) for path in pages}
    _lint_fact_index(index, page_paths, issues)
    linked_paths: set[str] = set()
    for path in pages:
        relative_path = str(path.relative_to(workspace_root))
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        linked_paths.update(
            target for target in _related_page_paths(relative_path, content) if target in page_paths
        )

    try:
        for path in pages:
            relative_path = str(path.relative_to(workspace_root))
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                issues.append(
                    _issue(
                        "unreadable_page",
                        relative_path,
                        "page cannot be read as UTF-8",
                    )
                )
                continue
            _lint_page_facts(index, relative_path, content, issues)
            citation_source_ids = _page_citation_source_ids(content)
            if citation_source_ids is None:
                issues.append(
                    _issue(
                        "invalid_frontmatter",
                        relative_path,
                        "citation_sources must contain unique source IDs",
                    )
                )
            if is_generated_navigation_page(content):
                if citation_source_ids:
                    _lint_source_freshness(
                        index,
                        relative_path,
                        citation_source_ids,
                        issues,
                    )
                    _lint_citations(
                        evidence_root,
                        index,
                        relative_path,
                        content,
                        set(citation_source_ids),
                        issues,
                    )
                _lint_related_page_links(relative_path, content, page_paths, issues)
                if require_navigation and relative_path not in indexed_paths:
                    issues.append(
                        _issue(
                            "missing_index_entry",
                            relative_path,
                            "page is absent from wiki/INDEX.md",
                        )
                    )
                continue
            source_ids = _page_source_ids(content)
            if source_ids is None:
                issues.append(
                    _issue(
                        "invalid_frontmatter",
                        relative_path,
                        "page must declare title, type, summary, and a non-empty sources list",
                    )
                )
            else:
                expected_source_ids = _source_ids_for_page(index, relative_path)
                if tuple(sorted(source_ids)) != expected_source_ids:
                    issues.append(
                        _issue(
                            "source_mapping_mismatch",
                            relative_path,
                            "frontmatter sources do not match the applied source-to-page mapping",
                        )
                    )
                else:
                    _lint_source_freshness(index, relative_path, source_ids, issues)
                _lint_citations(
                    evidence_root,
                    index,
                    relative_path,
                    content,
                    set(source_ids) | set(citation_source_ids or ()),
                    issues,
                )
            _lint_related_page_links(relative_path, content, page_paths, issues)
            if require_navigation and relative_path not in indexed_paths:
                issues.append(
                    _issue(
                        "missing_index_entry",
                        relative_path,
                        "page is absent from wiki/INDEX.md",
                    )
                )
                if relative_path not in linked_paths:
                    issues.append(
                        _issue(
                            "orphan_page",
                            relative_path,
                            "page is neither indexed nor linked by another page",
                        )
                    )
    except sqlite3.Error:
        issues.append(
            _issue(
                "invalid_workspace_index",
                ".memoryforge/index.sqlite",
                "workspace index is unavailable for read-only linting",
            )
        )
    finally:
        index.close()

    if require_navigation:
        for indexed_path in sorted(indexed_paths - page_paths):
            issues.append(
                _issue(
                    "index_missing_page",
                    "wiki/INDEX.md",
                    f"linked page does not exist: {indexed_path}",
                )
            )

    issues.sort(key=lambda issue: (issue["path"], issue["code"], issue["message"]))
    return {
        "status": "clean" if not issues else "issues",
        "checked_pages": len(pages),
        "issues": issues,
    }


def _indexed_page_paths(workspace_root: Path, issues: list[LintIssue]) -> set[str]:
    index = workspace_root / "wiki" / "INDEX.md"
    if index.is_symlink():
        issues.append(
            _issue(
                "invalid_index_path",
                "wiki/INDEX.md",
                "INDEX.md must be a real file inside wiki",
            )
        )
        return set()
    if not index.is_file():
        issues.append(_issue("missing_index", "wiki/INDEX.md", "INDEX.md does not exist"))
        return set()
    try:
        content = index.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        issues.append(
            _issue(
                "unreadable_index",
                "wiki/INDEX.md",
                "INDEX.md cannot be read as UTF-8",
            )
        )
        return set()

    indexed_paths: set[str] = set()
    for entry in _INDEX_ENTRY.finditer(content):
        link = Path(entry.group("path"))
        if link.is_absolute() or ".." in link.parts or link.parts[:1] != ("pages",):
            issues.append(
                _issue(
                    "invalid_index_link",
                    "wiki/INDEX.md",
                    f"invalid page link: {entry.group('path')}",
                )
            )
            continue
        indexed_paths.add(str(Path("wiki") / link))
    return indexed_paths


def _page_source_ids(content: str) -> tuple[str, ...] | None:
    fields = _page_frontmatter_fields(content)
    if fields is None:
        return None
    if (
        not fields.get("title")
        or fields.get("type") not in _PAGE_TYPES
        or not fields.get("summary")
    ):
        return None
    return _decode_source_ids(fields.get("sources"))


def _page_citation_source_ids(content: str) -> tuple[str, ...] | None:
    fields = _page_frontmatter_fields(content)
    if fields is None:
        return None
    if "citation_sources" not in fields:
        return ()
    return _decode_source_ids(fields["citation_sources"])


def _page_frontmatter_fields(content: str) -> dict[str, str] | None:
    if not content.startswith("---\n"):
        return None
    closing = content.find("\n---\n", len("---\n"))
    if closing < 0:
        return None
    return {
        key.strip(): value.strip()
        for line in content[len("---\n") : closing].splitlines()
        for key, separator, value in (line.partition(":"),)
        if separator
    }


def _decode_source_ids(raw_source_ids: str | None) -> tuple[str, ...] | None:
    if raw_source_ids is None:
        return None
    try:
        source_ids = json.loads(raw_source_ids)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or not all(
            isinstance(source_id, str) and re.fullmatch(r"[a-f0-9]{64}", source_id)
            for source_id in source_ids
        )
    ):
        return None
    if len(source_ids) != len(set(source_ids)):
        return None
    return tuple(source_ids)


def _lint_citations(
    workspace_root: Path,
    index: sqlite3.Connection,
    page_path: str,
    content: str,
    source_ids: set[str],
    issues: list[LintIssue],
) -> None:
    citations = parse_page_citations(content)
    if not citations:
        issues.append(_issue("missing_citation", page_path, "page has no verifiable citation"))
    represented_source_ids: set[str] = set()
    for citation in citations:
        source_id = citation["source_id"]
        if source_id not in source_ids:
            issues.append(
                _issue(
                    "citation_source_not_owned",
                    page_path,
                    f"citation source is not declared by the page: {source_id}",
                )
            )
            continue
        represented_source_ids.add(source_id)
        try:
            _validate_citation_excerpt(
                workspace_root,
                index,
                source_id=source_id,
                source_version=citation["source_version"],
                locator=citation["locator"],
                quote=citation["quote"],
                grounding=citation.get("grounding", "exact"),
            )
        except (
            OSError,
            ValueError,
            WorkspaceIntegrityError,
            WorkspaceSecurityError,
            sqlite3.Error,
        ):
            issues.append(
                _issue(
                    "invalid_citation",
                    page_path,
                    f"citation cannot be expanded: {source_id}",
                )
            )
    for source_id in sorted(source_ids - represented_source_ids):
        issues.append(
            _issue(
                "missing_source_citation",
                page_path,
                f"page has no citation for declared source: {source_id}",
            )
        )


def _lint_source_freshness(
    index: sqlite3.Connection,
    page_path: str,
    source_ids: tuple[str, ...],
    issues: list[LintIssue],
) -> None:
    """Report pages whose declared sources are deleted or newer than their Wiki copy."""
    for source_id in source_ids:
        row = index.execute(
            """
            SELECT current_version.id AS current_version, applied.source_version_id
            FROM sources AS s
            LEFT JOIN source_versions AS current_version
              ON current_version.source_id = s.id AND current_version.is_current = 1
            LEFT JOIN applied_source_versions AS applied ON applied.source_id = s.source_id
            WHERE s.source_id = ?
            """,
            (source_id,),
        ).fetchone()
        if row is None or row["current_version"] is None:
            issues.append(
                _issue(
                    "cleanup_required",
                    page_path,
                    f"declared source no longer has a current version: {source_id}",
                )
            )
        elif row["current_version"] != row["source_version_id"]:
            issues.append(
                _issue(
                    "source_needs_recompile",
                    page_path,
                    f"declared source has a newer version: {source_id}",
                )
            )


def _lint_related_page_links(
    page_path: str,
    content: str,
    page_paths: set[str],
    issues: list[LintIssue],
) -> None:
    for match in _RELATED_PAGE_LINK.finditer(content):
        target = match.group("path")
        resolved = _resolve_related_page_path(page_path, target)
        if resolved is None:
            issues.append(
                _issue(
                    "invalid_related_link",
                    page_path,
                    f"related page link must stay below wiki/pages: {target}",
                )
            )
        elif resolved not in page_paths:
            issues.append(
                _issue(
                    "related_page_missing",
                    page_path,
                    f"related page does not exist: {resolved}",
                )
            )


def _related_page_paths(page_path: str, content: str) -> tuple[str, ...]:
    """Return valid relative Wiki page targets for orphan detection."""
    return tuple(
        resolved
        for match in _RELATED_PAGE_LINK.finditer(content)
        for target in (match.group("path"),)
        for resolved in (_resolve_related_page_path(page_path, target),)
        if resolved is not None
    )


def _resolve_related_page_path(page_path: str, target: str) -> str | None:
    if "\\" in target or target.startswith("/"):
        return None
    parent = PurePosixPath(page_path).parent
    normalized = PurePosixPath(posixpath.normpath((parent / target).as_posix()))
    if (
        normalized.parts[:2] != ("wiki", "pages")
        or len(normalized.parts) < 3
        or normalized.suffix != ".md"
        or any(part in {"", ".", ".."} for part in normalized.parts)
    ):
        return None
    return normalized.as_posix()


def _open_readonly_index(workspace_root: Path) -> sqlite3.Connection:
    internal_root = workspace_root / ".memoryforge"
    index_path = internal_root / "index.sqlite"
    if (
        internal_root.is_symlink()
        or not internal_root.is_dir()
        or index_path.is_symlink()
        or not index_path.is_file()
    ):
        raise OSError("workspace index is unavailable")
    connection = sqlite3.connect(f"{index_path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT 1 FROM page_sources LIMIT 1").fetchone()
        connection.execute("SELECT 1 FROM sources LIMIT 1").fetchone()
        connection.execute("SELECT 1 FROM wiki_facts LIMIT 1").fetchone()
        connection.execute("SELECT 1 FROM wiki_fact_fts LIMIT 1").fetchone()
    except Exception:
        connection.close()
        raise
    return connection


def _source_ids_for_page(index: sqlite3.Connection, page_path: str) -> tuple[str, ...]:
    rows = index.execute(
        """
        SELECT source_id
        FROM page_sources
        WHERE page_path = ?
        ORDER BY source_id
        """,
        (page_path,),
    ).fetchall()
    return tuple(str(row["source_id"]) for row in rows)


def _lint_fact_index(
    index: sqlite3.Connection,
    page_paths: set[str],
    issues: list[LintIssue],
) -> None:
    fact_count = int(index.execute("SELECT COUNT(*) FROM wiki_facts").fetchone()[0])
    fts_count = int(index.execute("SELECT COUNT(*) FROM wiki_fact_fts_docsize").fetchone()[0])
    if fact_count != fts_count:
        issues.append(
            _issue(
                "fact_index_count_mismatch",
                ".memoryforge/index.sqlite",
                "Wiki fact rows and FTS rows have different counts",
            )
        )
    indexed_pages = {
        str(row[0]) for row in index.execute("SELECT DISTINCT page_path FROM wiki_facts").fetchall()
    }
    for page_path in sorted(indexed_pages - page_paths):
        issues.append(
            _issue(
                "fact_index_missing_page",
                page_path,
                "Wiki fact index references a missing page",
            )
        )


def _lint_page_facts(
    index: sqlite3.Connection,
    page_path: str,
    content: str,
    issues: list[LintIssue],
) -> None:
    expected = {fact.fact_id for fact in parse_page_facts(page_path, content)}
    actual = {
        str(row[0])
        for row in index.execute(
            "SELECT fact_id FROM wiki_facts WHERE page_path = ?",
            (page_path,),
        ).fetchall()
    }
    if expected != actual:
        issues.append(
            _issue(
                "fact_index_page_mismatch",
                page_path,
                "Wiki page facts do not match the applied fact index",
            )
        )


def _validate_citation_excerpt(
    workspace_root: Path,
    index: sqlite3.Connection,
    *,
    source_id: str,
    source_version: int,
    locator: str,
    quote: str,
    grounding: Literal["exact", "semantic"],
) -> None:
    locator_match = re.fullmatch(r"chars:(?P<start>\d+)-(?P<end>\d+)", locator)
    if locator_match is None:
        raise WorkspaceIntegrityError("Citation locator is invalid")
    row = index.execute(
        """
        SELECT b.content_sha256, b.snapshot_path
        FROM sources AS s
        JOIN source_versions AS v ON v.source_id = s.id
        JOIN blobs AS b ON b.id = v.blob_id
        WHERE s.source_id = ? AND v.id = ?
        """,
        (source_id, source_version),
    ).fetchone()
    if row is None:
        raise WorkspaceIntegrityError("Citation does not identify an imported SourceVersion")
    evidence = _read_blob_bytes_readonly(
        workspace_root,
        str(row["content_sha256"]),
        Path(str(row["snapshot_path"])),
    )
    try:
        text = evidence.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceIntegrityError("Citation evidence is not valid UTF-8") from exc
    start = int(locator_match.group("start"))
    end = int(locator_match.group("end"))
    if start >= end or end > len(text):
        raise WorkspaceIntegrityError("Citation locator is outside immutable evidence")
    if grounding == "exact" and not citation_quote_matches_excerpt(quote, text[start:end]):
        raise WorkspaceIntegrityError("Citation quote does not match immutable evidence")


def _read_blob_bytes_readonly(root: Path, content_sha256: str, relative: Path) -> bytes:
    expected_relative = _blob_relative_path(content_sha256)
    if relative != expected_relative:
        raise WorkspaceIntegrityError("blob integrity metadata is inconsistent")

    path = root
    try:
        root_stat = path.lstat()
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise WorkspaceSecurityError("workspace root must be a real directory")
        for index, component in enumerate(expected_relative.parts):
            path = path / component
            path_stat = path.lstat()
            if stat.S_ISLNK(path_stat.st_mode):
                raise WorkspaceSecurityError("immutable blob path cannot contain symlinks")
            if index < len(expected_relative.parts) - 1:
                if not stat.S_ISDIR(path_stat.st_mode):
                    raise WorkspaceSecurityError("immutable blob path must contain directories")
            elif not stat.S_ISREG(path_stat.st_mode):
                raise WorkspaceSecurityError("immutable blob must be a regular file")
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise WorkspaceIntegrityError("blob integrity check failed: evidence is missing") from exc
    except OSError as exc:
        raise WorkspaceSecurityError("read-only blob verification failed") from exc
    if hashlib.sha256(content).hexdigest() != content_sha256:
        raise WorkspaceIntegrityError("blob integrity check failed: digest mismatch")
    return content


def _issue(code: str, path: str, message: str) -> LintIssue:
    return {"code": code, "path": path, "message": message}
