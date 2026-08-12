from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.feishu_adapter as feishu_adapter
from memoryforge.cli import app
from memoryforge.feishu_adapter import FeishuDocumentError, refresh_feishu_documents
from memoryforge.workspace import Workspace, list_feishu_documents


def test_feishu_import_fetches_markdown_as_local_only_source(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    captured: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "data": {
                        "document": {
                            "document_id": "doxcn12345678",
                            "revision_id": 7,
                            "content": "# Cache Design\n\nUse a versioned key.",
                        }
                    }
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(feishu_adapter.subprocess, "run", fake_run)
    result = runner.invoke(
        app,
        [
            "feishu-import",
            "https://bytedance.sg.larkoffice.com/docx/doxcn12345678?from=copy",
            "--tag",
            "design",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    imported = json.loads(result.stdout)
    assert imported["status"] == "created"
    source = imported["sources"][0]
    with closing(sqlite3.connect(Workspace.open(workspace).index_path)) as connection, connection:
        stored = connection.execute(
            """
            SELECT s.source_path, v.sensitivity
            FROM sources AS s
            JOIN source_versions AS v ON v.source_id = s.id
            WHERE s.source_id = ? AND v.is_current = 1
            """,
            (source["source_id"],),
        ).fetchone()
    assert stored == ("feishu/doxcn12345678.md", "local_only")
    lark_commands = [
        command for command in captured if command[:3] == ["lark-cli", "docs", "+fetch"]
    ]
    assert lark_commands == [
        [
            "lark-cli",
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--doc",
            "https://bytedance.sg.larkoffice.com/docx/doxcn12345678?from=copy",
            "--doc-format",
            "markdown",
            "--format",
            "json",
        ]
    ]


@pytest.mark.parametrize(
    "reference",
    ["http://example.com/docx/doxcn12345678", "https://example.com/docx/doxcn12345678", "short"],
)
def test_feishu_import_rejects_non_lark_document_references(reference: str) -> None:
    with pytest.raises(FeishuDocumentError, match="document must be"):
        feishu_adapter._validate_document_reference(reference)


def test_feishu_import_hides_fetch_failure_details(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    def failed_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="access token leaked here")

    monkeypatch.setattr(feishu_adapter.subprocess, "run", failed_run)
    result = runner.invoke(
        app,
        ["feishu-import", "doxcn12345678", "--workspace", str(workspace)],
    )

    assert result.exit_code == 1
    assert "Feishu document fetch failed" in result.output
    assert "access token" not in result.output


def test_feishu_title_prefers_markdown_title_element() -> None:
    assert (
        feishu_adapter._document_title(
            {},
            "<title>学习手册</title>\n\n# 零、预备知识",
            "fallback",
        )
        == "学习手册"
    )


def test_feishu_content_drops_lark_wrapper_tags() -> None:
    content = '<title>学习手册</title>\n<callout emoji="📌">\n正文。\n</callout>'

    assert feishu_adapter._document_content(content) == "正文。"


def test_feishu_import_splits_top_level_sections_into_wiki_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    def fake_fetch(_document: str) -> dict[str, object]:
        return {
            "document_id": "doxcn12345678",
            "revision_id": 7,
            "content": (
                "<title>Storage Handbook</title>\n\n"
                "Overview fact.\n\n"
                "# Mounting\n\n"
                "Mount fact.\n\n"
                "## Mount detail\n\n"
                "Mount detail fact.\n\n"
                "# Billing\n\n"
                "Billing fact."
            ),
        }

    monkeypatch.setattr(feishu_adapter, "_fetch_document", fake_fetch)
    imported = runner.invoke(
        app,
        ["feishu-import", "doxcn12345678", "--workspace", str(workspace)],
    )

    assert imported.exit_code == 0, imported.output
    payload = json.loads(imported.stdout)
    assert payload["status"] == "created"
    assert [source["title"] for source in payload["sources"]] == [
        "Storage Handbook",
        "Storage Handbook / Mounting",
        "Storage Handbook / Billing",
    ]

    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])

    assert staged.exit_code == 0, staged.output
    proposal = json.loads(staged.stdout)
    assert len([path for path in proposal["files"] if path != "wiki/INDEX.md"]) == 3


def test_refresh_reimports_registered_feishu_document(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    content = {"value": "# Handbook\n\nFirst version."}
    references: list[str] = []

    def fake_fetch(reference: str) -> dict[str, object]:
        references.append(reference)
        return {
            "document_id": "doxcn12345678",
            "content": content["value"],
        }

    monkeypatch.setattr(feishu_adapter, "_fetch_document", fake_fetch)
    imported = runner.invoke(
        app,
        [
            "feishu-import",
            "doxcn12345678",
            "--category",
            "design",
            "--tag",
            "handbook",
            "--workspace",
            str(workspace),
        ],
    )
    assert imported.exit_code == 0, imported.output
    registered = [
        (item.document_id, item.category.value, item.tags)
        for item in list_feishu_documents(workspace)
    ]
    assert registered == [("doxcn12345678", "design", ("handbook",))]

    content["value"] = "# Handbook\n\nSecond version."
    refreshed = refresh_feishu_documents(workspace)

    assert references == ["doxcn12345678", "doxcn12345678"]
    assert refreshed[0].document_id == "doxcn12345678"
    assert refreshed[0].updated == 1
    assert refreshed[0].created == 0
    assert refreshed[0].deleted == 0


def test_feishu_section_ids_survive_insert_and_reconcile_deleted_sections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    content = {
        "value": (
            "<title>Storage Handbook</title>\n\n"
            "Overview fact.\n\n"
            "# Mounting\n\nMount fact.\n\n"
            "# Billing\n\nBilling fact."
        )
    }

    monkeypatch.setattr(
        feishu_adapter,
        "_fetch_document",
        lambda _reference: {
            "document_id": "doxcn12345678",
            "content": content["value"],
        },
    )
    imported = runner.invoke(
        app,
        ["feishu-import", "doxcn12345678", "--workspace", str(workspace)],
    )
    assert imported.exit_code == 0, imported.output
    initial = _current_feishu_sources(workspace)

    content["value"] = (
        "<title>Storage Handbook</title>\n\n"
        "Overview fact.\n\n"
        "# Setup\n\nSetup fact.\n\n"
        "# Mounting\n\nMount fact.\n\n"
        "# Billing\n\nBilling fact."
    )
    inserted = refresh_feishu_documents(workspace)[0]
    after_insert = _current_feishu_sources(workspace)

    assert inserted.created == 1
    assert inserted.deleted == 0
    assert after_insert["Storage Handbook / Mounting"] == initial["Storage Handbook / Mounting"]
    assert after_insert["Storage Handbook / Billing"] == initial["Storage Handbook / Billing"]

    content["value"] = (
        "<title>Storage Handbook</title>\n\n"
        "Overview fact.\n\n"
        "# Setup\n\nSetup fact.\n\n"
        "# Mounting\n\nMount fact."
    )
    removed = refresh_feishu_documents(workspace)[0]

    assert removed.deleted == 1
    assert "Storage Handbook / Billing" not in _current_feishu_sources(workspace)


def test_refresh_registers_a_preexisting_feishu_source(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    monkeypatch.setattr(
        feishu_adapter,
        "_fetch_document",
        lambda _reference: {
            "document_id": "doxcn12345678",
            "content": "# Handbook\n\nExisting version.",
        },
    )
    assert (
        runner.invoke(
            app,
            ["feishu-import", "doxcn12345678", "--workspace", str(workspace)],
        ).exit_code
        == 0
    )
    with closing(sqlite3.connect(Workspace.open(workspace).index_path)) as connection, connection:
        connection.execute("DELETE FROM feishu_documents")

    registered = list_feishu_documents(workspace)

    assert [(item.document_id, item.category.value, item.tags) for item in registered] == [
        ("doxcn12345678", "notes", ())
    ]


def _current_feishu_sources(workspace: Path) -> dict[str, str]:
    with closing(sqlite3.connect(Workspace.open(workspace).index_path)) as connection:
        rows = connection.execute(
            """
            SELECT versions.title, sources.source_id
            FROM sources
            JOIN source_versions AS versions
              ON versions.source_id = sources.id AND versions.is_current = 1
            WHERE sources.source_path LIKE 'feishu/%'
            ORDER BY versions.title
            """
        ).fetchall()
    return {str(title): str(source_id) for title, source_id in rows}
