from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.importer import import_local_file
from memoryforge.models import Sensitivity
from memoryforge.workspace import Workspace


def test_obsidian_build_three_views_and_deterministic_output(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    workspace = tmp_path / "workspace"
    workspace_root = Workspace.initialize(workspace).root
    runner = CliRunner()

    titles = {
        "public-stable.md": "Public stable",
        "public-shared.md": "Public shared",
        "private-shared.md": "Private shared",
    }
    opened = Workspace.open(workspace_root)
    for name, title in titles.items():
        source_path = repository / name
        source_path.write_text(f"# {title}\n\nNavigation fixture.\n", encoding="utf-8")
        sensitivity = Sensitivity.LOCAL_ONLY if name.startswith("private") else Sensitivity.PUBLIC
        tags = ("shared-state",) if "shared" in name else ()
        imported = import_local_file(
            workspace_root,
            source_path,
            source_root=repository,
            tags=tags,
            sensitivity=sensitivity,
        )
        opened.record_applied_source_versions(
            {imported.source_id: _current_source_version(workspace_root, imported.source_id)}
        )
        page_path = f"wiki/pages/{imported.source_id}.md"
        page = workspace_root / page_path
        page.write_text(
            f"---\ntitle: {json.dumps(title)}\n---\n\n# {title}\n",
            encoding="utf-8",
        )
        opened.replace_applied_page_sources({page_path: (imported.source_id,)})

    # Unapproved source metadata must not reclassify the already-applied stable page.
    stable_source = repository / "public-stable.md"
    stable_source.write_text("# Public stable\n\nUnapproved update.\n", encoding="utf-8")
    import_local_file(
        workspace_root,
        stable_source,
        source_root=repository,
        tags=("shared-state",),
        sensitivity=Sensitivity.LOCAL_ONLY,
    )

    first = runner.invoke(
        app,
        ["obsidian-build", "--workspace", str(workspace_root)],
    )

    assert first.exit_code == 0, first.output
    payload = json.loads(first.stdout)
    assert payload["status"] == "built"
    assert payload["output"] == "obsidian"
    assert payload["counts"] == {
        "private_processes": 1,
        "stable_knowledge": 1,
        "shared_state": 1,
    }
    output_dir = workspace_root / payload["output"]
    private = (output_dir / "private-processes.md").read_text(encoding="utf-8")
    stable = (output_dir / "stable-knowledge.md").read_text(encoding="utf-8")
    shared = (output_dir / "shared-state.md").read_text(encoding="utf-8")

    assert "Private shared" in private
    assert "Public stable" not in private
    assert "Public shared" not in private
    assert "Public stable" in stable
    assert "Public shared" not in stable
    assert "Private shared" not in stable
    assert "Public shared" in shared
    assert '"shared-state"' in shared
    assert "Private shared" not in shared
    assert "Public stable" not in shared
    assert (output_dir / ".gitignore").is_file()

    snapshot = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(output_dir.glob("*.md"))
    }
    second = runner.invoke(
        app,
        ["obsidian-build", "--workspace", str(workspace_root)],
    )

    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout) == payload
    assert {
        path.name: path.read_text(encoding="utf-8") for path in sorted(output_dir.glob("*.md"))
    } == snapshot


def _current_source_version(workspace: Path, source_id: str) -> int:
    with sqlite3.connect(workspace / ".memoryforge" / "index.sqlite") as connection:
        row = connection.execute(
            """
            SELECT source_versions.id
            FROM source_versions
            JOIN sources ON sources.id = source_versions.source_id
            WHERE sources.source_id = ? AND source_versions.is_current = 1
            """,
            (source_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])
