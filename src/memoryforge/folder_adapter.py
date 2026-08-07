"""Recursively import one local folder through the normal SourceVersion pipeline."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from memoryforge.errors import MemoryForgeError
from memoryforge.importer import (
    _canonical_relative_source_path,
    _extract_title,
    _is_ignored,
    _memoryforgeignore_rules,
    import_local_document,
    read_local_text_file,
    validate_source_path,
)
from memoryforge.models import (
    FolderDocumentSyncResult,
    FolderSyncResult,
    LocalDocument,
    Sensitivity,
    SourceCategory,
)
from memoryforge.web_adapter import FetchedWebPage, _readable_page
from memoryforge.workspace import (
    Workspace,
    reconcile_folder_sources,
    record_folder_source_version,
    register_folder_import,
)

_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_SUPPORTED_SUFFIXES = _TEXT_SUFFIXES | _HTML_SUFFIXES


class FolderImportError(MemoryForgeError):
    """Raised when a recursive local folder cannot be imported safely."""


@dataclass(frozen=True)
class _ScannedDocument:
    relative_path: str
    source_id: str
    document: LocalDocument


def sync_folder(
    workspace: Path,
    folder: Path,
    *,
    category: str = "refs",
    tags: tuple[str, ...] = (),
    sensitivity: Sensitivity = Sensitivity.LOCAL_ONLY,
) -> FolderSyncResult:
    """Import one deterministic folder snapshot and deactivate deleted members."""
    opened = Workspace.open(workspace)
    source_root = _validate_folder_root(folder, workspace_root=opened.root)
    try:
        normalized_category = SourceCategory(category)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceCategory)
        raise FolderImportError(f"category must be one of: {allowed}") from exc
    folder_id = hashlib.sha256(source_root.as_posix().encode("utf-8")).hexdigest()
    rules = _memoryforgeignore_rules(source_root)
    scanned = _scan_folder(
        source_root,
        folder_id=folder_id,
        rules=rules,
        category=normalized_category,
        tags=tags,
        sensitivity=sensitivity,
    )

    with opened.exclusive_lock():
        register_folder_import(opened, folder_id)
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        documents = []
        for item in scanned:
            imported = import_local_document(
                opened.root,
                item.document,
                source_id=item.source_id,
            )
            record_folder_source_version(
                opened,
                folder_id=folder_id,
                source_id=imported.source_id,
                relative_path=item.relative_path,
            )
            counts[imported.status] += 1
            documents.append(
                FolderDocumentSyncResult(
                    source_id=imported.source_id,
                    relative_path=item.relative_path,
                    status=imported.status,
                )
            )
        deleted = reconcile_folder_sources(
            opened,
            folder_id=folder_id,
            current_paths={item.relative_path for item in scanned},
        )
    return FolderSyncResult(
        folder_id=folder_id,
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        deleted=deleted,
        documents=tuple(documents),
    )


def _validate_folder_root(folder: Path, *, workspace_root: Path) -> Path:
    candidate = folder.expanduser()
    if candidate.is_symlink():
        raise FolderImportError("folder root must not be a symbolic link")
    try:
        root = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FolderImportError("folder root could not be resolved safely") from exc
    if not root.is_dir():
        raise FolderImportError("folder root must be an existing directory")
    resolved_workspace = workspace_root.resolve()
    if resolved_workspace == root or resolved_workspace.is_relative_to(root):
        raise FolderImportError("MemoryForge workspace must be outside the imported folder")
    return root


def _scan_folder(
    source_root: Path,
    *,
    folder_id: str,
    rules: tuple[str, ...],
    category: SourceCategory,
    tags: tuple[str, ...],
    sensitivity: Sensitivity,
) -> tuple[_ScannedDocument, ...]:
    scanned = []

    def fail_walk(_error: OSError) -> None:
        raise FolderImportError("folder could not be traversed safely")

    for current, dirnames, filenames in os.walk(
        source_root,
        topdown=True,
        onerror=fail_walk,
        followlinks=False,
    ):
        current_path = Path(current)
        kept_directories = []
        for name in sorted(dirnames):
            child = current_path / name
            relative = child.relative_to(source_root).as_posix()
            if (
                name.startswith(".")
                or child.is_symlink()
                or _is_ignored(
                    source_root,
                    f"{relative}/__memoryforge_folder_probe__",
                    rules=rules,
                )
            ):
                continue
            kept_directories.append(name)
        dirnames[:] = kept_directories

        for name in sorted(filenames):
            if name.startswith("."):
                continue
            source_path = current_path / name
            suffix = source_path.suffix.lower()
            if suffix not in _SUPPORTED_SUFFIXES or source_path.is_symlink():
                continue
            resolved = validate_source_path(
                source_path,
                source_root=source_root,
                allowed_suffixes=_SUPPORTED_SUFFIXES,
            )
            _filesystem_path, relative_path = _canonical_relative_source_path(
                source_root,
                resolved,
            )
            if _is_ignored(source_root, relative_path, rules=rules):
                continue
            content = read_local_text_file(
                resolved,
                source_root=source_root,
                allowed_suffixes=_SUPPORTED_SUFFIXES,
            )
            source_id = hashlib.sha256(f"local:{folder_id}:{relative_path}".encode()).hexdigest()
            document = _folder_document(
                relative_path,
                content,
                source_id=source_id,
                category=category,
                tags=tags,
                sensitivity=sensitivity,
            )
            scanned.append(
                _ScannedDocument(
                    relative_path=relative_path,
                    source_id=source_id,
                    document=document,
                )
            )
    return tuple(sorted(scanned, key=lambda item: item.relative_path))


def _folder_document(
    relative_path: str,
    content: str,
    *,
    source_id: str,
    category: SourceCategory,
    tags: tuple[str, ...],
    sensitivity: Sensitivity,
) -> LocalDocument:
    path = PurePosixPath(relative_path)
    suffix = path.suffix.lower()
    media_type: Literal["text/markdown", "text/plain"]
    document_suffix: Literal[".md", ".markdown", ".txt"]
    if suffix in _HTML_SUFFIXES:
        title, body = _readable_page(
            FetchedWebPage(
                url=relative_path,
                media_type="text/html",
                content=content,
            )
        )
        if title == "Web page":
            title = path.stem
        document_content = f"# {title}\n\n{body}\n"
        media_type = "text/markdown"
        document_suffix = ".md"
    else:
        title = _extract_title(content, path.stem)
        document_content = content
        media_type = "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
        document_suffix = cast(Literal[".md", ".markdown", ".txt"], suffix)
    parent = path.parent.as_posix()
    normalized_tags = tuple(
        sorted(
            {
                "folder",
                f"folder-path:{parent}",
                *(tag.strip() for tag in tags if tag.strip()),
            }
        )
    )
    return LocalDocument(
        source_uri=f"mf://source/{source_id}",
        source_path=relative_path,
        media_type=media_type,
        category=category,
        suffix=document_suffix,
        title=title,
        content=document_content,
        sensitivity=sensitivity,
        tags=normalized_tags,
    )
