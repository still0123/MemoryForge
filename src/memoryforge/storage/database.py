"""SQLite connection helpers shared by storage components."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def connect_readonly(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(
        database_path.as_uri() + "?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
    finally:
        connection.close()
