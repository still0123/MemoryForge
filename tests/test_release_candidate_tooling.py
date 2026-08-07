from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
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
    provenance = _release_artifacts(tmp_path)
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
    (tmp_path / provenance["package"]["wheel"]).unlink()
    assert benchmark._check_reproducible_artifacts(tmp_path) == {
        "passed": False,
        "error_classification": "artifact_missing",
    }


def test_release_candidate_versions_read_both_artifact_metadata(tmp_path: Path) -> None:
    provenance = _release_artifacts(tmp_path)
    path = tmp_path / "release-provenance.json"
    path.write_text(json.dumps(provenance), encoding="utf-8")

    assert benchmark._check_versions(tmp_path)["passed"] is True
    provenance["package"] = []
    path.write_text(json.dumps(provenance), encoding="utf-8")
    assert benchmark._check_versions(tmp_path) == {
        "passed": False,
        "error_classification": "version_mismatch",
    }


def test_release_candidate_privacy_scan_rejects_paths_and_secrets() -> None:
    assert benchmark._private_detail_leaks([{"path": "dist/memoryforge.whl"}]) == 0
    assert (
        benchmark._private_detail_leaks(
            [
                {"path": "prefix:/Users/private/workspace"},
                {"detail": "api_key=not-public"},
            ]
        )
        == 2
    )
    assert benchmark._private_detail_leaks([{"api_key": "sk-live-private-value"}]) == 2


def test_release_document_claims_are_explicit() -> None:
    for path, claims in benchmark.DOCUMENT_CLAIMS.items():
        text = path.read_text(encoding="utf-8")
        assert all(claim in text for claim in claims)
        assert text.count(benchmark.RELEASE_CLAIM_MARKER) == 1
        assert not any(claim in text for claim in benchmark.FORBIDDEN_RELEASE_CLAIMS)
    texts = {path: path.read_text(encoding="utf-8") for path in benchmark.DOCUMENTS}
    texts[benchmark.DOCUMENTS[0]] += "\nConfirmation status: `passed`\n"
    assert not benchmark._document_claims_consistent(texts)


def test_release_clean_room_checks_cannot_be_not_run() -> None:
    assert benchmark._clean_room_passed(
        benchmark.WHEEL_CLEAN_ROOM_CHECKS,
        benchmark.WHEEL_CLEAN_ROOM_CHECKS,
    )
    assert not benchmark._clean_room_passed(
        {key: "not_run" for key in benchmark.SDIST_CLEAN_ROOM_CHECKS},
        benchmark.SDIST_CLEAN_ROOM_CHECKS,
    )


def _release_artifacts(root: Path) -> dict:
    wheel = root / "memoryforge-0.3.0-py3-none-any.whl"
    sdist = root / "memoryforge-0.3.0.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "memoryforge-0.3.0.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: memoryforge\nVersion: 0.3.0\n",
        )
    payload = b"Metadata-Version: 2.3\nName: memoryforge\nVersion: 0.3.0\n"
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("memoryforge-0.3.0/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    artifacts = {
        "wheel": {
            "path": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "size": wheel.stat().st_size,
        },
        "sdist": {
            "path": sdist.name,
            "sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
            "size": sdist.stat().st_size,
        },
    }
    builds = []
    for name in ("first", "second"):
        retained = root / "reproducibility" / name
        retained.mkdir(parents=True)
        retained_wheel = retained / wheel.name
        retained_sdist = retained / sdist.name
        retained_wheel.write_bytes(wheel.read_bytes())
        retained_sdist.write_bytes(sdist.read_bytes())
        builds.append(
            {
                "name": name,
                "wheel": {
                    **artifacts["wheel"],
                    "retained_path": retained_wheel.relative_to(root).as_posix(),
                },
                "sdist": {
                    **artifacts["sdist"],
                    "retained_path": retained_sdist.relative_to(root).as_posix(),
                },
            }
        )
    return {
        "memoryforge_commit": benchmark._git("rev-parse", "HEAD"),
        "memoryforge_worktree_dirty": False,
        "package": {
            "version": "0.3.0",
            "wheel": wheel.name,
            "wheel_sha256": artifacts["wheel"]["sha256"],
            "sdist": sdist.name,
            "sdist_sha256": artifacts["sdist"]["sha256"],
        },
        "builds": builds,
        "reproducible_artifacts": True,
        "checks": {
            "wheel_clean_room": dict(benchmark.WHEEL_CLEAN_ROOM_CHECKS),
            "sdist_clean_room": dict(benchmark.SDIST_CLEAN_ROOM_CHECKS),
        },
    }
