"""Thin adapter that imports one readable Feishu Docx or Wiki document."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from memoryforge.errors import MemoryForgeError
from memoryforge.importer import import_local_document
from memoryforge.models import ImportResult, LocalDocument, Sensitivity, SourceCategory
from memoryforge.workspace import list_feishu_documents, register_feishu_document

_DOCUMENT_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,}$")
_DOCUMENT_TITLE = re.compile(r"<title>(?P<title>[^<]+)</title>")
_LARK_WRAPPER_TAG = re.compile(r"</?(?:title|callout|grid|column)\b[^>]*>")
_TOP_LEVEL_HEADING = re.compile(r"^# (?P<title>.+?)\s*$", re.MULTILINE)
_LARK_HOST_SUFFIXES = (".feishu.cn", ".larkoffice.com", ".doubao.com")


class FeishuDocumentError(MemoryForgeError):
    """Raised when one Feishu document cannot be read as a source."""


@dataclass(frozen=True)
class FeishuDocumentSyncResult:
    document_id: str
    created: int
    updated: int
    unchanged: int


def import_feishu_document(
    workspace: Path,
    document_reference: str,
    *,
    category: str = "notes",
    tags: tuple[str, ...] = (),
) -> tuple[ImportResult, ...]:
    """Fetch one document in Markdown and store it through the normal source pipeline."""
    try:
        normalized_category = SourceCategory(category)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceCategory)
        raise FeishuDocumentError(f"category must be one of: {allowed}") from exc

    document = _fetch_document(document_reference)
    document_id = _required_string(document, "document_id")
    raw_content = _required_string(document, "content")
    title = _document_title(document, raw_content, document_id)
    content = _document_content(raw_content)
    normalized_tags = tuple(sorted({"feishu", *(tag.strip() for tag in tags if tag.strip())}))
    results: list[ImportResult] = []
    for part_id, part_title, part_content in _document_parts(title, content):
        source_key = (
            f"feishu:{document_id}" if part_id == "document" else f"feishu:{document_id}:{part_id}"
        )
        source_id = hashlib.sha256(source_key.encode()).hexdigest()
        local_document = LocalDocument(
            source_uri=f"mf://source/{source_id}",
            source_path=(
                f"feishu/{document_id}.md"
                if part_id == "document"
                else f"feishu/{document_id}/{part_id}.md"
            ),
            media_type="text/markdown",
            category=normalized_category,
            suffix=".md",
            title=part_title,
            content=part_content,
            sensitivity=Sensitivity.LOCAL_ONLY,
            tags=normalized_tags,
        )
        results.append(import_local_document(workspace, local_document, source_id=source_id))
    register_feishu_document(
        workspace,
        document_id,
        category=normalized_category,
        tags=tags,
    )
    return tuple(results)


def refresh_feishu_documents(workspace: Path) -> tuple[FeishuDocumentSyncResult, ...]:
    """Re-import every previously selected Feishu document once, without background sync."""
    refreshed: list[FeishuDocumentSyncResult] = []
    for document in list_feishu_documents(workspace):
        results = import_feishu_document(
            workspace,
            document.document_id,
            category=document.category.value,
            tags=document.tags,
        )
        refreshed.append(
            FeishuDocumentSyncResult(
                document_id=document.document_id,
                created=sum(result.status == "created" for result in results),
                updated=sum(result.status == "updated" for result in results),
                unchanged=sum(result.status == "unchanged" for result in results),
            )
        )
    return tuple(refreshed)


def _fetch_document(document_reference: str) -> dict[str, Any]:
    document_reference = document_reference.strip()
    _validate_document_reference(document_reference)
    try:
        completed = subprocess.run(
            [
                "lark-cli",
                "docs",
                "+fetch",
                "--api-version",
                "v2",
                "--as",
                "user",
                "--doc",
                document_reference,
                "--doc-format",
                "markdown",
                "--format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise FeishuDocumentError("lark-cli is not available") from exc
    if completed.returncode != 0:
        raise FeishuDocumentError("Feishu document fetch failed; check login and document access")
    try:
        payload = json.loads(completed.stdout)
        document = payload["data"]["document"]
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise FeishuDocumentError("Feishu document fetch returned an unexpected response") from exc
    if not isinstance(document, dict):
        raise FeishuDocumentError("Feishu document fetch returned an unexpected response")
    return document


def _validate_document_reference(document_reference: str) -> None:
    reference = document_reference.strip()
    if _DOCUMENT_TOKEN.fullmatch(reference):
        return
    parsed = urlparse(reference)
    is_lark_host = parsed.hostname is not None and parsed.hostname.endswith(_LARK_HOST_SUFFIXES)
    is_document_path = parsed.path.startswith(("/docx/", "/wiki/"))
    if parsed.scheme != "https" or not is_lark_host or not is_document_path:
        raise FeishuDocumentError(
            "document must be a Feishu/Lark Docx or Wiki URL, or a document token"
        )


def _required_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FeishuDocumentError("Feishu document fetch returned an incomplete document")
    return value


def _document_title(document: dict[str, Any], content: str, fallback: str) -> str:
    title = document.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    title_match = _DOCUMENT_TITLE.search(content)
    if title_match and title_match["title"].strip():
        return title_match["title"].strip()
    for line in content.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return fallback


def _document_content(content: str) -> str:
    return _LARK_WRAPPER_TAG.sub("", _DOCUMENT_TITLE.sub("", content)).strip()


def _document_parts(title: str, content: str) -> tuple[tuple[str, str, str], ...]:
    headings = list(_TOP_LEVEL_HEADING.finditer(content))
    introduction = content[: headings[0].start()].strip() if headings else content
    if len(headings) <= 1 and not introduction:
        return (("document", title, content),)

    parts: list[tuple[str, str, str]] = []
    if introduction:
        parts.append(("document", title, introduction))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        part_id = "document" if not parts and index == 0 else f"section-{index + 1}"
        heading_title = heading["title"].strip()
        parts.append(
            (part_id, f"{title} / {heading_title}", content[heading.start() : end].strip())
        )
    return tuple(parts)
