import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_release_check.py"
_spec = importlib.util.spec_from_file_location("run_release_check", _SCRIPT)
assert _spec and _spec.loader
run_release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_release_check)


def test_release_workflow_keeps_the_tag_and_artifact_contract() -> None:
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for required in (
        'tags:\n      - "v*"',
        'git cat-file -t "$GITHUB_REF_NAME"',
        'git rev-parse "$GITHUB_REF_NAME^{}"',
        "python -m build --wheel --sdist",
        "--no-isolation",
        "python demo/run_release_check.py",
        "PIP_CONSTRAINT:",
        "pip install hatchling",
        "--no-build-isolation dist/memoryforge-*.tar.gz",
        "actions/attest-build-provenance@v3",
        "gh release upload",
        "gh release download",
        "sha256sum --check SHA256SUMS",
    ):
        assert required in workflow


def test_release_check_requires_the_frozen_public_repository_identity() -> None:
    root = Path(__file__).resolve().parent.parent
    evidence = json.loads(
        (root / "demo/results/agent_skill_eval_public.json").read_text(encoding="utf-8")
    )

    run_release_check._validate_public_evidence(evidence)
    evidence["source_repositories"][0]["repository_id"] = "0" * 64

    with pytest.raises(SystemExit, match="unexpected source identity"):
        run_release_check._validate_public_evidence(evidence)
