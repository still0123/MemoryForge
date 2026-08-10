from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEVELOPMENT = ROOT / "demo/evaluation/release_candidate_development.json"
CONFIRMATION = ROOT / "demo/evaluation/release_candidate_confirmation.json"
HOLDOUT = ROOT / "demo/evaluation/release_candidate_holdout.json"
FROZEN_SHA256 = {
    DEVELOPMENT: "8d9fe33359b71ac0b86b6fa42b0bc5ee34080126af3ae0b9bf2fa2c0121cf2a6",
    CONFIRMATION: "3eed08e04592b614a709cd18c6a92951b5da145d7dab4b9997824cd4e002e805",
    HOLDOUT: "b466644bef2748a96d598540c54633fe49770af97fc71ce6e62e18eda687e8f8",
}


def test_release_candidate_inputs_are_frozen_and_closed() -> None:
    payloads = {}
    for path, expected_sha256 in FROZEN_SHA256.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
        payloads[path] = json.loads(path.read_text(encoding="utf-8"))

    development = payloads[DEVELOPMENT]
    confirmation = payloads[CONFIRMATION]
    holdout = payloads[HOLDOUT]
    assert {(payload["suite_id"], payload["suite_revision"]) for payload in payloads.values()} == {
        ("release-candidate-delivery", 1)
    }
    assert development["split"] == "development"
    assert len(development["cases"]) == 6
    assert confirmation["split"] == "confirmation"
    assert confirmation["status"] == "not_run"
    assert len(confirmation["components"]) == 7
    assert sum(component["case_count"] for component in confirmation["components"]) == 26
    assert holdout["split"] == "holdout"
    assert holdout["status"] == "not_run"
    assert len(holdout["cases"]) == 5
    assert not (ROOT / "demo/results/release_candidate_confirmation.json").exists()
    assert not (ROOT / "demo/results/release_candidate_holdout.json").exists()


def test_release_confirmation_component_hashes_are_exact() -> None:
    confirmation = json.loads(CONFIRMATION.read_text(encoding="utf-8"))

    for component in confirmation["components"]:
        path = ROOT / component["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == component["sha256"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["cases"]
        assert len(cases) == component["case_count"]


def test_release_candidate_keeps_hosted_workflows_disabled() -> None:
    workflows = ROOT / ".github/workflows"

    assert not workflows.exists() or not [
        *workflows.glob("*.yml"),
        *workflows.glob("*.yaml"),
    ]
