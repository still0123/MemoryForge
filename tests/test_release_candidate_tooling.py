from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "demo/run_release_candidate_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("run_release_candidate_benchmark", _SCRIPT)
assert _SPEC and _SPEC.loader
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def test_release_candidate_runner_binds_frozen_inputs() -> None:
    development, confirmation, holdout = benchmark._load_frozen_inputs()

    assert development["split"] == "development"
    assert confirmation["status"] == "not_run"
    assert holdout["status"] == "not_run"
    assert [case["id"] for case in development["cases"]] == [
        "package-version-consistency",
        "registry-and-benchmark-summary",
        "local-reproducible-artifacts",
        "workspace-release-drill",
        "release-document-consistency",
        "frozen-splits-remain-closed",
    ]


def test_release_candidate_reproducibility_requires_two_matching_builds(
    tmp_path: Path,
) -> None:
    provenance = {
        "builds": [
            {
                "wheel": {"sha256": "1" * 64},
                "sdist": {"sha256": "2" * 64},
            },
            {
                "wheel": {"sha256": "1" * 64},
                "sdist": {"sha256": "2" * 64},
            },
        ],
        "reproducible_artifacts": True,
    }
    (tmp_path / "release-provenance.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )

    assert benchmark._check_reproducible_artifacts(tmp_path)["passed"] is True
    provenance["builds"][1]["sdist"]["sha256"] = "3" * 64
    (tmp_path / "release-provenance.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    assert benchmark._check_reproducible_artifacts(tmp_path) == {
        "passed": False,
        "error_classification": "artifact_reproducibility_failure",
    }


def test_release_candidate_privacy_scan_rejects_paths_and_secrets() -> None:
    assert benchmark._private_detail_leaks([{"path": "dist/memoryforge.whl"}]) == 0
    assert (
        benchmark._private_detail_leaks(
            [
                {"path": "/Users/private/workspace"},
                {"detail": "api_key=not-public"},
            ]
        )
        == 2
    )
