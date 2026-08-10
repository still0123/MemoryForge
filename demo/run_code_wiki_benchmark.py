#!/usr/bin/env python3
"""Run the fixed, model-free C0 code Wiki benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from memoryforge.code_evaluation import CodeEvaluationSuite, run_code_evaluation
from memoryforge.code_index import build_code_index
from memoryforge.code_models import ModuleNode
from memoryforge.code_wiki_compiler import compile_code_wiki
from memoryforge.module_planner import build_module_plan

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "demo/fixtures/code_wiki_project"
SUITE = REPO_ROOT / "demo/evaluation/code_wiki_eval.json"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    evidence = build_evidence(workdir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote code Wiki evidence to {output}")


def build_evidence(
    workdir: Path,
    *,
    include_sample_query: bool = False,
) -> dict[str, Any]:
    source_repo = workdir / "source"
    workspace = workdir / "workspace"
    shutil.copytree(FIXTURE, source_repo)
    _git(source_repo, "init")
    _git(
        source_repo,
        "remote",
        "add",
        "origin",
        "https://example.invalid/memoryforge-showcase-fixture.git",
    )
    _git(source_repo, "config", "user.email", "benchmark@example.com")
    _git(source_repo, "config", "user.name", "MemoryForge Benchmark")
    _git(source_repo, "add", ".")
    _git(source_repo, "commit", "-m", "C0 fixture v1")
    fixture_commit = _git_output(source_repo, "rev-parse", "HEAD")

    _cli("init", str(workspace))
    registration = _cli_json("git-add", str(source_repo), *_ws(workspace))
    repository_id = registration["repository_id"]
    _cli_json("git-sync", repository_id, *_ws(workspace))
    for path in ("py", "go", "ts"):
        _cli_json("code-add", repository_id, path, *_ws(workspace))
    _cli_json("git-sync", repository_id, *_ws(workspace))

    first_snapshot = build_code_index(workspace, repository_id)
    first_plan = build_module_plan(first_snapshot)
    ingest = _cli_json("ingest", "--code-wiki", repository_id, *_ws(workspace))
    _cli("review", ingest["changeset_id"], *_ws(workspace))
    _cli_json("approve", ingest["changeset_id"], *_ws(workspace))
    applied = _cli_json("apply", ingest["changeset_id"], *_ws(workspace))
    lint = _cli_json("lint", *_ws(workspace))
    evaluation = run_code_evaluation(workspace, repository_id, SUITE)
    sample_query = None
    if include_sample_query:
        sample_query = _cli_json(
            "ask",
            "Which module does ts.service import?",
            "--debug",
            "--verify",
            "--repository",
            repository_id,
            *_ws(workspace),
        )
        if sample_query.get("status") != "answered" or not sample_query.get("citations"):
            raise RuntimeError("fixed Code Wiki Showcase query was not answered")

    suite = CodeEvaluationSuite.model_validate_json(SUITE.read_text(encoding="utf-8"))
    incremental = suite.incremental
    if incremental is None:
        raise RuntimeError("fixed C0 suite must define incremental expectations")
    changed_file = source_repo / incremental.relative_path
    original = changed_file.read_text(encoding="utf-8")
    if incremental.old_text not in original:
        raise RuntimeError("incremental fixture no longer contains old_text")
    changed_file.write_text(
        original.replace(incremental.old_text, incremental.new_text, 1),
        encoding="utf-8",
    )
    _git(source_repo, "add", incremental.relative_path)
    _git(source_repo, "commit", "-m", "C0 fixture v2")
    updated_commit = _git_output(source_repo, "rev-parse", "HEAD")
    _cli_json("git-sync", repository_id, *_ws(workspace))

    second_snapshot = build_code_index(workspace, repository_id)
    second_plan = build_module_plan(second_snapshot)
    update = compile_code_wiki(workspace, second_snapshot, second_plan)
    if update is None:
        raise RuntimeError("incremental fixture produced no code Wiki update")
    first_symbols = {symbol.symbol_id: symbol for symbol in first_snapshot.symbols}
    changed_symbols = sorted(
        symbol.qualified_name
        for symbol in second_snapshot.symbols
        if symbol.symbol_id in first_symbols
        and symbol.body_sha256 != first_symbols[symbol.symbol_id].body_sha256
    )
    changed_pages = sorted(
        _display_code_page(path, repository_id)
        for path in update.candidate_files
        if path.startswith("wiki/pages/code/")
    )
    expected_changed_pages = sorted(
        _display_code_page(_repository_scoped_code_page(repository_id, path), repository_id)
        for path in incremental.expected_changed_pages
    )
    source_module_count = sum(
        bool(module.symbol_ids) for module in _flatten_modules(first_plan.modules)
    )
    changed_page_ratio = round(len(changed_pages) / source_module_count, 3)
    incremental_result = {
        "changed_symbols": changed_symbols,
        "expected_changed_symbols": list(incremental.expected_changed_symbols),
        "changed_pages": changed_pages,
        "expected_changed_pages": expected_changed_pages,
        "changed_page_ratio": changed_page_ratio,
        "max_changed_page_ratio": incremental.max_changed_page_ratio,
        "stable_symbol_ids": set(first_symbols)
        <= {symbol.symbol_id for symbol in second_snapshot.symbols},
    }
    incremental_result["passed"] = (
        changed_symbols == sorted(incremental.expected_changed_symbols)
        and changed_pages == expected_changed_pages
        and changed_page_ratio <= incremental.max_changed_page_ratio
        and incremental_result["stable_symbol_ids"]
    )
    evidence = {
        "schema_version": 1,
        "memoryforge_commit": _git_output(REPO_ROOT, "rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git_output(REPO_ROOT, "status", "--porcelain")),
        "fixture": {
            "path": str(FIXTURE.relative_to(REPO_ROOT)),
            "suite_sha256": hashlib.sha256(SUITE.read_bytes()).hexdigest(),
            "initial_commit": fixture_commit,
            "updated_commit": updated_commit,
        },
        "workflow": {
            "wiki_file_count": sum(
                path.startswith("wiki/pages/code/") for path in applied["files"]
            ),
            "lint": lint,
        },
        "evaluation": evaluation,
        "incremental": incremental_result,
    }
    if sample_query is not None:
        evidence["sample_query"] = {
            "question": "Which module does ts.service import?",
            "answer": sample_query["answer"],
            "citations": sample_query["citations"],
            "trace": sample_query.get("trace", []),
        }
    return evidence


def _flatten_modules(modules: tuple[ModuleNode, ...]) -> tuple[ModuleNode, ...]:
    return tuple(module for root in modules for module in (root, *_flatten_modules(root.children)))


def _repository_scoped_code_page(repository_id: str, path: str) -> str:
    prefix = "wiki/pages/code/"
    if not path.startswith(prefix):
        raise ValueError(f"expected a code Wiki page path: {path}")
    return f"{prefix}{repository_id[:12]}/{path.removeprefix(prefix)}"


def _display_code_page(path: str, repository_id: str) -> str:
    prefix = f"wiki/pages/code/{repository_id[:12]}/"
    if not path.startswith(prefix):
        raise ValueError(f"code Wiki page is not scoped to the repository: {path}")
    return "wiki/pages/code/<repository-id-prefix>/" + path.removeprefix(prefix)


def _ws(workspace: Path) -> tuple[str, str]:
    return "--workspace", str(workspace)


def _cli(*args: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "memoryforge", *args],
        cwd=REPO_ROOT,
        env=_git_environment(),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"memoryforge {' '.join(args)} failed:\n{completed.stderr.strip()}")
    return completed.stdout


def _cli_json(*args: str) -> Any:
    return json.loads(_cli(*args))


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            *args,
        ],
        cwd=root,
        env=_git_environment(),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_environment() -> dict[str, str]:
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "MemoryForge Benchmark",
        "GIT_AUTHOR_EMAIL": "benchmark@example.com",
        "GIT_COMMITTER_NAME": "MemoryForge Benchmark",
        "GIT_COMMITTER_EMAIL": "benchmark@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return environment


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=root,
        env=_git_environment(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
