from __future__ import annotations

import http.client
import json
import sqlite3
import subprocess
import threading
from pathlib import Path

from typer.testing import CliRunner

import memoryforge.cli as cli_module
import memoryforge.portal_jobs as portal_jobs
from memoryforge.cli import app
from memoryforge.local_portal import LocalPortalApp, LocalPortalServer
from memoryforge.portal_jobs import PortalJobManager, automation_status, configure_automation
from memoryforge.workspace import Workspace


def test_start_requires_workspace_and_opens_browser_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    calls = []

    def serve(
        path: Path,
        port: int,
        *,
        open_browser: bool,
        provider: object,
        allow_local_llm: bool,
    ) -> None:
        assert provider is None
        assert allow_local_llm is False
        calls.append((path, port, open_browser))

    monkeypatch.setattr(cli_module, "serve_local_portal", serve)
    runner = CliRunner()

    missing = runner.invoke(app, ["start"])
    started = runner.invoke(app, ["start", "--workspace", str(workspace), "--port", "9876"])

    assert missing.exit_code != 0
    assert started.exit_code == 0, started.output
    assert calls == [(workspace, 9876, True)]


def test_portal_file_job_review_and_apply_uses_real_lifecycle(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    source = tmp_path / "cache.md"
    source.write_text(
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        encoding="utf-8",
    )
    portal = LocalPortalApp(workspace)
    try:
        status, _, body = portal.dispatch_post(
            "/api/sources/preview",
            {"kind": "file", "path": str(source)},
        )
        assert status == 200
        preview = json.loads(body)
        assert preview["title"] == "cache"
        assert str(tmp_path) not in body.decode()

        status, _, body = portal.dispatch_post(
            "/api/sources",
            {"kind": "file", "path": str(source)},
        )
        assert status == 202
        job_id = json.loads(body)["id"]
        imported = portal.jobs.wait(job_id)
        assert imported["status"] == "waiting_review"
        assert not list((workspace / "wiki/pages").glob("*.md"))
        sources = json.loads(portal.dispatch("/api/sources?kind=note")[2])
        source = sources["items"][0]
        assert len(source["ref"]) == 16
        assert "source_id" not in source
        detail = json.loads(portal.dispatch(f"/api/source?ref={source['ref']}")[2])
        assert len(detail["source_id"]) == 64
        assert str(tmp_path) not in json.dumps(detail)

        updates = json.loads(portal.dispatch("/api/updates")[2])["items"]
        assert len(updates) == 1
        update_id = updates[0]["id"]
        detail = json.loads(portal.dispatch(f"/api/updates/{update_id}")[2])
        assert detail["pages"]
        assert str(tmp_path) not in json.dumps(detail)
        assert all(len(item["name"]) < 160 for item in detail["sources"])

        status, _, body = portal.dispatch_post(
            f"/api/updates/{update_id}/approve-and-apply",
            {},
        )
        assert status == 202
        applied = portal.jobs.wait(json.loads(body)["id"])
        assert applied["status"] == "completed"
        assert list((workspace / "wiki/pages").glob("*.md"))
        archived = workspace / ".memoryforge/staging/applied" / update_id
        review = json.loads((archived / "review.json").read_text(encoding="utf-8"))
        assert review["review_mode"] == "displayed"
        assert (archived / "approval.json").is_file()
    finally:
        portal.close()


def test_portal_same_file_version_is_noop(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nStable knowledge.\n", encoding="utf-8")
    portal = LocalPortalApp(workspace)
    try:
        first = json.loads(
            portal.dispatch_post("/api/sources", {"kind": "file", "path": str(source)})[2]
        )
        first_job = portal.jobs.wait(first["id"])
        update_id = first_job["changeset_ids"][0]
        apply_job = json.loads(
            portal.dispatch_post(f"/api/updates/{update_id}/approve-and-apply", {})[2]
        )
        assert portal.jobs.wait(apply_job["id"])["status"] == "completed"

        second = json.loads(
            portal.dispatch_post("/api/sources", {"kind": "file", "path": str(source)})[2]
        )
        repeated = portal.jobs.wait(second["id"])
        assert repeated["status"] == "completed"
        assert repeated["changeset_ids"] == []
        assert repeated["message"] == "来源已是最新版本。"
    finally:
        portal.close()


def test_portal_reject_keeps_raw_source_and_does_not_apply(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    source = tmp_path / "reject.md"
    source.write_text("# Reject\n\nKeep raw evidence.\n", encoding="utf-8")
    portal = LocalPortalApp(workspace)
    try:
        submitted = json.loads(
            portal.dispatch_post("/api/sources", {"kind": "file", "path": str(source)})[2]
        )
        job = portal.jobs.wait(submitted["id"])
        update_id = job["changeset_ids"][0]

        status, _, _ = portal.dispatch_post(f"/api/updates/{update_id}/reject", {})

        assert status == 200
        assert not list((workspace / "wiki/pages").glob("*.md"))
        assert list((workspace / "raw/blobs").rglob("*"))
        assert (workspace / ".memoryforge/staging/rejected" / update_id).is_dir()
    finally:
        portal.close()


def test_portal_repository_and_codex_sources_stay_private_by_default(
    tmp_path: Path,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    checkout = _repository(tmp_path / "repository")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "portal-thread"}})
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Which cache did we choose?"}],
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "We chose SQLite."}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    portal = LocalPortalApp(workspace)
    try:
        repository_preview = json.loads(
            portal.dispatch_post(
                "/api/sources/preview",
                {"kind": "repository", "path": str(checkout)},
            )[2]
        )
        assert repository_preview["languages"] == ["Python"]
        assert repository_preview["branch"]
        assert str(checkout) not in json.dumps(repository_preview)
        repository_job = json.loads(
            portal.dispatch_post(
                "/api/sources",
                {"kind": "repository", "path": str(checkout)},
            )[2]
        )
        assert portal.jobs.wait(repository_job["id"], timeout=20)["status"] == "waiting_review"

        codex_job = json.loads(
            portal.dispatch_post(
                "/api/sources",
                {"kind": "codex", "path": str(rollout)},
            )[2]
        )
        assert portal.jobs.wait(codex_job["id"])["status"] == "waiting_review"
        with sqlite3.connect(workspace / ".memoryforge/index.sqlite") as connection:
            sensitivity, tags = connection.execute(
                """
                SELECT sensitivity, tags_json
                FROM source_versions
                WHERE is_current = 1 AND tags_json LIKE '%"platform:codex"%'
                """
            ).fetchone()
        assert sensitivity == "local_only"
        assert "conversation" in json.loads(tags)
    finally:
        portal.close()


def test_portal_post_requires_same_origin_json_and_csrf(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nSafe content.\n", encoding="utf-8")
    server = LocalPortalServer(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        host = f"127.0.0.1:{port}"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/session", headers={"Host": host})
        session = json.loads(connection.getresponse().read())
        body = json.dumps({"kind": "file", "path": str(source)})

        connection.request(
            "POST",
            "/api/sources/preview",
            body=body,
            headers={
                "Host": host,
                "Origin": f"http://{host}",
                "Content-Type": "application/json",
            },
        )
        missing_csrf = connection.getresponse()
        assert missing_csrf.status == 403
        missing_csrf.read()

        connection.request(
            "POST",
            "/api/sources/preview",
            body=body,
            headers={
                "Host": host,
                "Origin": "http://attacker.example",
                "Content-Type": "application/json",
                "X-MemoryForge-CSRF": session["csrf_token"],
            },
        )
        cross_site = connection.getresponse()
        assert cross_site.status == 403
        cross_site.read()

        connection.request(
            "POST",
            "/api/sources/preview",
            body=body,
            headers={
                "Host": host,
                "Origin": f"http://{host}",
                "Content-Type": "application/json",
                "X-MemoryForge-CSRF": session["csrf_token"],
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert str(tmp_path) not in response.read().decode()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_uploaded_file_uses_controlled_temporary_storage(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    manager = PortalJobManager(workspace)
    try:
        submitted = manager.submit_upload(
            "uploaded.md",
            b"# Uploaded\n\nSmall browser upload.\n",
            "file",
        )
        assert submitted["name"] == "uploaded"
        job = manager.wait(submitted["id"])
        assert job["status"] == "waiting_review"
        uploads = manager.state_dir / "uploads"
        assert not list(uploads.glob("upload_*"))
    finally:
        manager.close()


def test_running_job_becomes_retryable_failure_after_restart(tmp_path: Path) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nRecovery fixture.\n", encoding="utf-8")
    manager = PortalJobManager(workspace)
    submitted = manager.submit_source({"kind": "file", "path": str(source)})
    manager.wait(submitted["id"])
    manager.close()

    path = manager.jobs_dir / f"{submitted['id']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "running"
    payload["finished_at"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")

    recovered = PortalJobManager(workspace)
    try:
        job = recovered.get(submitted["id"])
        assert job["status"] == "failed"
        assert job["error_code"] == "service_restarted"
        assert job["retryable"] is True
    finally:
        recovered.close()


def test_retry_after_compile_failure_reuses_imported_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    source = tmp_path / "retry.md"
    source.write_text("# Retry\n\nCompile this source.\n", encoding="utf-8")
    original = portal_jobs._stage_pending
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("compile failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(portal_jobs, "_stage_pending", fail_once)
    manager = PortalJobManager(workspace)
    try:
        first = manager.submit_source({"kind": "file", "path": str(source)})
        assert manager.wait(first["id"])["status"] == "failed"

        second = manager.submit_source({"kind": "file", "path": str(source)})
        retried = manager.wait(second["id"])
        assert retried["status"] == "waiting_review"
        assert retried["changeset_ids"]
    finally:
        manager.close()


def test_automation_owns_one_launchd_file_and_preserves_other_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = Workspace.initialize(tmp_path / "workspace").root
    home = tmp_path / "home"
    launch_agents = home / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True)
    existing = launch_agents / "com.example.keep.plist"
    existing.write_text("keep", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(portal_jobs.sys, "platform", "darwin")
    monkeypatch.setattr(portal_jobs.shutil, "which", lambda _name: "/usr/local/bin/memoryforge")
    monkeypatch.setattr(
        portal_jobs.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"", b""),
    )

    enabled = configure_automation(
        workspace,
        {"enabled": True, "interval_minutes": 15, "types": ["code", "codex"]},
    )

    assert enabled["enabled"] is True
    assert enabled["types"] == ["code", "codex"]
    assert existing.read_text(encoding="utf-8") == "keep"
    assert len(list(launch_agents.glob("com.memoryforge.portal.*.plist"))) == 1

    disabled = configure_automation(
        workspace,
        {"enabled": False, "interval_minutes": 15, "types": ["code", "codex"]},
    )
    assert disabled["enabled"] is False
    assert not list(launch_agents.glob("com.memoryforge.portal.*.plist"))
    assert automation_status(workspace)["enabled"] is False


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("# Service\n\nRepository fixture.\n", encoding="utf-8")
    source = path / "src/service.py"
    source.parent.mkdir()
    source.write_text("def run() -> str:\n    return 'ok'\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "Add service")
    return path


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
