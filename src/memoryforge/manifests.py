"""Append-only manifest storage for immutable imported source records."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from memoryforge.errors import WorkspaceError
from memoryforge.models import SourceDocument


class SourceManifestStore:
    """Persists one immutable Pydantic record per imported source."""

    def __init__(self, manifest_dir: Path) -> None:
        self.manifest_dir = manifest_dir

    def find_by_hash(self, content_sha256: str) -> Optional[SourceDocument]:
        """Find the already imported source with the same full content hash."""

        for source in self.list_all():
            if source.content_sha256 == content_sha256:
                return source
        return None

    def list_all(self) -> list[SourceDocument]:
        """Return deterministic source manifests, ordered by source ID."""

        if not self.manifest_dir.exists():
            return []
        return [
            SourceDocument.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.manifest_dir.glob("src_*.json"))
        ]

    def write(self, source: SourceDocument) -> Path:
        """Persist a new manifest once and reject any attempt to replace it."""

        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        destination = self.manifest_dir / f"{source.source_id}.json"
        if destination.exists():
            existing = SourceDocument.model_validate_json(destination.read_text(encoding="utf-8"))
            if existing == source:
                return destination
            raise WorkspaceError(f"Refusing to replace immutable manifest: {destination}")

        payload = source.model_dump_json(indent=2) + "\n"
        _write_atomically(destination, payload)
        return destination


def _write_atomically(destination: Path, content: str) -> None:
    """Write a single manifest by atomically renaming a file in its directory."""

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
