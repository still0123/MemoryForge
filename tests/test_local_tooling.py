import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

import memoryforge

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_release_check.py"
_spec = importlib.util.spec_from_file_location("run_release_check", _SCRIPT)
assert _spec and _spec.loader
run_release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_release_check)


def test_package_and_cli_versions_match_v040() -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["name"] == "memoryforge-wiki"
    assert project["project"]["version"] == "0.4.0"
    assert memoryforge.__version__ == "0.4.0"
    assert project["project"]["readme"] == "PACKAGE_README.md"
    assert project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"] == [
        "/LICENSE",
        "/PACKAGE_README.md",
        "/pyproject.toml",
        "/src",
    ]


def test_local_check_keeps_the_quality_and_artifact_contract() -> None:
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts/check_local.sh").read_text(encoding="utf-8")

    for required in (
        "ruff check --no-cache .",
        "ruff format --check .",
        "mypy",
        "demo/validate_benchmark_registry.py",
        "pytest -W error::ResourceWarning",
        "error::pytest.PytestUnraisableExceptionWarning",
        'python="$(cd "$(dirname "$python")" && pwd)/$(basename "$python")"',
        "core.hooksPath=/dev/null",
        'worktree add --detach "$snapshot" "$commit"',
        'export PYTHONPATH="$snapshot/src"',
        'output="$root/${output#./}"',
        '"$name" == PIP_* || "$name" == UV_*',
        '"$workdir/build/bin/python" -m build',
        "--wheel --sdist --no-isolation",
        "uv pip install",
        "--no-config --default-index https://pypi.org/simple",
        "--isolated --index-url https://pypi.org/simple",
        "demo/run_release_check.py",
        "--code-evidence-output",
        "code-wiki-evidence.json",
        "PIP_CONSTRAINT=",
        "pip install",
        "hatchling",
        "--no-build-isolation",
        "pip check",
        "env -u PYTHONPATH PYTHONNOUSERSITE=1",
        "-I -m pip check",
        "sdist import escaped clean environment",
        "sdist contains retained or nested artifacts",
        "/demo/results/artifacts/",
        "dist/memoryforge_wiki-*.whl",
        "dist/memoryforge_wiki-*.tar.gz",
        '"$workdir/sdist/bin/python" -I -m memoryforge --version',
        "hashlib.sha256",
        "SHA256SUMS",
    ):
        assert required in script
    assert script.index("worktree add --detach") < script.index("pytest -W")
    assert script.index('output="$root/${output#./}"') < script.index('mkdir -p "$output/dist"')


def test_powershell_gate_uses_literal_paths_and_isolated_sdist_probe() -> None:
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts/check_local.ps1").read_text(encoding="utf-8")

    for required in (
        "Resolve-Path -LiteralPath",
        "Set-Location -LiteralPath",
        "Get-ChildItem -LiteralPath",
        "Get-Item -LiteralPath",
        "Get-FileHash -Algorithm SHA256 -LiteralPath",
        "Set-Content -LiteralPath",
        "Remove-Item Env:PYTHONPATH",
        '"core.hooksPath=NUL"',
        '"worktree", "add", "--detach", $Snapshot, $Commit',
        '$env:PYTHONPATH = Join-Path $Snapshot "src"',
        '$_ -Like "PIP_*" -or $_ -Like "UV_*"',
        '"--no-config", "--default-index", "https://pypi.org/simple"',
        '"--isolated", "--index-url", "https://pypi.org/simple"',
        "$OriginalEnvironment",
        "[Environment]::SetEnvironmentVariable",
        "Set-Location -LiteralPath $PreviousLocation.Path",
        "--code-evidence-output",
        "code-wiki-evidence.json",
        'Invoke-External $SdistPython @("-I", "-m", "pip", "check")',
        "sdist import escaped clean environment",
        "sdist contains retained or nested artifacts",
        "/demo/results/artifacts/",
        '"memoryforge_wiki-*.whl"',
        '"memoryforge_wiki-*.tar.gz"',
        'Invoke-External $SdistPython @("-I", "-m", "memoryforge", "--version")',
    ):
        assert required in script
    assert script.index("$Workdir = $null") < script.index("try {")
    assert script.index("try {") < script.index("Set-Location -LiteralPath $Root")
    assert "if ($Workdir)" in script
    assert "$OriginalEnvironment.ContainsKey($Name)" in script
    assert script.index('"worktree", "add", "--detach"') < script.index('"-m", "pytest"')


def test_github_actions_workflows_are_removed() -> None:
    root = Path(__file__).resolve().parent.parent
    workflows = root / ".github/workflows"

    assert not workflows.exists() or not any(workflows.glob("*.yml"))


def test_release_check_requires_the_frozen_public_repository_identity() -> None:
    root = Path(__file__).resolve().parent.parent
    evidence = json.loads(
        (root / "demo/results/agent_skill_eval_public.json").read_text(encoding="utf-8")
    )

    run_release_check._validate_public_evidence(evidence)
    evidence["source_repositories"][0]["repository_id"] = "0" * 64

    with pytest.raises(SystemExit, match="unexpected source identity"):
        run_release_check._validate_public_evidence(evidence)
