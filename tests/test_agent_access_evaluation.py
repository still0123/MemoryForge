from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "demo" / "run_agent_access_evaluation.py"
_spec = importlib.util.spec_from_file_location("run_agent_access_evaluation", _SCRIPT)
assert _spec and _spec.loader
run_agent_access_evaluation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_agent_access_evaluation)


def test_agent_access_frozen_evaluation_passes_all_hard_gates(tmp_path: Path) -> None:
    """Spec §16: the frozen, model-free Agent Access suite must pass every gate."""
    output = tmp_path / "evidence.json"
    run_agent_access_evaluation.main(["--workdir", str(tmp_path / "run"), "--output", str(output)])

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["gates"]["all_pass"] is True
    metrics = evidence["metrics"]
    assert metrics["answer_accuracy"] == 1.0
    assert metrics["abstention_accuracy"] == 1.0
    assert metrics["citation_grounding"] == 1.0
    assert metrics["applied_only_rate"] == 1.0
    assert metrics["repository_isolation"] == 1.0
    assert metrics["local_scope_leak_count"] == 0
    assert metrics["evidence_read_rate"] == 1.0
    assert metrics["proposal_safety_rate"] == 1.0
    assert metrics["context_p95_characters"] <= 8000
    assert evidence["baseline"]["accuracy"] == 1.0
    assert evidence["baseline"]["accuracy"] <= metrics["answer_accuracy"]
    # 8 context cases + unregistered project + expired citation + grounded proposal.
    assert len(evidence["scenarios"]) == 11
