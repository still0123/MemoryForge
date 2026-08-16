from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import memoryforge.compiler.linting as linting_module
import memoryforge.storage.blob_store as blob_store_module
from memoryforge.compiler.linting import lint_workspace
from memoryforge.interface.cli import app
from memoryforge.storage.workspace import Workspace
from tests.cli_helpers import review_approve_apply


def test_lint_reports_clean_applied_wiki(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"status": "clean", "checked_pages": 1, "issues": []}


def test_lint_reuses_immutable_evidence_for_repeated_citations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    citation = linting_module.parse_page_citations(page.read_text(encoding="utf-8"))[0]
    index = linting_module._open_readonly_index(workspace)
    original_read = linting_module._read_blob_bytes_readonly
    reads = 0

    def counted_read(*args: Any, **kwargs: Any) -> bytes:
        nonlocal reads
        reads += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(linting_module, "_read_blob_bytes_readonly", counted_read)
    cache: dict[tuple[str, int], str] = {}
    try:
        for _ in range(2):
            linting_module._validate_citation_excerpt(
                workspace,
                index,
                source_id=citation["source_id"],
                source_version=citation["source_version"],
                locator=citation["locator"],
                quote=citation["quote"],
                grounding=citation.get("grounding", "exact"),
                evidence_cache=cache,
            )
    finally:
        index.close()

    assert reads == 1


def test_lint_reads_normal_workspace_without_workspace_open(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    before = _workspace_state(workspace)

    def fail_workspace_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("lint must not open the mutable Workspace lifecycle")

    def fail_blob_chain(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("lint must not use the mutable blob chain helper")

    monkeypatch.setattr(Workspace, "open", fail_workspace_open)
    monkeypatch.setattr(blob_store_module, "open_blob_chain", fail_blob_chain)

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"status": "clean", "checked_pages": 1, "issues": []}
    assert _workspace_state(workspace) == before


def test_lint_reports_missing_workspace_index_without_opening_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    (workspace / ".memoryforge/index.sqlite").unlink()

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "status": "issues",
        "checked_pages": 0,
        "issues": [
            {
                "code": "invalid_workspace_index",
                "path": ".memoryforge/index.sqlite",
                "message": "workspace index is unavailable for read-only linting",
            }
        ],
    }


def test_lint_reports_missing_index_page_and_source_mapping_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _workspace_with_applied_source(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            f'sources: ["{imported["source_id"]}"]',
            f'sources: ["{"a" * 64}"]',
        ),
        encoding="utf-8",
    )
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Gone](pages/gone.md) — broken link\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "issues"
    assert {issue["code"] for issue in payload["issues"]} == {
        "citation_source_not_owned",
        "index_missing_page",
        "missing_index_entry",
        "missing_source_citation",
        "orphan_page",
        "source_mapping_mismatch",
    }


def test_lint_is_read_only_and_rejects_fix_proposal(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    before = {path: path.read_text(encoding="utf-8") for path in (workspace / "wiki").rglob("*.md")}

    result = runner.invoke(app, ["lint", "--fix-proposal", "--workspace", str(workspace)])

    assert result.exit_code == 2
    assert "lint is read-only" in result.output
    after = {path: path.read_text(encoding="utf-8") for path in (workspace / "wiki").rglob("*.md")}
    assert after == before


def test_lint_reports_page_without_citation(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page.write_text(
        page.read_text(encoding="utf-8").split("\n[^", maxsplit=1)[0] + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert [issue["code"] for issue in json.loads(result.stdout)["issues"]] == [
        "fact_index_page_mismatch",
        "missing_citation",
        "missing_source_citation",
    ]


def test_lint_reports_source_that_needs_recompile(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, imported = _workspace_with_applied_source(tmp_path, monkeypatch)
    source = workspace.parent / "note.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after thirty seconds.\n",
        encoding="utf-8",
    )
    assert runner.invoke(app, ["import", str(source), "--workspace", str(workspace)]).exit_code == 0

    payload = lint_workspace(workspace)

    assert payload["status"] == "issues"
    assert [issue["code"] for issue in payload["issues"]] == ["source_needs_recompile"]
    assert imported["source_id"] in payload["issues"][0]["message"]


def test_lint_reports_missing_related_page(tmp_path: Path, monkeypatch) -> None:
    _, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page.write_text(
        page.read_text(encoding="utf-8") + "\n## Related pages\n\n- [Missing](missing.md)\n",
        encoding="utf-8",
    )

    payload = lint_workspace(workspace)

    assert payload["status"] == "issues"
    assert [issue["code"] for issue in payload["issues"]] == ["related_page_missing"]


def test_lint_reports_orphan_page_when_not_indexed_or_linked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    (workspace / "wiki/INDEX.md").write_text("# Knowledge Index\n", encoding="utf-8")

    payload = lint_workspace(workspace)

    assert payload["status"] == "issues"
    assert {issue["code"] for issue in payload["issues"]} == {
        "missing_index_entry",
        "orphan_page",
    }


def test_lint_rejects_reversed_citation_range(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page.write_text(
        re.sub(r"chars:\d+-\d+", "chars:9-2", page.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert [issue["code"] for issue in json.loads(result.stdout)["issues"]] == [
        "fact_index_page_mismatch",
        "invalid_citation",
    ]


def test_lint_rejects_quote_not_grounded_by_locator(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "Cache entries expire after sixty seconds.",
            "Cache entries never expire.",
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert [issue["code"] for issue in json.loads(result.stdout)["issues"]] == [
        "fact_index_page_mismatch",
        "invalid_citation",
    ]


def test_lint_reports_each_declared_source_without_a_citation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, first = _workspace_with_applied_source(tmp_path, monkeypatch)
    second_source = workspace.parent / "second.md"
    second_source.write_text("# Storage\n\nStorage remains local.\n", encoding="utf-8")
    second_result = runner.invoke(
        app,
        ["import", str(second_source), "--workspace", str(workspace)],
    )
    assert second_result.exit_code == 0, second_result.output
    second = json.loads(second_result.stdout)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page_path = str(page.relative_to(workspace))
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            f'sources: ["{first["source_id"]}"]',
            f'sources: ["{first["source_id"]}", "{second["source_id"]}"]',
        ),
        encoding="utf-8",
    )
    Workspace.open(workspace).replace_applied_page_sources(
        {page_path: (first["source_id"], second["source_id"])}
    )

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    missing = [
        issue
        for issue in json.loads(result.stdout)["issues"]
        if issue["code"] == "missing_source_citation"
    ]
    assert missing == [
        {
            "code": "missing_source_citation",
            "path": page_path,
            "message": f"page has no citation for declared source: {second['source_id']}",
        }
    ]


def test_lint_reports_invalid_utf8_index(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    (workspace / "wiki/INDEX.md").write_bytes(b"\xff")

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "status": "issues",
        "checked_pages": 0,
        "issues": [
            {
                "code": "unreadable_index",
                "path": "wiki/INDEX.md",
                "message": "INDEX.md cannot be read as UTF-8",
            }
        ],
    }


def test_lint_reports_invalid_utf8_page(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    page = next((workspace / "wiki/pages").glob("*.md"))
    page.write_bytes(b"\xff")

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "status": "issues",
        "checked_pages": 1,
        "issues": [
            {
                "code": "unreadable_page",
                "path": str(page.relative_to(workspace)),
                "message": "page cannot be read as UTF-8",
            }
        ],
    }


def test_lint_does_not_follow_wiki_symlinks(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    external = tmp_path / "outside.md"
    external.write_text("outside wiki", encoding="utf-8")
    escaped_page = workspace / "wiki/pages/escaped.md"
    escaped_page.symlink_to(external)

    payload = lint_workspace(workspace)
    assert payload["checked_pages"] == 1
    assert [issue["code"] for issue in payload["issues"]] == ["invalid_page_path"]


def test_lint_does_not_follow_symlinked_index(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    external_index = tmp_path / "outside-index.md"
    external_index.write_text("# outside", encoding="utf-8")
    index = workspace / "wiki/INDEX.md"
    index.unlink()
    index.symlink_to(external_index)
    _fail_if_path_is_read(monkeypatch, external_index)

    result = runner.invoke(app, ["lint", "--workspace", str(workspace)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["checked_pages"] == 0
    assert [issue["code"] for issue in payload["issues"]] == ["invalid_index_path"]


def test_lint_does_not_follow_symlinked_pages_directory(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_applied_source(tmp_path, monkeypatch)
    external_pages = tmp_path / "outside-pages"
    external_pages.mkdir()
    external_page = external_pages / "outside.md"
    external_page.write_text("# outside", encoding="utf-8")
    pages = workspace / "wiki/pages"
    shutil.rmtree(pages)
    pages.symlink_to(external_pages, target_is_directory=True)
    _fail_if_path_is_read(monkeypatch, external_page)

    assert lint_workspace(workspace) == {
        "status": "issues",
        "checked_pages": 0,
        "issues": [
            {
                "code": "invalid_pages_path",
                "path": "wiki/pages",
                "message": "pages must be a real directory inside wiki",
            }
        ],
    }


def _fail_if_path_is_read(monkeypatch, external_path: Path) -> None:
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == external_path:
            raise AssertionError(f"lint followed external path: {path}")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)


def _workspace_state(workspace: Path) -> dict[Path, tuple[int, int, int, int]]:
    return {
        path.relative_to(workspace): (
            path.lstat().st_mode,
            path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns,
            path.lstat().st_size,
        )
        for path in (workspace, *workspace.rglob("*"))
        if not path.is_symlink()
    }


def _workspace_with_applied_source(
    tmp_path: Path,
    monkeypatch,
) -> tuple[CliRunner, Path, dict[str, Any]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    imported_result = runner.invoke(app, ["import", str(source), "--workspace", str(workspace)])
    assert imported_result.exit_code == 0
    ingested = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert ingested.exit_code == 0
    changeset_id = json.loads(ingested.stdout)["changeset_id"]
    assert review_approve_apply(runner, changeset_id, workspace).exit_code == 0
    return runner, workspace, json.loads(imported_result.stdout)
