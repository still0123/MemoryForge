from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_public_demo.py"
_spec = importlib.util.spec_from_file_location("run_public_demo", _SCRIPT)
assert _spec and _spec.loader
run_public_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_public_demo)


def test_run_public_demo_builds_reproducible_evidence_from_a_tiny_git_repo(
    tmp_path: Path,
) -> None:
    source_repo = _tiny_git_repo(tmp_path / "repo")
    eval_config = tmp_path / "suite.json"
    eval_config.write_text(
        json.dumps(
            {
                "name": "cache",
                "cases": [
                    {
                        "id": "cache-expiry",
                        "question": "When do cache entries expire?",
                        "expected_source_path": "docs/cache.md",
                        "required_terms": ["sixty", "seconds"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "evidence.json"

    run_public_demo.main(
        [
            "--source-repo",
            str(source_repo),
            "--workspace",
            str(tmp_path / "workspace"),
            "--output",
            str(output),
            "--eval-config",
            str(eval_config),
            "--question",
            "When do cache entries expire?",
        ]
    )

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["source_repository"]["commit"] == _git_head(source_repo)
    assert evidence["sample_query"]["citations"]
    assert evidence["workflow"]["lint"]["status"] == "clean"
    assert evidence["evaluation"]["memoryforge"]["answer_accuracy"] == 100.0


def _tiny_git_repo(root: Path) -> Path:
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text("# Tiny\n\nA tiny demo repository.\n", encoding="utf-8")
    (docs / "cache.md").write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "demo@example.com")
    _git(root, "config", "user.name", "demo")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "seed")
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _git_head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
