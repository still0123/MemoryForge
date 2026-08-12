from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner, Result

from memoryforge.cli import app


def review_approve_apply(
    runner: CliRunner,
    changeset_id: str,
    workspace: Path,
) -> Result:
    """Run the public lifecycle, returning the first failure or apply result."""
    for command in ("review", "approve"):
        result = runner.invoke(
            app,
            [command, changeset_id, "--workspace", str(workspace)],
        )
        if result.exit_code != 0:
            return result
    return runner.invoke(
        app,
        ["apply", changeset_id, "--workspace", str(workspace)],
    )
