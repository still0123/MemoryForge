from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from memoryforge.cli import app
from memoryforge.models import ChangeSet, ChangeSetStatus, Claim, ClaimStatus


def test_cli_initializes_and_imports_a_source(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "wiki"
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nKeep citations attached to claims.\n", encoding="utf-8")

    initialized = runner.invoke(app, ["init", str(workspace)])
    imported = runner.invoke(
        app,
        [
            "import",
            str(source),
            "--category",
            "notes",
            "--local-only",
            "--tag",
            "citation",
            "--tag",
            "design",
            "--workspace",
            str(workspace),
        ],
    )

    assert initialized.exit_code == 0, initialized.stdout
    assert imported.exit_code == 0, imported.stdout
    payload = json.loads(imported.stdout)
    assert payload["outcome"] == "imported"
    assert payload["source"]["sensitivity"] == "local_only"
    assert payload["source"]["tags"] == ["citation", "design"]


def test_cli_registers_future_commands_without_claiming_they_work(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "wiki"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["ask", "What is current?", "--workspace", str(workspace)])

    assert result.exit_code == 2
    assert "trusted-storage milestone" in result.output


def test_changeset_state_machine_requires_human_review() -> None:
    changeset = ChangeSet(
        changeset_id="chg_demo",
        base_commit="abc123",
        status=ChangeSetStatus.PROPOSED,
    )

    assert changeset.can_transition_to(ChangeSetStatus.VALIDATED)
    assert changeset.can_transition_to(ChangeSetStatus.REJECTED)
    assert not changeset.can_transition_to(ChangeSetStatus.APPROVED)
    assert not changeset.can_transition_to(ChangeSetStatus.APPLIED)


def test_verified_claim_cannot_exist_without_evidence() -> None:
    with pytest.raises(ValidationError, match="require at least one citation"):
        Claim(
            claim_id="clm_cache_key",
            subject="cache-key",
            predicate="uses",
            object="namespaced-hash",
            status=ClaimStatus.VERIFIED,
            confidence=0.96,
        )
