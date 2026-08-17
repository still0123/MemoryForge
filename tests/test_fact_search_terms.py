from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.storage.workspace as workspace_module
from memoryforge.interface.cli import app
from memoryforge.storage.workspace import (
    FACT_SEARCH_TERMS_USER_VERSION,
    Workspace,
    reindex_fact_search_terms,
    search_wiki_facts,
)
from tests.cli_helpers import review_approve_apply

_CHINESE_NOTE = "# 缓存设计\n\n缓存键使用版本命名空间，避免读取过期数据。\n"


def test_fact_search_matches_cjk_subphrase(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)

    results = search_wiki_facts(workspace, "存键")

    assert len(results) == 1
    assert "缓存键使用版本命名空间" in results[0].quote


def test_reindex_migrates_legacy_projection_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)
    _downgrade_fact_projection(workspace)

    dry_run = reindex_fact_search_terms(workspace, dry_run=True)
    first = reindex_fact_search_terms(workspace)
    second = reindex_fact_search_terms(workspace)

    assert dry_run["migration_required"] is True
    assert dry_run["user_version"] == 0
    assert first["status"] == "rebuilt"
    assert first["backfilled"] == first["facts"]
    assert first["fts_rows"] == first["facts"]
    assert first["user_version"] == FACT_SEARCH_TERMS_USER_VERSION
    assert first["database_bytes_after"] >= first["database_bytes_before"]
    assert second["status"] == "up_to_date"
    assert second["size_ratio"] == 1.0
    assert search_wiki_facts(workspace, "存键")


def test_reindex_dry_run_does_not_write(tmp_path: Path, monkeypatch) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)
    _downgrade_fact_projection(workspace)
    database = workspace / ".memoryforge/index.sqlite"
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    result = reindex_fact_search_terms(workspace, dry_run=True)

    assert result["status"] == "dry_run"
    assert result["migration_required"] is True
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with sqlite3.connect(database) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(wiki_facts)")}
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    assert "search_terms" not in columns
    assert version == 0


def test_reindex_failure_rolls_back_schema_and_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _applied_workspace(tmp_path, monkeypatch)
    _downgrade_fact_projection(workspace)
    monkeypatch.setattr(
        workspace_module,
        "_WIKI_FACT_FTS_SCHEMA_STATEMENT",
        "CREATE VIRTUAL TABLE invalid SQL",
    )

    with pytest.raises(sqlite3.Error):
        reindex_fact_search_terms(workspace)

    with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(wiki_facts)")}
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    assert "search_terms" not in columns
    assert version == 0


def _applied_workspace(tmp_path: Path, monkeypatch) -> Path:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    source = tmp_path / "cache.md"
    source.write_text(_CHINESE_NOTE, encoding="utf-8")
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["import", str(source), "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0, ingested.output
    applied = review_approve_apply(
        runner,
        json.loads(ingested.stdout)["changeset_id"],
        workspace,
    )
    assert applied.exit_code == 0, applied.output
    return workspace


def _downgrade_fact_projection(workspace: Path) -> None:
    database = Workspace.open_readonly(workspace).index_path
    with sqlite3.connect(database) as connection:
        for trigger in ("wiki_facts_ai", "wiki_facts_ad", "wiki_facts_au"):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("DROP TABLE wiki_fact_fts")
        connection.execute("ALTER TABLE wiki_facts DROP COLUMN search_terms")
        connection.execute(
            """
            CREATE VIRTUAL TABLE wiki_fact_fts USING fts5(
                section_path,
                quote,
                routing_text,
                symbol,
                relation_type,
                content='wiki_facts',
                content_rowid='id',
                tokenize='unicode61'
            )
            """
        )
        connection.execute(
            """
            CREATE TRIGGER wiki_facts_ai AFTER INSERT ON wiki_facts BEGIN
              INSERT INTO wiki_fact_fts(
                rowid, section_path, quote, routing_text, symbol, relation_type
              ) VALUES (
                new.id, new.section_path, new.quote, new.routing_text,
                new.symbol, new.relation_type
              );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER wiki_facts_ad AFTER DELETE ON wiki_facts BEGIN
              INSERT INTO wiki_fact_fts(
                wiki_fact_fts, rowid, section_path, quote, routing_text,
                symbol, relation_type
              ) VALUES (
                'delete', old.id, old.section_path, old.quote, old.routing_text,
                old.symbol, old.relation_type
              );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER wiki_facts_au AFTER UPDATE ON wiki_facts BEGIN
              INSERT INTO wiki_fact_fts(
                wiki_fact_fts, rowid, section_path, quote, routing_text,
                symbol, relation_type
              ) VALUES (
                'delete', old.id, old.section_path, old.quote, old.routing_text,
                old.symbol, old.relation_type
              );
              INSERT INTO wiki_fact_fts(
                rowid, section_path, quote, routing_text, symbol, relation_type
              ) VALUES (
                new.id, new.section_path, new.quote, new.routing_text,
                new.symbol, new.relation_type
              );
            END
            """
        )
        connection.execute("INSERT INTO wiki_fact_fts(wiki_fact_fts) VALUES ('rebuild')")
        connection.execute("PRAGMA user_version = 0")
