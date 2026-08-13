"""Persistent serial jobs for the local Portal control plane."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import queue
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from memoryforge.changesets import ChangeSetStore
from memoryforge.code_index import build_code_index
from memoryforge.code_wiki_compiler import compile_code_wiki
from memoryforge.codex_adapter import CodexImportError, import_codex_rollout
from memoryforge.compiler import Compilation, compile_pending_sources
from memoryforge.errors import ChangeSetStoreError
from memoryforge.feishu_adapter import (
    import_feishu_document,
    preview_feishu_document,
    refresh_feishu_documents,
)
from memoryforge.folder_adapter import sync_folder
from memoryforge.git_adapter import (
    GitRepositoryError,
    scan_git_snapshot_code,
    snapshot_git_repository,
)
from memoryforge.github_thread_adapter import import_github_thread, parse_github_thread_url
from memoryforge.importer import import_local_file, validate_source_path
from memoryforge.lifecycle import apply_changeset
from memoryforge.models import ImportResult, Sensitivity
from memoryforge.module_planner import build_module_plan
from memoryforge.provider import OpenAICompatibleProvider
from memoryforge.web_adapter import _normalise_url, import_web_page
from memoryforge.workspace import (
    Workspace,
    _connect_readonly,
    list_git_checkouts,
    list_git_code_modules,
    register_git_checkout,
    register_git_code_module,
    sync_git_checkout,
)

_JOB_ID = re.compile(r"^job_[a-f0-9]{24}$")
_SUPPORTED_FILES = frozenset({".md", ".markdown", ".txt"})
_CODE_SUFFIXES = {".go": "Go", ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript"}
_AUTOMATION_TYPES = {"code", "codex", "feishu"}
_ACTIVE_STATUSES = {"queued", "running"}
_FINAL_STATUSES = {"waiting_review", "completed", "failed", "cancelled"}
_GIT_CLONE_TIMEOUT_SECONDS = 120


class PortalJobManager:
    """One durable queue and one worker for one Workspace."""

    def __init__(
        self,
        workspace: Path,
        *,
        provider: OpenAICompatibleProvider | None = None,
        allow_local_llm: bool = False,
    ) -> None:
        self.workspace = Workspace.open_readonly(workspace).root
        self.provider = provider
        self.allow_local_llm = allow_local_llm
        self.state_dir = self.workspace / ".memoryforge" / "traces" / "portal"
        self.jobs_dir = self.state_dir / "jobs"
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._state_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._choices: dict[str, Path] = {}
        self._closed = False
        self._recover()

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = _required_string(payload, "kind")
        if kind == "codex_scan":
            sessions = []
            imported = _imported_codex_threads(Workspace.open_readonly(self.workspace))
            for path in _codex_rollouts():
                try:
                    thread = _codex_thread(path)
                    item = _codex_preview(path)
                except (CodexImportError, OSError, UnicodeDecodeError):
                    continue
                if thread is None or thread in imported:
                    continue
                choice_id = "session_" + hashlib.sha256(os.fsencode(path)).hexdigest()[:20]
                self._choices[choice_id] = path
                sessions.append({**item, "selection_id": choice_id})
            return {"kind": kind, "sessions": sessions[:100]}
        return _preview_payload(kind, payload)

    def submit_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        kind = _required_string(normalized, "kind")
        if kind == "codex" and "selection_id" in normalized:
            selection_id = _required_string(normalized, "selection_id")
            selected = self._choices.get(selection_id)
            if selected is None:
                raise ValueError("preview the Codex session again")
            normalized["path"] = str(selected)
        preview = _preview_payload(kind, normalized)
        public = bool(normalized.get("public", False))
        if public and not bool(normalized.get("confirm_public", False)):
            raise ValueError("public sources require explicit confirmation")
        if kind == "codex":
            public = False
        normalized["public"] = public
        job = self._new_job(
            name=str(preview["title"]),
            kind="import",
            payload=normalized,
        )
        return _public_job(job)

    def submit_upload(
        self,
        filename: str,
        content: bytes,
        kind: str,
        *,
        public: bool = False,
        confirm_public: bool = False,
    ) -> dict[str, Any]:
        name = Path(filename).name
        suffix = Path(name).suffix.lower()
        allowed = {".jsonl", ".md", ".markdown", ".txt"} if kind == "codex" else _SUPPORTED_FILES
        if kind not in {"file", "codex"} or suffix not in allowed:
            raise ValueError("unsupported upload")
        Workspace.open(self.workspace)
        uploads = self.state_dir / "uploads"
        _ensure_state_directory(self.workspace, uploads)
        path = uploads / f"upload_{secrets.token_hex(12)}{suffix}"
        path.write_bytes(content)
        path.chmod(0o600)
        return self.submit_source(
            {
                "kind": kind,
                "path": str(path),
                "uploaded": True,
                "title": Path(name).stem,
                "public": public,
                "confirm_public": confirm_public,
            }
        )

    def submit_refresh(self, source_id: str) -> dict[str, Any]:
        if re.fullmatch(r"[a-f0-9]{64}", source_id) is None:
            raise ValueError("invalid source")
        name = _source_name(self.workspace, source_id)
        job = self._new_job(
            name=f"刷新 {name}",
            kind="refresh",
            payload={"source_id": source_id},
        )
        return _public_job(job)

    def submit_apply(self, changeset_id: str, name: str) -> dict[str, Any]:
        job = self._new_job(
            name=f"应用 {name}",
            kind="apply",
            payload={"changeset_id": changeset_id},
        )
        return _public_job(job)

    def list_jobs(self) -> dict[str, Any]:
        jobs = [_public_job(job) for job in self._read_all()]
        return {"items": jobs}

    def get(self, job_id: str) -> dict[str, Any]:
        return _public_job(self._read(job_id), detail=True)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._state_lock:
            job = self._read(job_id)
            if job["status"] != "queued":
                raise ValueError("only queued jobs can be cancelled safely")
            job.update(
                status="cancelled",
                stage="已取消",
                finished_at=_now(),
                message="任务已取消。",
                retryable=False,
            )
            self._write(job)
        return _public_job(job)

    def wait(self, job_id: str, timeout: float = 10) -> dict[str, Any]:
        deadline = datetime.now(UTC) + timedelta(seconds=timeout)
        while datetime.now(UTC) < deadline:
            job = self._read(job_id)
            if job["status"] in _FINAL_STATUSES:
                return _public_job(job, detail=True)
            threading.Event().wait(0.02)
        raise TimeoutError("job did not finish")

    def close(self) -> None:
        self._closed = True
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout=5)

    def _new_job(self, *, name: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("job manager is closed")
        job = {
            "id": "job_" + secrets.token_hex(12),
            "name": name[:160],
            "type": kind,
            "status": "queued",
            "stage": "等待处理",
            "progress": 0,
            "message": "任务已进入本地队列。",
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "error_code": None,
            "retryable": False,
            "changeset_ids": [],
            "workspace_commit": Workspace.open_readonly(self.workspace).current_commit(),
            "payload": payload,
            "result": {},
        }
        with self._state_lock:
            self._write(job)
            self._queue.put(str(job["id"]))
            self._ensure_worker()
        return job

    def _ensure_worker(self) -> None:
        if self._closed:
            raise RuntimeError("job manager is closed")
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None or self._closed:
                return
            assert isinstance(job_id, str)
            with self._state_lock:
                job = self._read(job_id)
                if job["status"] != "queued":
                    continue
                job.update(
                    status="running",
                    stage="校验来源",
                    progress=5,
                    started_at=_now(),
                    message="正在校验本地输入。",
                )
                self._write(job)

            try:
                result = _execute_job(
                    self.workspace,
                    job,
                    partial(self._record_progress, job_id),
                    provider=self.provider,
                    allow_local_llm=self.allow_local_llm,
                )
            except Exception as exc:
                with self._state_lock:
                    current = self._read(job_id)
                    current.update(
                        status="failed",
                        stage="失败",
                        progress=100,
                        finished_at=_now(),
                        error_code=_error_code(exc),
                        message=_safe_failure_message(str(job["type"])),
                        retryable=True,
                    )
                    self._write(current)
                continue
            with self._state_lock:
                current = self._read(job_id)
                changesets = list(result.get("changeset_ids", []))
                current.update(
                    status="waiting_review" if changesets else "completed",
                    stage="等待审核" if changesets else "完成",
                    progress=100,
                    finished_at=_now(),
                    message=(
                        "知识更新已生成，等待审核。"
                        if changesets
                        else str(result.get("message", "任务已完成。"))
                    ),
                    retryable=False,
                    changeset_ids=changesets,
                    workspace_commit=Workspace.open_readonly(self.workspace).current_commit(),
                    result=_public_result(result),
                )
                self._write(current)

    def _record_progress(self, job_id: str, stage: str, value: int, message: str) -> None:
        with self._state_lock:
            current = self._read(job_id)
            current.update(stage=stage, progress=value, message=message)
            self._write(current)

    def _recover(self) -> None:
        if not self.jobs_dir.is_dir() or self.jobs_dir.is_symlink():
            return
        queued = []
        for job in self._read_all():
            if job["status"] == "running":
                job.update(
                    status="failed",
                    stage="服务已重启",
                    progress=100,
                    finished_at=_now(),
                    error_code="service_restarted",
                    message="服务重启中断任务，可以重新提交。",
                    retryable=True,
                )
                self._write(job)
            elif job["status"] == "queued":
                queued.append(str(job["id"]))
        for job_id in queued:
            self._queue.put(job_id)
        if queued:
            self._ensure_worker()

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.jobs_dir.is_dir() or self.jobs_dir.is_symlink():
            return []
        jobs = [
            self._read(path.stem)
            for path in self.jobs_dir.glob("job_*.json")
            if path.is_file() and not path.is_symlink()
        ]
        return sorted(jobs, key=lambda item: str(item["created_at"]), reverse=True)

    def _read(self, job_id: str) -> dict[str, Any]:
        if _JOB_ID.fullmatch(job_id) is None:
            raise ValueError("invalid job")
        path = self.jobs_dir / f"{job_id}.json"
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("job not found")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("id") != job_id:
            raise ValueError("invalid job")
        return value

    def _write(self, job: dict[str, Any]) -> None:
        self._ensure_jobs_dir()
        job_id = str(job["id"])
        if _JOB_ID.fullmatch(job_id) is None:
            raise ValueError("invalid job")
        target = self.jobs_dir / f"{job_id}.json"
        temporary = self.jobs_dir / f".{job_id}.{secrets.token_hex(6)}.tmp"
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, target)

    def _ensure_jobs_dir(self) -> None:
        Workspace.open(self.workspace)
        _ensure_state_directory(self.workspace, self.jobs_dir)


def _execute_job(
    workspace: Path,
    job: dict[str, Any],
    progress: Callable[[str, int, str], None],
    *,
    provider: OpenAICompatibleProvider | None = None,
    allow_local_llm: bool = False,
) -> dict[str, Any]:
    kind = str(job["type"])
    payload = job["payload"]
    if not isinstance(payload, dict):
        raise ValueError("invalid job payload")
    if kind == "import":
        try:
            return _execute_import(
                workspace,
                payload,
                progress,
                provider=provider,
                allow_local_llm=allow_local_llm,
            )
        finally:
            if payload.get("uploaded") is True and isinstance(payload.get("path"), str):
                Path(payload["path"]).unlink(missing_ok=True)
    if kind == "refresh":
        return _execute_refresh(
            workspace,
            _required_string(payload, "source_id"),
            progress,
            provider=provider,
            allow_local_llm=allow_local_llm,
        )
    if kind == "apply":
        progress("记录审核", 25, "正在记录审核和批准凭据。")
        result = apply_changeset(
            workspace,
            _required_string(payload, "changeset_id"),
            review_mode="displayed",
        )
        progress("校验结果", 90, "Wiki 已应用并完成 lint。")
        return {"message": "知识更新已应用。", "commit": result["commit"]}
    raise ValueError("unsupported job")


def _execute_import(
    workspace: Path,
    payload: dict[str, Any],
    progress: Callable[[str, int, str], None],
    *,
    provider: OpenAICompatibleProvider | None = None,
    allow_local_llm: bool = False,
) -> dict[str, Any]:
    kind = _required_string(payload, "kind")
    sensitivity = Sensitivity.PUBLIC if bool(payload.get("public")) else Sensitivity.LOCAL_ONLY
    source_ids: list[str] = []
    changed = False
    progress("导入来源", 25, "正在写入不可变 SourceVersion。")

    if kind in {"repository", "repository_url"}:
        checkout = (
            _clone_git_repository(workspace, _required_string(payload, "url"), progress)
            if kind == "repository_url"
            else Path(_required_string(payload, "path")).expanduser()
        )
        opened = Workspace.open(workspace)
        with opened.exclusive_lock():
            progress("扫描仓库", 30, "正在读取仓库文档和代码索引。")
            repository = register_git_checkout(workspace, checkout, sensitivity=sensitivity)
            snapshot = snapshot_git_repository(checkout)
            has_code = bool(scan_git_snapshot_code(snapshot, (".",), sensitivity=sensitivity))
            if has_code:
                register_git_code_module(workspace, repository.repository_id, ".")
            progress("导入仓库内容", 45, "正在写入不可变 SourceVersion。")
            synced = sync_git_checkout(workspace, repository.repository_id)
            source_ids.extend(document.source_id for document in synced.documents)
            changed = any(
                document.status in {"created", "updated"} for document in synced.documents
            )
            _repository_stage_progress(progress, provider, has_code)
            changesets = _stage_repository(
                opened,
                repository.repository_id,
                tuple(source_ids),
                has_code=has_code,
                provider=provider,
                allow_local_llm=allow_local_llm,
            )
        return _job_result(changed, changesets)

    if kind == "folder":
        folder_sync = sync_folder(
            workspace,
            Path(_required_string(payload, "path")),
            sensitivity=sensitivity,
        )
        source_ids.extend(document.source_id for document in folder_sync.documents)
        changed = any(
            document.status in {"created", "updated"} for document in folder_sync.documents
        ) or bool(folder_sync.deleted)
    else:
        opened = Workspace.open(workspace)
        with opened.exclusive_lock():
            imported: tuple[ImportResult, ...]
            if kind == "file":
                path = Path(_required_string(payload, "path")).expanduser()
                imported = (
                    import_local_file(
                        workspace,
                        path,
                        source_root=path.parent,
                        sensitivity=sensitivity,
                    ),
                )
            elif kind == "codex":
                path = Path(_required_string(payload, "path"))
                if path.suffix.lower() == ".jsonl":
                    imported = (
                        import_codex_rollout(
                            workspace,
                            path,
                            title=_optional_string(payload, "title"),
                        ),
                    )
                else:
                    imported = (
                        import_local_file(
                            workspace,
                            path,
                            source_root=path.parent,
                            tags=("conversation", "platform:codex", "unverified"),
                            sensitivity=Sensitivity.LOCAL_ONLY,
                        ),
                    )
            elif kind == "feishu":
                imported = import_feishu_document(
                    workspace,
                    _required_string(payload, "document"),
                )
            elif kind == "web":
                imported = (
                    import_web_page(
                        workspace,
                        _required_string(payload, "url"),
                        sensitivity=sensitivity,
                    ),
                )
            elif kind == "github":
                imported = (
                    import_github_thread(
                        workspace,
                        _required_string(payload, "url"),
                        sensitivity=sensitivity,
                    ),
                )
            else:
                raise ValueError("unsupported source kind")
            source_ids.extend(item.source_id for item in imported)
            changed = any(item.status != "unchanged" for item in imported)

    _stage_progress(progress, provider)
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        changesets = _stage_pending(
            opened,
            tuple(source_ids),
            provider=provider,
            allow_local_llm=allow_local_llm,
        )
    return _job_result(changed, changesets)


def _execute_refresh(
    workspace: Path,
    source_id: str,
    progress: Callable[[str, int, str], None],
    *,
    provider: OpenAICompatibleProvider | None = None,
    allow_local_llm: bool = False,
) -> dict[str, Any]:
    progress("刷新来源", 25, "正在读取来源当前版本。")
    with _connect_readonly(Workspace.open_readonly(workspace).index_path) as connection:
        row = connection.execute(
            """
            SELECT sources.source_path, versions.tags_json, revisions.repository_id
            FROM sources
            JOIN source_versions AS versions
              ON versions.source_id = sources.id AND versions.is_current = 1
            LEFT JOIN git_source_revisions AS revisions
              ON revisions.source_version_id = versions.id
            WHERE sources.source_id = ?
            """,
            (source_id,),
        ).fetchone()
    if row is None:
        raise ValueError("unknown source")
    repository_id = str(row["repository_id"]) if row["repository_id"] is not None else None
    if repository_id is None:
        source_path = str(row["source_path"])
        parts = Path(source_path).parts
        if len(parts) >= 2 and parts[0] == "feishu":
            document_id = parts[1].removesuffix(".md")
            opened = Workspace.open(workspace)
            with opened.exclusive_lock():
                imported = import_feishu_document(workspace, document_id)
                changed_ids = tuple(item.source_id for item in imported)
                changed = any(item.status in {"created", "updated"} for item in imported)
                _stage_progress(progress, provider)
                changesets = _stage_pending(
                    opened,
                    changed_ids,
                    provider=provider,
                    allow_local_llm=allow_local_llm,
                )
            return _job_result(changed, changesets)
        raise ValueError("this source cannot be refreshed without selecting it again")
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        synced = sync_git_checkout(workspace, repository_id)
        changed_ids = tuple(document.source_id for document in synced.documents)
        changed = any(document.status in {"created", "updated"} for document in synced.documents)
        has_code = bool(list_git_code_modules(opened, repository_id))
        _repository_stage_progress(progress, provider, has_code)
        changesets = _stage_repository(
            opened,
            repository_id,
            changed_ids,
            has_code=has_code,
            provider=provider,
            allow_local_llm=allow_local_llm,
        )
    return _job_result(changed, changesets)


def _stage_pending(
    opened: Workspace,
    source_ids: tuple[str, ...],
    *,
    provider: OpenAICompatibleProvider | None = None,
    allow_local_llm: bool = False,
) -> list[str]:
    if not source_ids:
        return []
    compilation = compile_pending_sources(
        opened,
        source_ids=source_ids,
        provider=provider,
        allow_local=allow_local_llm,
    )
    return [_stage(opened, compilation)] if compilation is not None else []


def _stage_repository(
    opened: Workspace,
    repository_id: str,
    source_ids: tuple[str, ...],
    *,
    has_code: bool,
    provider: OpenAICompatibleProvider | None = None,
    allow_local_llm: bool = False,
) -> list[str]:
    changesets: list[str] = []
    if has_code:
        snapshot = build_code_index(opened, repository_id)
        compilation = compile_code_wiki(
            opened,
            snapshot,
            build_module_plan(snapshot),
            provider=provider,
            allow_local=allow_local_llm,
        )
        if compilation is not None:
            changesets.append(_stage(opened, compilation))
    changesets.extend(
        _stage_pending(
            opened,
            _non_code_source_ids(opened, source_ids),
            provider=provider,
            allow_local_llm=allow_local_llm,
        )
    )
    return changesets


def _non_code_source_ids(opened: Workspace, source_ids: tuple[str, ...]) -> tuple[str, ...]:
    selected = set(source_ids)
    if not selected:
        return ()
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            """
            SELECT sources.source_id, versions.tags_json
            FROM sources
            JOIN source_versions AS versions ON versions.source_id = sources.id
            WHERE versions.is_current = 1
            """
        ).fetchall()
    return tuple(
        str(row["source_id"])
        for row in rows
        if str(row["source_id"]) in selected
        and not ({"code", "code-module"} & set(json.loads(str(row["tags_json"]))))
    )


def _stage_progress(
    progress: Callable[[str, int, str], None],
    provider: OpenAICompatibleProvider | None,
) -> None:
    if provider is None:
        progress("生成知识更新", 65, "正在生成可审核 ChangeSet。")
    else:
        progress("模型整理", 65, "模型正在生成可审核 ChangeSet。")


def _repository_stage_progress(
    progress: Callable[[str, int, str], None],
    provider: OpenAICompatibleProvider | None,
    has_code: bool,
) -> None:
    if not has_code:
        _stage_progress(progress, provider)
    elif provider is None:
        progress("生成代码 Wiki", 65, "正在按代码结构生成可审核 ChangeSet。")
    else:
        progress("模型整理代码 Wiki", 65, "正在生成代码 Wiki 和模块叙事。")


def _stage_all_pending(opened: Workspace) -> list[str]:
    compilation = compile_pending_sources(opened)
    return [_stage(opened, compilation)] if compilation is not None else []


def _stage(opened: Workspace, compilation: Compilation) -> str:
    store = ChangeSetStore(opened)
    try:
        stored = store.get(compilation.changeset.changeset_id)
    except ChangeSetStoreError:
        stored = store.create(compilation.changeset, compilation.candidate_files)
    return stored.changeset.changeset_id


def _job_result(changed: bool, changesets: list[str]) -> dict[str, Any]:
    return {
        "message": "来源已是最新版本。" if not changed else "来源处理完成。",
        "changeset_ids": list(dict.fromkeys(changesets)),
    }


def _preview_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "repository":
        snapshot = snapshot_git_repository(Path(_required_string(payload, "path")))
        code = scan_git_snapshot_code(snapshot, (".",))
        languages = sorted(
            {
                language
                for document in code
                if (language := _CODE_SUFFIXES.get(Path(document.source_path).suffix.lower()))
            }
        )
        return {
            "kind": kind,
            "title": snapshot.repository_root.name,
            "commit": snapshot.revision[:12],
            "branch": snapshot.branch or "detached",
            "languages": languages,
            "privacy": "local_only",
        }
    if kind == "repository_url":
        url = _normalise_git_repository_url(_required_string(payload, "url"))
        return {
            "kind": kind,
            "title": Path(urlsplit(url).path).stem or "Git 仓库",
            "host": urlsplit(url).hostname,
            "download": "确认后下载到本机并编译为待审核 Wiki",
            "privacy": "local_only",
        }
    if kind == "codex":
        path = Path(_required_string(payload, "path")).expanduser()
        if path.suffix.lower() == ".jsonl":
            return {"kind": kind, **_codex_preview(path)}
        validated = validate_source_path(path, source_root=path.parent)
        return {
            "kind": kind,
            "title": _optional_string(payload, "title") or validated.stem,
            "platform": "codex",
            "updated": datetime.fromtimestamp(validated.stat().st_mtime, tz=UTC).isoformat(),
            "size_bytes": validated.stat().st_size,
            "privacy": "local_only",
        }
    if kind == "file":
        path = Path(_required_string(payload, "path")).expanduser()
        validated = validate_source_path(path, source_root=path.parent)
        return {
            "kind": kind,
            "title": _optional_string(payload, "title") or validated.stem,
            "size_bytes": validated.stat().st_size,
            "privacy": "local_only",
        }
    if kind == "folder":
        path = Path(_required_string(payload, "path")).expanduser()
        if path.is_symlink() or not path.resolve(strict=True).is_dir():
            raise ValueError("folder must be a real directory")
        count = _count_supported_files(path.resolve())
        return {
            "kind": kind,
            "title": path.resolve().name,
            "file_count": count,
            "privacy": "local_only",
        }
    if kind == "feishu":
        document = _required_string(payload, "document")
        authorized = shutil.which("lark-cli") is not None
        return {
            "kind": kind,
            "title": preview_feishu_document(document) if authorized else "飞书文档",
            "authorized": authorized,
            "auth_command": "lark-cli auth login" if not authorized else None,
            "privacy": "local_only",
        }
    if kind == "web":
        url = _normalise_url(_required_string(payload, "url"))
        return {
            "kind": kind,
            "title": urlsplit(url).hostname or "网页",
            "host": urlsplit(url).hostname,
            "privacy": "local_only",
        }
    if kind == "github":
        identity = parse_github_thread_url(_required_string(payload, "url"))
        return {
            "kind": kind,
            "title": f"{identity.owner}/{identity.repository} #{identity.number}",
            "privacy": "local_only",
        }
    raise ValueError("unsupported source kind")


def _clone_git_repository(
    workspace: Path,
    url: str,
    progress: Callable[[str, int, str], None],
) -> Path:
    normalized = _normalise_git_repository_url(url)
    opened = Workspace.open(workspace)
    checkouts = opened.internal_dir / "checkouts"
    _ensure_state_directory(opened.root, checkouts)
    checkout = checkouts / hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    if checkout.is_symlink():
        raise ValueError("managed Git checkout is unsafe")
    if checkout.exists():
        snapshot = snapshot_git_repository(checkout)
        if snapshot.remote_url != normalized:
            raise ValueError("managed Git checkout identity does not match URL")
        return checkout

    progress("下载仓库", 15, "正在将 Git 仓库下载到本机。")
    environment = os.environ | {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_TEMPLATE_DIR": "",
    }
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=never",
                "-c",
                "protocol.ext.allow=never",
                "clone",
                "--depth=1",
                "--no-tags",
                "--",
                normalized,
                str(checkout),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_CLONE_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitRepositoryError("could not clone Git repository") from exc
    if result.returncode != 0 or checkout.is_symlink() or not checkout.is_dir():
        raise GitRepositoryError("could not clone Git repository")
    return checkout


def _normalise_git_repository_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Git 仓库链接仅支持不含凭据的 HTTPS URL")
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        raise ValueError("Git 仓库链接缺少仓库路径")
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _codex_rollouts() -> list[Path]:
    roots = (
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "aiden-app-home" / "sessions",
    )
    paths = {
        path
        for root in roots
        if root.is_dir() and not root.is_symlink()
        for path in root.rglob("rollout-*.jsonl")
        if path.is_file() and not path.is_symlink()
    }
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:200]


def _count_supported_files(root: Path) -> int:
    count = 0
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
        count += sum(
            (current_path / name).suffix.lower() in _SUPPORTED_FILES
            and not (current_path / name).is_symlink()
            for name in files
        )
    return count


def _codex_preview(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".jsonl":
        raise CodexImportError("Codex rollout must be a regular JSONL file")
    title = "Codex 会话"
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if len(line) > 200_000:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict) or payload.get("role") != "user":
                continue
            content = payload.get("content")
            if not isinstance(content, list):
                continue
            text = next(
                (
                    str(item["text"])
                    for item in content
                    if isinstance(item, dict)
                    and item.get("type") == "input_text"
                    and isinstance(item.get("text"), str)
                ),
                "",
            )
            if text and not text.lstrip().startswith("<"):
                title = " ".join(text.split())[:80]
                break
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return {
        "title": title,
        "platform": "codex",
        "updated": modified.isoformat(),
        "size_bytes": path.stat().st_size,
        "privacy": "local_only",
    }


def _source_name(workspace: Path, source_id: str) -> str:
    with _connect_readonly(Workspace.open_readonly(workspace).index_path) as connection:
        row = connection.execute(
            """
            SELECT versions.title
            FROM sources
            JOIN source_versions AS versions
              ON versions.source_id = sources.id AND versions.is_current = 1
            WHERE sources.source_id = ?
            """,
            (source_id,),
        ).fetchone()
    if row is None:
        raise ValueError("unknown source")
    return str(row["title"])


def _public_job(job: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    started = _parse_time(job.get("started_at"))
    finished = _parse_time(job.get("finished_at")) or datetime.now(UTC)
    duration = int((finished - started).total_seconds()) if started is not None else 0
    result = {
        "id": job["id"],
        "name": job["name"],
        "type": job["type"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": job["progress"],
        "message": job["message"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "duration_seconds": max(0, duration),
        "retryable": bool(job["retryable"]),
        "next_step": "查看知识更新" if job["status"] == "waiting_review" else None,
    }
    if detail:
        result.update(
            error_code=job["error_code"],
            changeset_ids=job["changeset_ids"],
            workspace_commit=str(job["workspace_commit"])[:12],
            result=job["result"],
        )
    return result


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in result.items() if key in {"message", "changeset_ids", "commit"}
    }


def _parse_time(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value.strip() or None


def _safe_failure_message(kind: str) -> str:
    return {
        "import": "来源处理失败，请检查本地来源后重试。",
        "refresh": "来源刷新失败，请检查来源状态后重试。",
        "apply": "应用失败，正式 Wiki 未被标记为成功。",
    }.get(kind, "任务失败，可以重试。")


def _error_code(exc: Exception) -> str:
    if isinstance(exc, (ValueError, CodexImportError, GitRepositoryError)):
        return "invalid_source"
    if isinstance(exc, (OSError, sqlite3.Error)):
        return "local_io_error"
    return "workflow_error"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def automation_status(workspace: Path) -> dict[str, Any]:
    opened = Workspace.open_readonly(workspace)
    path = opened.internal_dir / "traces" / "portal" / "automation.json"
    if path.is_symlink() or not path.is_file():
        return {
            "enabled": False,
            "supported": sys.platform == "darwin",
            "interval_minutes": 60,
            "types": ["code", "codex"],
            "last_run": None,
            "next_run": None,
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    interval = int(value.get("interval_minutes", 60))
    last_run = value.get("last_run")
    configured_at = value.get("configured_at")
    base_time = last_run if isinstance(last_run, str) else configured_at
    next_run = (
        (datetime.fromisoformat(base_time) + timedelta(minutes=interval)).isoformat()
        if bool(value.get("enabled")) and isinstance(base_time, str)
        else None
    )
    return {
        "enabled": bool(value.get("enabled")),
        "supported": sys.platform == "darwin",
        "interval_minutes": interval,
        "types": value.get("types", []),
        "last_run": last_run,
        "next_run": next_run,
        "last_status": value.get("last_status"),
    }


def configure_automation(workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise ValueError("automatic updates currently require macOS launchd")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean")
    interval = payload.get("interval_minutes", 60)
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 5:
        raise ValueError("interval_minutes must be at least 5")
    raw_types = payload.get("types", ["code", "codex"])
    if not isinstance(raw_types, list) or not all(isinstance(item, str) for item in raw_types):
        raise ValueError("types must be a list")
    types = sorted(set(raw_types))
    if not set(types) <= _AUTOMATION_TYPES:
        raise ValueError("unsupported automation type")
    opened = Workspace.open(workspace)
    digest = hashlib.sha256(os.fsencode(str(opened.root))).hexdigest()[:16]
    label = f"com.memoryforge.portal.{digest}"
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    if launch_agents.is_symlink():
        raise ValueError("LaunchAgents directory is unsafe")
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{label}.plist"
    config_path = opened.internal_dir / "traces" / "portal" / "automation.json"
    domain = f"gui/{os.getuid()}"
    if not enabled:
        subprocess.run(
            ["launchctl", "bootout", domain, str(plist_path)],
            check=False,
            capture_output=True,
        )
        plist_path.unlink(missing_ok=True)
        _write_automation_config(
            opened.root,
            config_path,
            {
                "enabled": False,
                "interval_minutes": interval,
                "types": types,
                "last_run": None,
                "last_status": None,
                "configured_at": _now(),
            },
        )
        return automation_status(workspace)

    executable = shutil.which("memoryforge")
    if executable is None:
        raise ValueError("memoryforge executable is unavailable")
    plist = {
        "Label": label,
        "ProgramArguments": [
            executable,
            "automation-run",
            "--workspace",
            str(opened.root),
        ],
        "StartInterval": interval * 60,
        "RunAtLoad": False,
        "ProcessType": "Background",
    }
    temporary = plist_path.with_suffix(".tmp")
    temporary.write_bytes(plistlib.dumps(plist, sort_keys=True))
    temporary.chmod(0o600)
    os.replace(temporary, plist_path)
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        check=False,
        capture_output=True,
    )
    loaded = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        check=False,
        capture_output=True,
    )
    if loaded.returncode != 0:
        plist_path.unlink(missing_ok=True)
        raise ValueError("launchd could not enable automatic updates")
    _write_automation_config(
        opened.root,
        config_path,
        {
            "enabled": True,
            "interval_minutes": interval,
            "types": types,
            "last_run": None,
            "last_status": None,
            "configured_at": _now(),
        },
    )
    return automation_status(workspace)


def run_automation(workspace: Path) -> dict[str, Any]:
    status = automation_status(workspace)
    if not status["enabled"]:
        return {"status": "disabled", "changeset_ids": []}
    types = set(status["types"])
    opened = Workspace.open(workspace)
    changed = False
    with opened.exclusive_lock():
        if "code" in types:
            for repository in list_git_checkouts(workspace):
                synced = sync_git_checkout(workspace, repository.repository_id)
                changed = changed or any(
                    document.status in {"created", "updated"} for document in synced.documents
                )
        if "codex" in types:
            imported_threads = _imported_codex_threads(opened)
            for path in _codex_rollouts():
                thread = _codex_thread(path)
                if thread is None or thread in imported_threads:
                    continue
                result = import_codex_rollout(workspace, path)
                if result.status != "unchanged":
                    changed = True
                imported_threads.add(thread)
        if "feishu" in types:
            feishu = refresh_feishu_documents(workspace)
            changed = changed or any(item.created or item.updated for item in feishu)
        changesets = _stage_all_pending(opened) if changed else []
    config_path = opened.internal_dir / "traces" / "portal" / "automation.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(last_run=_now(), last_status="proposed" if changesets else "unchanged")
    _write_automation_config(opened.root, config_path, config)
    return {"status": config["last_status"], "changeset_ids": changesets}


def _imported_codex_threads(opened: Workspace) -> set[str]:
    with _connect_readonly(opened.index_path) as connection:
        rows = connection.execute(
            """
            SELECT versions.tags_json
            FROM source_versions AS versions
            WHERE versions.is_current = 1
              AND versions.tags_json LIKE '%"platform:codex"%'
            """
        ).fetchall()
    threads: set[str] = set()
    for row in rows:
        tags = json.loads(str(row["tags_json"]))
        threads.update(tag.removeprefix("thread:") for tag in tags if tag.startswith("thread:"))
    return threads


def _codex_thread(path: Path) -> str | None:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            if isinstance(payload, dict):
                value = payload.get("id") or payload.get("session_id")
                return value if isinstance(value, str) else None
    return None


def _write_automation_config(
    workspace: Path,
    path: Path,
    value: dict[str, Any],
) -> None:
    _ensure_state_directory(workspace, path.parent)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _ensure_state_directory(workspace: Path, path: Path) -> None:
    internal = workspace / ".memoryforge"
    try:
        relative = path.relative_to(internal)
    except ValueError as exc:
        raise ValueError("Portal state must stay inside Workspace") from exc
    current = internal
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Portal state directory is unsafe")
        current.mkdir(mode=0o700, exist_ok=True)
        metadata = current.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Portal state directory is unsafe")
        current.chmod(0o700)
