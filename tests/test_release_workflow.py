import tomllib
from pathlib import Path

import memoryforge


def test_package_and_cli_versions_match_v021() -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "0.2.1"
    assert memoryforge.__version__ == "0.2.1"


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
