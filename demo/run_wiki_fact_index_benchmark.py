#!/usr/bin/env python3
"""Build two pinned public Code Wikis and verify deterministic Fact FTS5 evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, cast

from memoryforge.workspace import search_wiki_facts

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "demo/evaluation/external_code_wiki_learn_claude_code_sources_v031.json"
REPOSITORY_NAME = "learn_claude_code"
_EXTERNAL_SCRIPT = REPO_ROOT / "demo/run_external_code_wiki_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("run_external_code_wiki_benchmark", _EXTERNAL_SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("could not load external Code Wiki benchmark")
external_benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(external_benchmark)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    source_repo = args.source_repo.resolve()
    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")

    runs = []
    canonical_rows = []
    for name in ("first", "second"):
        run_root = workdir / name
        structural = external_benchmark.build_evidence(
            run_root,
            {REPOSITORY_NAME: source_repo},
            MANIFEST,
        )
        workspace = run_root / REPOSITORY_NAME / "workspace"
        fact_evidence = _fact_evidence(workspace)
        runs.append(
            {
                "name": name,
                "structural_passed": structural["summary"]["passed"],
                "fact_evidence": fact_evidence,
            }
        )
        canonical_rows.append(fact_evidence["canonical_rows_sha256"])

    first = runs[0]["fact_evidence"]
    deterministic = canonical_rows[0] == canonical_rows[1]
    gates = {
        "structural_benchmark": all(run["structural_passed"] for run in runs),
        "fact_rows_match_fts": all(
            run["fact_evidence"]["fact_count"] == run["fact_evidence"]["fts_count"] for run in runs
        ),
        "facts_use_applied_source_versions": all(
            run["fact_evidence"]["fact_count"]
            == run["fact_evidence"]["applied_source_version_fact_count"]
            for run in runs
        ),
        "metadata_round_trip": all(
            run["fact_evidence"]["invalid_metadata_count"] == 0 for run in runs
        ),
        "deterministic_replay": deterministic,
    }
    evidence = {
        "schema_version": 1,
        "memoryforge_commit": _git("rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git("status", "--porcelain")),
        "source_manifest": {
            "path": str(MANIFEST.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        },
        "source_repository": {
            "remote_url": _git_at(source_repo, "remote", "get-url", "origin"),
            "commit": _git_at(source_repo, "rev-parse", "HEAD"),
        },
        "counts": {
            "facts": first["fact_count"],
            "pages_with_facts": first["page_count"],
            "symbols": first["symbol_count"],
            "relations": first["relation_count"],
        },
        "canonical_rows_sha256": canonical_rows[0],
        "sample_exact_symbol_search": first["sample_exact_symbol_search"],
        "runs": runs,
        "gates": gates,
        "passed": all(gates.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote Wiki Fact index evidence to {output}")
    if not evidence["passed"]:
        raise SystemExit("Wiki Fact index benchmark failed")


def _fact_evidence(workspace: Path) -> dict[str, Any]:
    database = workspace / ".memoryforge/index.sqlite"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                fact_id, page_path, repository_id, source_id, source_version,
                locator, section_path, quote, routing_text, symbol, relation_type
            FROM wiki_facts
            ORDER BY fact_id
            """
        ).fetchall()
        fts_count = int(connection.execute("SELECT COUNT(*) FROM wiki_fact_fts").fetchone()[0])
        applied_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM wiki_facts AS facts
                JOIN applied_source_versions AS applied
                  ON applied.source_id = facts.source_id
                 AND applied.source_version_id = facts.source_version
                """
            ).fetchone()[0]
        )
        repository_id = str(
            connection.execute("SELECT repository_id FROM git_repositories").fetchone()[0]
        )
    canonical = [dict(row) for row in rows]
    invalid_metadata_count = sum(
        not row["fact_id"]
        or not row["page_path"]
        or not row["source_id"]
        or int(row["source_version"]) < 1
        or not row["locator"]
        or not row["quote"]
        for row in rows
    )
    exact_results = search_wiki_facts(
        workspace,
        "check_permission",
        repository_id=repository_id,
        limit=3,
    )
    return {
        "fact_count": len(rows),
        "fts_count": fts_count,
        "applied_source_version_fact_count": applied_count,
        "page_count": len({str(row["page_path"]) for row in rows}),
        "symbol_count": sum(row["symbol"] is not None for row in rows),
        "relation_count": sum(row["relation_type"] is not None for row in rows),
        "invalid_metadata_count": invalid_metadata_count,
        "canonical_rows_sha256": hashlib.sha256(
            json.dumps(
                cast(list[dict[str, Any]], canonical),
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        "sample_exact_symbol_search": [
            {
                "page_path": result.page_path,
                "source_id": result.source_id,
                "source_version": result.source_version,
                "locator": result.locator,
                "symbol": result.symbol,
            }
            for result in exact_results
        ],
    }


def _git(*args: str) -> str:
    return _git_at(REPO_ROOT, *args)


def _git_at(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
