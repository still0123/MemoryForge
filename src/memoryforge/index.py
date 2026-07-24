"""SQLite FTS5 index for the immutable Raw source layer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memoryforge.models import SourceDocument


class SourceIndex:
    """Owns the compact, rebuildable keyword index for imported sources."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        """Create the metadata and FTS5 tables if the index is new."""

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_documents (
                    source_id TEXT PRIMARY KEY,
                    uri TEXT NOT NULL UNIQUE,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    media_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    observed_at TEXT,
                    sensitivity TEXT NOT NULL,
                    tags_json TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
                    source_id UNINDEXED,
                    title,
                    body
                );
                """
            )

    def add(self, source: SourceDocument, content: str) -> None:
        """Index one imported source once; callers enforce manifest idempotency."""

        title = Path(source.uri).name
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_documents (
                    source_id, uri, content_sha256, media_type, category,
                    imported_at, observed_at, sensitivity, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.uri,
                    source.content_sha256,
                    source.media_type,
                    source.category.value,
                    source.imported_at.isoformat(),
                    source.observed_at.isoformat() if source.observed_at else None,
                    source.sensitivity.value,
                    _json_tags(source.tags),
                ),
            )
            connection.execute(
                """
                INSERT INTO source_fts (source_id, title, body)
                SELECT ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM source_fts WHERE source_id = ?
                )
                """,
                (source.source_id, title, content, source.source_id),
            )

    def search(self, query: str, limit: int = 10) -> list[str]:
        """Return matching source IDs in BM25 rank order."""

        if not query.strip():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id
                FROM source_fts
                WHERE source_fts MATCH ?
                ORDER BY bm25(source_fts)
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        return [str(row["source_id"]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _json_tags(tags: tuple[str, ...]) -> str:
    """Serialize tags with the standard-library JSON encoder."""

    return json.dumps(tags, ensure_ascii=True)
