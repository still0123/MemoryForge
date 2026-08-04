#!/usr/bin/env python3
"""Run the real MemoryForge CLI over a local public Git checkout and emit reproducible evidence.

This is not a new product feature and it registers no new CLI command. It only chains the
existing commands (init -> git-add -> git-sync -> ingest -> review -> apply -> lint -> ask ->
eval) along the real user path, then records their true JSON output as a committable evidence
file. It never clones, fetches, checks out, calls a model, or reads .env.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVAL_CONFIG = REPO_ROOT / "demo" / "evaluation" / "agent_skill_eval.json"
DEFAULT_QUESTION = "本地配对实验引擎依赖哪些服务？"


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    source_repositories = tuple(source_repo.resolve() for source_repo in args.source_repos)
    workspace: Path = args.workspace.resolve()
    output: Path = args.output.resolve()
    eval_config: Path = args.eval_config.resolve()

    for source_repo in source_repositories:
        if not (source_repo / ".git").exists():
            raise SystemExit(f"--source-repo must be an existing local Git checkout: {source_repo}")
    if workspace.exists() and any(workspace.iterdir()):
        raise SystemExit(f"--workspace must be absent or empty; refusing to touch it: {workspace}")
    if not eval_config.is_file():
        raise SystemExit(f"--eval-config does not exist: {eval_config}")

    evidence = _build_evidence(source_repositories, workspace, eval_config, args.question)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote public evidence to {output}")


def _build_evidence(
    source_repositories: tuple[Path, ...],
    workspace: Path,
    eval_config: Path,
    question: str,
) -> dict[str, Any]:
    _cli("init", str(workspace))
    synced_repositories = []
    for source_repo in source_repositories:
        registration = _cli_json("git-add", str(source_repo), "--public", *_ws(workspace))
        sync = _cli_json("git-sync", registration["repository_id"], *_ws(workspace))
        synced_repositories.append((registration, sync))
    ingest = _cli_json("ingest", "--pending", *_ws(workspace))
    _cli("review", ingest["changeset_id"], *_ws(workspace))  # run it; never store the full diff
    applied = _cli_json("apply", ingest["changeset_id"], "--approve", *_ws(workspace))
    lint = _cli_json("lint", *_ws(workspace))
    ask = _cli_json("ask", question, "--debug", "--verify", *_ws(workspace))
    evaluation = _cli_json("eval", str(eval_config), *_ws(workspace))

    return {
        "schema_version": 1,
        "memoryforge_commit": _git_head(REPO_ROOT),
        "source_repositories": [
            {
                "repository_id": registration["repository_id"],
                "remote_url": registration["remote_url"],
                "commit": sync["head_commit"],
                "source_count": sync["created"] + sync["updated"] + sync["unchanged"],
            }
            for registration, sync in synced_repositories
        ],
        "workflow": {
            "source_count": sum(
                sync["created"] + sync["updated"] + sync["unchanged"]
                for _registration, sync in synced_repositories
            ),
            "wiki_file_count": sum(path.startswith("wiki/pages/") for path in applied["files"]),
            "lint": lint,
        },
        "sample_query": {
            "question": question,
            "answer": ask["answer"],
            "citations": ask["citations"],
            "trace": ask.get("trace", []),
        },
        "evaluation": evaluation,
    }


def _ws(workspace: Path) -> tuple[str, str]:
    return ("--workspace", str(workspace))


def _cli(*args: str) -> str:
    """Invoke the installed CLI through the current interpreter; fail loudly, keep the workspace."""
    completed = subprocess.run(
        [sys.executable, "-m", "memoryforge", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"command failed (exit {completed.returncode}): memoryforge {' '.join(args)}\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _cli_json(*args: str) -> Any:
    return json.loads(_cli(*args))


def _git_head(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(f"could not read HEAD of {repo}\n{completed.stderr.strip()}")
    return completed.stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-repo",
        dest="source_repos",
        type=Path,
        action="append",
        required=True,
        help="Existing local checkout. Repeat to compile several public repositories together.",
    )
    parser.add_argument("--workspace", type=Path, required=True, help="Absent or empty workspace.")
    parser.add_argument("--output", type=Path, required=True, help="Evidence JSON path to write.")
    parser.add_argument("--eval-config", type=Path, default=DEFAULT_EVAL_CONFIG, help="Eval suite.")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="Fixed sample query.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
