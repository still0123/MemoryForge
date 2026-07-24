"""Safe, idempotent import of text sources into a workspace's Raw layer."""

from __future__ import annotations

import fnmatch
import hashlib
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from memoryforge.errors import ImportSafetyError, UnsupportedSourceError, WorkspaceError
from memoryforge.manifests import SourceManifestStore
from memoryforge.models import Sensitivity, SourceCategory, SourceDocument
from memoryforge.workspace import Workspace

MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}
SENSITIVE_FILE_NAMES = {
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
BINARY_DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
SENSITIVE_DIRECTORIES = {".aws", ".git", ".ssh", "credentials", "secrets"}


@dataclass(frozen=True)
class ImportResult:
    """Reports whether a source was newly copied or deduplicated."""

    source: SourceDocument
    duplicate: bool


class SourceImporter:
    """Imports Markdown and plain text without allowing raw content mutation."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.manifests = SourceManifestStore(workspace.manifest_dir)

    def import_file(
        self,
        source_path: Path,
        category: SourceCategory,
        tags: tuple[str, ...] = (),
        sensitivity: Sensitivity = Sensitivity.PUBLIC,
    ) -> ImportResult:
        """Copy a new text source, record its identity, and index it exactly once."""

        source_path = source_path.expanduser().resolve()
        self._validate_path(source_path)
        media_type = MEDIA_TYPES[source_path.suffix.lower()]
        content_bytes = source_path.read_bytes()
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UnsupportedSourceError(
                f"Only UTF-8 Markdown and text sources are supported: {source_path}"
            ) from error

        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        existing = self.manifests.find_by_hash(content_sha256)
        if existing is not None:
            return ImportResult(source=existing, duplicate=True)

        source_id = f"src_{content_sha256[:16]}"
        destination = self.workspace.raw_dir / category.value / f"{source_id}--{source_path.name}"
        if destination.exists():
            raise WorkspaceError(
                f"Raw source path already exists without a matching manifest: {destination}"
            )

        observed_at = datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc)
        normalized_tags = tuple(sorted({tag.strip() for tag in tags if tag.strip()}))
        document = SourceDocument(
            source_id=source_id,
            uri=destination.relative_to(self.workspace.root).as_posix(),
            content_sha256=content_sha256,
            media_type=media_type,
            category=category,
            imported_at=datetime.now(timezone.utc),
            observed_at=observed_at,
            sensitivity=sensitivity,
            tags=normalized_tags,
        )

        destination.write_bytes(content_bytes)
        destination.chmod(stat.S_IRUSR)
        try:
            self.manifests.write(document)
        except Exception:
            destination.chmod(stat.S_IWUSR | stat.S_IRUSR)
            destination.unlink(missing_ok=True)
            raise

        self.workspace.source_index.initialize()
        self.workspace.source_index.add(document, content)
        return ImportResult(source=document, duplicate=False)

    def _validate_path(self, source_path: Path) -> None:
        """Apply the MVP text-only and credential-exclusion boundary."""

        if not source_path.is_file():
            raise UnsupportedSourceError(f"Source must be a readable file: {source_path}")
        if source_path.name.lower() in SENSITIVE_FILE_NAMES:
            raise ImportSafetyError(f"Refusing to import sensitive file: {source_path.name}")
        if source_path.suffix.lower() in SENSITIVE_SUFFIXES:
            raise ImportSafetyError(f"Refusing to import sensitive file: {source_path.name}")
        if source_path.suffix.lower() in BINARY_DATABASE_SUFFIXES:
            raise ImportSafetyError(f"Refusing to import binary database: {source_path.name}")
        if any(part.lower() in SENSITIVE_DIRECTORIES for part in source_path.parts):
            raise ImportSafetyError(f"Refusing to import from sensitive directory: {source_path}")
        if self._matches_ignore(source_path):
            raise ImportSafetyError(f"Source is excluded by .memoryforgeignore: {source_path}")
        if source_path.suffix.lower() not in MEDIA_TYPES:
            extensions = ", ".join(sorted(MEDIA_TYPES))
            raise UnsupportedSourceError(f"Only {extensions} sources are supported: {source_path}")

    def _matches_ignore(self, source_path: Path) -> bool:
        """Evaluate a small, predictable subset of gitignore-style patterns."""

        ignore_path = self.workspace.root / ".memoryforgeignore"
        if not ignore_path.is_file():
            return False
        candidate = source_path.as_posix()
        filename = source_path.name
        for raw_pattern in ignore_path.read_text(encoding="utf-8").splitlines():
            pattern = raw_pattern.strip()
            if not pattern or pattern.startswith("#"):
                continue
            if pattern.endswith("/"):
                directory = pattern.rstrip("/").lower()
                if any(part.lower() == directory for part in source_path.parts):
                    return True
                continue
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(candidate, pattern):
                return True
        return False
