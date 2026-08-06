#!/usr/bin/env python3
"""Profile cold and single-file Code Wiki builds on pinned public repositories."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import statistics
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from tree_sitter import Parser

from memoryforge import code_index, go_index, typescript_index
from memoryforge.changesets import ChangeSetStore
from memoryforge.code_index import build_code_index
from memoryforge.code_models import ModuleNode
from memoryforge.code_wiki_compiler import compile_code_wiki
from memoryforge.models import Sensitivity
from memoryforge.module_planner import build_module_plan
from memoryforge.workspace import (
    Workspace,
    register_git_checkout,
    register_git_code_module,
    sync_git_checkout,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "demo/evaluation/external_code_wiki_sources_v021.json"
_MARKERS = {
    ".py": "\n\ndef memoryforge_profile_marker() -> None:\n    pass\n",
    ".go": "\n\nfunc memoryforgeProfileMarker() {}\n",
    ".ts": "\n\nexport function memoryforgeProfileMarker(): void {}\n",
    ".tsx": "\n\nexport function memoryforgeProfileMarker(): void {}\n",
}


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.child:
        print(
            json.dumps(
                _profile_repository(
                    args.repository,
                    args.source.resolve(),
                    args.run_dir.resolve(),
                    args.manifest.resolve(),
                ),
                ensure_ascii=False,
            )
        )
        return

    workdir = args.workdir.resolve()
    output = args.output.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        raise SystemExit(f"--workdir must be absent or empty: {workdir}")
    sources = _parse_sources(args.source_repo)
    evidence = build_evidence(
        workdir,
        sources,
        repetitions=args.repetitions,
        manifest_path=args.manifest.resolve(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote external Code Wiki profile to {output}")


def build_evidence(
    workdir: Path,
    sources: dict[str, Path],
    *,
    repetitions: int = 3,
    manifest_path: Path = MANIFEST,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repositories = manifest["repositories"]
    expected_names = {str(repository["name"]) for repository in repositories}
    if set(sources) != expected_names:
        raise ValueError(f"--source-repo names must be: {', '.join(sorted(expected_names))}")
    if repetitions < 1:
        raise ValueError("--repetitions must be positive")

    workdir.mkdir(parents=True, exist_ok=True)
    results = []
    for repository in repositories:
        name = str(repository["name"])
        source = sources[name].resolve()
        _verify_source(source, repository)
        samples = [
            _run_child(
                name,
                source,
                workdir / name / f"run-{run_number:02d}",
                manifest_path,
            )
            for run_number in range(1, repetitions + 1)
        ]
        results.append(_summarize_repository(name, samples))

    largest = max(results, key=lambda result: int(result["counts"]["symbols"]))
    cache_decision = _cache_decision(largest)
    return {
        "schema_version": 1,
        "memoryforge_commit": _git_output(REPO_ROOT, "rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": bool(_git_output(REPO_ROOT, "status", "--porcelain")),
        "scope": {
            "cold_start": "empty MemoryForge workspace; operating-system file cache not flushed",
            "single_file_update": (
                "committed synthetic function added to one selected source; "
                "Git commit time excluded"
            ),
            "timing": "wall clock seconds; medians decide the cache gate",
            "peak_rss": "per-repetition process high-water mark",
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "tree_sitter": importlib.metadata.version("tree-sitter"),
            "tree_sitter_python": importlib.metadata.version("tree-sitter-python"),
            "tree_sitter_go": importlib.metadata.version("tree-sitter-go"),
            "tree_sitter_typescript": importlib.metadata.version("tree-sitter-typescript"),
        },
        "source_manifest": {
            "path": str(manifest_path.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
        "repetitions": repetitions,
        "repositories": results,
        "cache_gate": cache_decision,
    }


def _profile_repository(
    name: str,
    source: Path,
    run_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repository = next(item for item in manifest["repositories"] if str(item["name"]) == name)
    suite = json.loads((REPO_ROOT / str(repository["suite"])).read_text(encoding="utf-8"))
    changed_path = str(suite["expected_source_paths"][0])
    source_worktree = run_dir / "source"
    workspace_path = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=True)
    _git(source, "worktree", "add", "--detach", str(source_worktree), repository["commit"])
    try:
        Workspace.initialize(workspace_path)
        registered = register_git_checkout(
            workspace_path,
            source_worktree,
            sensitivity=Sensitivity.PUBLIC,
        )
        for code_path in repository["code_paths"]:
            register_git_code_module(workspace_path, registered.repository_id, str(code_path))

        cold = _profile_pipeline(workspace_path, registered.repository_id)
        if cold["compilation"] is None:
            raise RuntimeError(f"{name} cold profile produced no Code Wiki ChangeSet")
        _apply_compilation(workspace_path, cold["compilation"])

        changed_file = source_worktree / changed_path
        marker = _MARKERS.get(changed_file.suffix)
        if marker is None:
            raise RuntimeError(f"no profile marker for {changed_path}")
        changed_file.write_text(
            changed_file.read_text(encoding="utf-8").rstrip() + marker,
            encoding="utf-8",
        )
        _git(source_worktree, "add", changed_path)
        _git(
            source_worktree,
            "-c",
            "user.name=MemoryForge Benchmark",
            "-c",
            "user.email=benchmark@example.com",
            "commit",
            "-m",
            "profile: single-file update",
        )
        update_commit = _git_output(source_worktree, "rev-parse", "HEAD")
        update = _profile_pipeline(workspace_path, registered.repository_id)
        compilation = update["compilation"]
        changed_pages = (
            sorted(
                path for path in compilation.candidate_files if path.startswith("wiki/pages/code/")
            )
            if compilation is not None
            else []
        )
        source_modules = sum(
            bool(module.symbol_ids) for module in _flatten_modules(cold["plan"].modules)
        )
        snapshot = update["snapshot"]
        return {
            "cold_start": cold["timings"],
            "single_file_update": {
                **update["timings"],
                "changed_file": changed_path,
                "commit": update_commit,
                "changed_pages": changed_pages,
                "changed_page_ratio": round(len(changed_pages) / source_modules, 6),
            },
            "counts": {
                "sources": len(snapshot.source_versions),
                "symbols": len(snapshot.symbols),
                "relations": len(snapshot.relations),
                "modules": len(_flatten_modules(update["plan"].modules)),
            },
            "peak_rss_bytes": _peak_rss_bytes(),
        }
    finally:
        _git(source, "worktree", "remove", "--force", str(source_worktree))


def _profile_pipeline(workspace: Path, repository_id: str) -> dict[str, Any]:
    timings = {
        "source_scan_and_sync_seconds": 0.0,
        "tree_sitter_parse_seconds": 0.0,
        "relation_resolver_seconds": 0.0,
        "code_index_other_seconds": 0.0,
        "module_plan_seconds": 0.0,
        "wiki_compile_seconds": 0.0,
        "total_seconds": 0.0,
    }
    total_started = perf_counter()

    started = perf_counter()
    sync_git_checkout(workspace, repository_id)
    timings["source_scan_and_sync_seconds"] = perf_counter() - started

    with _index_instrumentation(timings):
        started = perf_counter()
        snapshot = build_code_index(workspace, repository_id)
        code_index_seconds = perf_counter() - started
    timings["code_index_other_seconds"] = max(
        0.0,
        code_index_seconds
        - timings["tree_sitter_parse_seconds"]
        - timings["relation_resolver_seconds"],
    )

    started = perf_counter()
    plan = build_module_plan(snapshot)
    timings["module_plan_seconds"] = perf_counter() - started

    started = perf_counter()
    compilation = compile_code_wiki(workspace, snapshot, plan)
    timings["wiki_compile_seconds"] = perf_counter() - started
    timings["total_seconds"] = perf_counter() - total_started
    return {
        "snapshot": snapshot,
        "plan": plan,
        "compilation": compilation,
        "timings": {key: round(value, 6) for key, value in timings.items()},
    }


def _index_instrumentation(timings: dict[str, float]) -> ExitStack:
    stack = ExitStack()
    timed_parser = _timed_parser(timings)
    for module in (code_index, go_index, typescript_index):
        stack.enter_context(patch.object(module, "Parser", timed_parser))
    for module, names in (
        (code_index, ("_add_python_imports_and_calls", "build_relations")),
        (go_index, ("_add_go_calls", "build_relations")),
        (typescript_index, ("_add_import_relations", "_add_calls", "build_relations")),
    ):
        for name in names:
            stack.enter_context(
                patch.object(
                    module,
                    name,
                    _timed_call(getattr(module, name), timings, "relation_resolver_seconds"),
                )
            )
    return stack


def _timed_parser(timings: dict[str, float]) -> type[Any]:
    class TimedParser:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._parser = Parser(*args, **kwargs)

        def parse(self, *args: Any, **kwargs: Any) -> Any:
            started = perf_counter()
            try:
                return self._parser.parse(*args, **kwargs)
            finally:
                timings["tree_sitter_parse_seconds"] += perf_counter() - started

    return TimedParser


def _timed_call(
    function: Callable[..., Any],
    timings: dict[str, float],
    key: str,
) -> Callable[..., Any]:
    def timed(*args: Any, **kwargs: Any) -> Any:
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            timings[key] += perf_counter() - started

    return timed


def _apply_compilation(workspace_path: Path, compilation: Any) -> None:
    opened = Workspace.open(workspace_path)
    store = ChangeSetStore(opened)
    stored = store.create(compilation.changeset, compilation.candidate_files)
    with opened.exclusive_lock():
        store.record_review(stored)
        store.approve(stored)
    _cli("apply", stored.changeset.changeset_id, "--workspace", str(workspace_path))


def _summarize_repository(name: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    timing_keys = tuple(samples[0]["cold_start"])
    cold = {
        key: round(statistics.median(sample["cold_start"][key] for sample in samples), 6)
        for key in timing_keys
    }
    update = {
        key: round(
            statistics.median(sample["single_file_update"][key] for sample in samples),
            6,
        )
        for key in timing_keys
    }
    update_ratio = round(update["total_seconds"] / cold["total_seconds"], 6)
    peak_rss = round(
        statistics.median(sample["peak_rss_bytes"] for sample in samples) / (1024 * 1024),
        3,
    )
    return {
        "name": name,
        "counts": samples[0]["counts"],
        "median": {
            "cold_start": cold,
            "single_file_update": update,
            "single_file_update_to_cold_ratio": update_ratio,
            "peak_rss_mib": peak_rss,
        },
        "single_file_change": {
            key: samples[0]["single_file_update"][key]
            for key in ("changed_file", "changed_pages", "changed_page_ratio")
        },
        "samples": samples,
    }


def _cache_decision(largest: dict[str, Any]) -> dict[str, Any]:
    cold = float(largest["median"]["cold_start"]["total_seconds"])
    update = float(largest["median"]["single_file_update"]["total_seconds"])
    ratio = update / cold
    triggered = update > 10.0 and ratio > 0.5
    return {
        "largest_repository_by_symbol_count": largest["name"],
        "cold_start_seconds": cold,
        "single_file_update_seconds": update,
        "single_file_update_to_cold_ratio": round(ratio, 6),
        "thresholds": {
            "single_file_update_seconds_gt": 10.0,
            "single_file_update_to_cold_ratio_gt": 0.5,
        },
        "triggered": triggered,
        "decision": "IMPLEMENT_FILE_CACHE" if triggered else "NO_CHANGE",
    }


def _run_child(
    name: str,
    source: Path,
    run_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            __file__,
            "--child",
            "--repository",
            name,
            "--source",
            str(source),
            "--run-dir",
            str(run_dir),
            "--manifest",
            str(manifest_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _verify_source(source: Path, repository: dict[str, Any]) -> None:
    if _git_output(source, "rev-parse", "HEAD") != repository["commit"]:
        raise ValueError(f"{repository['name']} checkout is not at the pinned commit")
    if _git_output(source, "remote", "get-url", "origin") != repository["remote_url"]:
        raise ValueError(f"{repository['name']} checkout has the wrong origin")
    license_bytes = subprocess.run(
        ["git", "show", f"HEAD:{repository['license_path']}"],
        cwd=source,
        check=True,
        capture_output=True,
    ).stdout
    if hashlib.sha256(license_bytes).hexdigest() != repository["license_sha256"]:
        raise ValueError(f"{repository['name']} license hash does not match the manifest")


def _peak_rss_bytes() -> int:
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return rss if sys.platform == "darwin" else rss * 1024


def _flatten_modules(modules: tuple[ModuleNode, ...]) -> tuple[ModuleNode, ...]:
    return tuple(module for root in modules for module in (root, *_flatten_modules(root.children)))


def _parse_sources(values: list[str]) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ValueError("--source-repo must use NAME=/absolute/path")
        if name in sources:
            raise ValueError(f"duplicate --source-repo name: {name}")
        sources[name] = Path(path)
    return sources


def _cli(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "memoryforge", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", action="append", default=[])
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repository", help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    required = ("repository", "source", "run_dir") if args.child else ("workdir", "output")
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        parser.error("missing required arguments: " + ", ".join(f"--{name}" for name in missing))
    return args


if __name__ == "__main__":
    main()
