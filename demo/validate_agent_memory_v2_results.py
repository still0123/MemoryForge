#!/usr/bin/env python3
"""Hard-threshold validation for agent-memory-v2 benchmark results JSON.

Checks:
  - source recall >= threshold (default 0.8)
  - privacy leak count == 0
  - deterministic_hash matches payload recomputed hash
  - required schema fields present
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

DEFAULT_RESULTS = (
    Path(__file__).resolve().parent.parent
    / "demo"
    / "results"
    / "agent_memory_v2_benchmark_result.json"
)


def _payload_sha256(payload: Any) -> str:
    stripped = {k: v for k, v in payload.items() if k != "deterministic_hash"}
    return hashlib.sha256(
        json.dumps(stripped, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def validate_results(
    result: dict[str, Any],
    *,
    source_recall_threshold: float = 0.8,
    page_recall_threshold: float = 0.8,
    max_privacy_leaks: int = 0,
    require_deterministic: bool = True,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("result payload must be a JSON object")

    if result.get("schema_version") != 1:
        raise ValueError("result.schema_version must be 1")

    required_root = ("suite_id", "suite_revision", "splits", "deterministic_hash")
    for field in required_root:
        if field not in result:
            raise ValueError(f"result missing required field: {field}")

    splits = cast(dict[str, Any], result["splits"])
    required_splits = ("development", "confirmation", "holdout")
    for split in required_splits:
        if split not in splits:
            raise ValueError(f"splits missing required split: {split}")

    dev = cast(dict[str, Any], splits["development"])
    dev_summary = cast(dict[str, Any], dev.get("summary", {}))
    dev_cases = cast(list[dict[str, Any]], dev.get("cases", []))

    if not dev_cases:
        raise ValueError("development split has no cases")

    summary = dev_summary if dev_summary else {}
    page_pct = summary.get("page_recall_at_3_pct", 0.0)
    source_pct = summary.get("source_recall_at_3_pct", 0.0)
    page_ratio = page_pct / 100.0
    source_ratio = source_pct / 100.0
    privacy_leak_count = int(summary.get("privacy_leak_count", 0))

    case_privacy_leaks = sum(
        1 for c in dev_cases if bool(c.get("privacy_leak_detected"))
    )
    total_privacy_leaks = privacy_leak_count + case_privacy_leaks

    reported_hash = str(result["deterministic_hash"])
    recomputed_hash = _payload_sha256(result)
    deterministic_ok = reported_hash == recomputed_hash

    gates: dict[str, bool] = {}
    failures: list[str] = []

    gates["source_recall_threshold"] = source_ratio >= source_recall_threshold
    if not gates["source_recall_threshold"]:
        failures.append(
            f"source_recall_at_3 {source_ratio:.3f} below threshold {source_recall_threshold}"
        )

    gates["page_recall_threshold"] = page_ratio >= page_recall_threshold
    if not gates["page_recall_threshold"]:
        failures.append(
            f"page_recall_at_3 {page_ratio:.3f} below threshold {page_recall_threshold}"
        )

    gates["privacy_leak_count"] = total_privacy_leaks <= max_privacy_leaks
    if not gates["privacy_leak_count"]:
        failures.append(
            f"privacy_leak_count {total_privacy_leaks} exceeds max {max_privacy_leaks}"
        )

    gates["deterministic_hash_match"] = (not require_deterministic) or deterministic_ok
    if require_deterministic and not deterministic_ok:
        failures.append(
            "deterministic_hash mismatch: reported != recomputed payload hash"
        )

    all_passed = all(gates.values())

    return {
        "status": "valid" if all_passed else "invalid",
        "passed": all_passed,
        "gates": gates,
        "failures": failures,
        "metrics": {
            "page_recall_at_3_pct": page_pct,
            "source_recall_at_3_pct": source_pct,
            "mean_reciprocal_rank": summary.get("mean_reciprocal_rank", 0.0),
            "privacy_leak_count": total_privacy_leaks,
            "development_case_count": len(dev_cases),
        },
        "hash": {
            "reported": reported_hash,
            "recomputed": recomputed_hash,
            "match": deterministic_ok,
        },
    }


def validate_results_file(
    result_path: Path,
    *,
    source_recall_threshold: float = 0.8,
    page_recall_threshold: float = 0.8,
    max_privacy_leaks: int = 0,
) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(result_path.read_text(encoding="utf-8")))
    return validate_results(
        payload,
        source_recall_threshold=source_recall_threshold,
        page_recall_threshold=page_recall_threshold,
        max_privacy_leaks=max_privacy_leaks,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, nargs="?", default=DEFAULT_RESULTS)
    parser.add_argument("--source-recall-threshold", type=float, default=0.8)
    parser.add_argument("--page-recall-threshold", type=float, default=0.8)
    parser.add_argument("--max-privacy-leaks", type=int, default=0)
    parser.add_argument("--allow-non-deterministic", action="store_true")
    args = parser.parse_args(argv)

    summary = validate_results_file(
        args.result,
        source_recall_threshold=args.source_recall_threshold,
        page_recall_threshold=args.page_recall_threshold,
        max_privacy_leaks=args.max_privacy_leaks,
    )
    if not args.allow_non_deterministic:
        summary = validate_results(
            json.loads(args.result.read_text(encoding="utf-8")),
            source_recall_threshold=args.source_recall_threshold,
            page_recall_threshold=args.page_recall_threshold,
            max_privacy_leaks=args.max_privacy_leaks,
            require_deterministic=True,
        )

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
