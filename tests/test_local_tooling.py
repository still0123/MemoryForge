import tomllib
from pathlib import Path

import memoryforge


def test_package_and_cli_versions_match_v021() -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "0.2.1"
    assert memoryforge.__version__ == "0.2.1"


def test_local_check_keeps_the_quality_and_artifact_contract() -> None:
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts/check_local.sh").read_text(encoding="utf-8")

    for required in (
        "ruff check .",
        "ruff format --check .",
        "mypy",
        "pytest -W error::ResourceWarning",
        '"$workdir/build/bin/python" -m build',
        "--wheel --sdist --no-isolation",
        "uv pip install",
        "demo/run_release_check.py",
        "PIP_CONSTRAINT=",
        "pip install",
        "hatchling",
        "--no-build-isolation",
        "pip check",
        "hashlib.sha256",
        "SHA256SUMS",
    ):
        assert required in script


def test_github_actions_workflows_are_removed() -> None:
    root = Path(__file__).resolve().parent.parent
    workflows = root / ".github/workflows"

    assert not workflows.exists() or not any(workflows.glob("*.yml"))
