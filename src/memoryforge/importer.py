from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath

from memoryforge.manifests import SourceManifestStore
from memoryforge.models import (
    ImportResult,
    LocalDocument,
    Sensitivity,
    SourceCategory,
    SourceVersionManifest,
)
from memoryforge.workspace import Workspace, store_source

ALLOWED_SUFFIXES = {".md", ".markdown", ".txt"}
MAX_SOURCE_BYTES = 5 * 1024 * 1024

_SENSITIVE_EXACT_NAMES = {
    ".env",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "application_default_credentials.json",
    "credentials",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "kubeconfig",
    "secret",
    "secrets",
}
_SENSITIVE_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
_SENSITIVE_STEMS = {
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "password",
    "secret",
    "secrets",
    "token",
}
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:[A-Z0-9][A-Z0-9 ]* )?PRIVATE KEY-----",
)
_TOKEN_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bsk_(?:live|test)_[0-9A-Za-z]{16,}\b"),
    re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bnpm_[0-9A-Za-z]{20,}\b"),
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"""(?im)^\s*
    (?:export\s+)?
    (?:AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|
       (?:[A-Z][A-Z0-9]*[_-])*
       (?:API[_-]?KEY|SECRET(?:[_-]?KEY)?|TOKEN|PASSWORD))
    \s*[:=]\s*
    ["']?([^\s"'#]{12,})["']?
    \s*(?:\#.*)?$
    """,
    flags=re.VERBOSE,
)
_PLACEHOLDER_VALUES = {
    "changeme",
    "dummy",
    "example",
    "placeholder",
    "redacted",
    "replace_me",
    "sample",
    "test",
}
_PLACEHOLDER_PREFIXES = ("dummy", "example", "placeholder", "redacted", "sample", "your_")


class SourceValidationError(ValueError):
    """Raised when a local source violates the import safety boundary."""


def validate_source_path(path: Path, *, source_root: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise SourceValidationError("symbolic links are not accepted as import sources")

    resolved = candidate.resolve()
    allowed_root = source_root.expanduser().resolve()
    if not resolved.is_relative_to(allowed_root):
        raise SourceValidationError("source must be inside the allowed root")
    if not resolved.exists():
        raise SourceValidationError("source does not exist")
    if not resolved.is_file():
        raise SourceValidationError("source must be a regular file")

    lower_name = resolved.name.lower()
    if (
        lower_name in _SENSITIVE_EXACT_NAMES
        or lower_name.startswith(".env.")
        or resolved.suffix.lower() in _SENSITIVE_SUFFIXES
        or resolved.stem.lower().replace("-", "_") in _SENSITIVE_STEMS
    ):
        raise SourceValidationError(f"sensitive file name is not allowed: {resolved.name}")

    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise SourceValidationError("only .md, .markdown, and .txt files are supported")
    if resolved.stat().st_size > MAX_SOURCE_BYTES:
        raise SourceValidationError(
            f"source exceeds the {MAX_SOURCE_BYTES // (1024 * 1024)} MiB size limit"
        )
    return resolved


def import_local_file(
    workspace: Path,
    source_path: Path,
    *,
    category: str = "notes",
    source_root: Path | None = None,
    tags: tuple[str, ...] = (),
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> ImportResult:
    current_workspace = Workspace.open(workspace)
    workspace = current_workspace.root
    try:
        normalized_category = SourceCategory(category)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceCategory)
        raise SourceValidationError(f"category must be one of: {allowed}") from exc

    allowed_root = (source_root if source_root is not None else Path.cwd()).expanduser().resolve()
    resolved = validate_source_path(source_path, source_root=allowed_root)
    filesystem_relative_path, relative_source_path = _canonical_relative_source_path(
        allowed_root,
        resolved,
    )
    if _is_ignored(allowed_root, relative_source_path):
        raise SourceValidationError(
            f"source is excluded by .memoryforgeignore: {relative_source_path}"
        )
    source_bytes, content_sha256 = _read_source_secure(
        allowed_root,
        filesystem_relative_path,
    )
    try:
        content = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceValidationError("source must be valid UTF-8 text") from exc
    if _contains_high_confidence_secret(content):
        raise SourceValidationError(
            "source content appears to contain a high-confidence secret pattern"
        )

    source_id = hashlib.sha256(relative_source_path.encode("utf-8")).hexdigest()
    source_uri = f"mf://source/{source_id}"

    canonical_path = Path(relative_source_path)
    suffix = canonical_path.suffix.lower()
    normalized_tags = tuple(sorted({tag.strip() for tag in tags if tag.strip()}))
    document = LocalDocument.model_validate(
        {
            "source_uri": source_uri,
            "source_path": relative_source_path,
            "media_type": ("text/markdown" if suffix in {".md", ".markdown"} else "text/plain"),
            "category": normalized_category,
            "suffix": suffix,
            "title": _extract_title(content, canonical_path.stem),
            "content": content,
            "sensitivity": sensitivity,
            "tags": normalized_tags,
        }
    )
    result = store_source(
        workspace,
        source_id=source_id,
        content_sha256=content_sha256,
        document=document,
    )
    SourceManifestStore(current_workspace.manifest_dir).write(
        SourceVersionManifest(
            source_id=result.source_id,
            source_uri=result.source_uri,
            source_path=document.source_path,
            content_sha256=result.content_sha256,
            snapshot_uri=result.snapshot_uri,
            snapshot_path=result.snapshot_path,
            media_type=document.media_type,
            category=result.category,
            title=result.title,
            observed_at=result.observed_at,
            sensitivity=document.sensitivity,
            tags=document.tags,
        )
    )
    return result


def _is_ignored(source_root: Path, relative_path: str) -> bool:
    """Apply the documented, intentionally small .memoryforgeignore subset."""
    try:
        root_fd = os.open(
            source_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise SourceValidationError("source root could not be opened safely") from exc
    try:
        try:
            ignore_fd = os.open(
                ".memoryforgeignore",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise SourceValidationError(".memoryforgeignore could not be opened safely") from exc
        try:
            file_stat = os.fstat(ignore_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise SourceValidationError(".memoryforgeignore must be a regular file")
            with os.fdopen(ignore_fd, "r", encoding="utf-8", closefd=False) as ignore_file:
                rules = ignore_file.read().splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise SourceValidationError(".memoryforgeignore must be valid UTF-8") from exc
        finally:
            os.close(ignore_fd)
    finally:
        os.close(root_fd)

    path = PurePosixPath(relative_path)
    for raw_rule in rules:
        rule = raw_rule.strip()
        if not rule or rule.startswith("#"):
            continue
        if rule.startswith("!"):
            raise SourceValidationError(
                ".memoryforgeignore negation rules are not supported in Phase 1"
            )
        anchored = rule.startswith("/")
        rule = rule.lstrip("/")
        directory_rule = rule.endswith("/")
        rule = rule.rstrip("/")
        if not rule or any(part == ".." for part in PurePosixPath(rule).parts):
            raise SourceValidationError(".memoryforgeignore contains an unsafe rule")
        candidates = [relative_path] if anchored or "/" in rule else list(path.parts)
        if directory_rule:
            directories = [
                PurePosixPath(*path.parts[:index]).as_posix() for index in range(1, len(path.parts))
            ]
            candidates = directories if anchored or "/" in rule else list(path.parts[:-1])
        if any(fnmatch.fnmatchcase(candidate, rule) for candidate in candidates):
            return True
    return False


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title
    return fallback


def _contains_high_confidence_secret(content: str) -> bool:
    if _PRIVATE_KEY_PATTERN.search(content):
        return True
    for pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(content):
            if not _is_placeholder_value(match.group(0)):
                return True

    for match in _SECRET_ASSIGNMENT_PATTERN.finditer(content):
        if not _is_placeholder_value(match.group(1)):
            return True
    return False


def _is_placeholder_value(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    for token_prefix in (
        "sk_test_",
        "sk_live_",
        "glpat-",
        "aiza",
        "akia",
        "npm_",
    ):
        if normalized.startswith(token_prefix):
            normalized = normalized[len(token_prefix) :]
            break
    return normalized in _PLACEHOLDER_VALUES or normalized.startswith(_PLACEHOLDER_PREFIXES)


def _canonical_relative_source_path(source_root: Path, resolved: Path) -> tuple[Path, str]:
    requested_relative = resolved.relative_to(source_root)
    actual_parts: list[str] = []
    canonical_parts: list[str] = []
    current_directory = source_root

    for requested_component in requested_relative.parts:
        requested_child = current_directory / requested_component
        matching_entries: list[os.DirEntry[str]] = []
        try:
            with os.scandir(current_directory) as entries:
                for entry in entries:
                    if not _could_be_filesystem_alias(entry.name, requested_component):
                        continue
                    try:
                        if os.path.samefile(entry.path, requested_child):
                            matching_entries.append(entry)
                    except OSError:
                        continue
        except OSError as exc:
            raise SourceValidationError("source path could not be canonicalized safely") from exc

        if len(matching_entries) != 1:
            raise SourceValidationError("source path could not be canonicalized safely")
        actual_name = matching_entries[0].name
        actual_parts.append(actual_name)
        canonical_parts.append(_canonical_component_name(current_directory, actual_name))
        current_directory /= actual_name

    filesystem_relative = Path(*actual_parts)
    canonical_relative = Path(*canonical_parts).as_posix()
    return filesystem_relative, canonical_relative


def _could_be_filesystem_alias(actual: str, requested: str) -> bool:
    if actual == requested:
        return True
    actual_nfc = unicodedata.normalize("NFC", actual)
    requested_nfc = unicodedata.normalize("NFC", requested)
    return actual_nfc == requested_nfc or actual_nfc.casefold() == requested_nfc.casefold()


def _canonical_component_name(parent: Path, actual_name: str) -> str:
    nfc_name = unicodedata.normalize("NFC", actual_name)
    if nfc_name == actual_name:
        return actual_name

    nfc_candidate = parent / nfc_name
    try:
        if nfc_candidate.exists() and os.path.samefile(parent / actual_name, nfc_candidate):
            return nfc_name
    except OSError:
        pass
    return actual_name


def _read_source_secure(source_root: Path, relative_path: Path) -> tuple[bytes, str]:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise SourceValidationError("secure local imports are unsupported on this platform")

    parts = relative_path.parts
    if not parts or relative_path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise SourceValidationError("source path must be a safe path relative to the source root")

    descriptors: list[int] = []
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        current_fd = os.open(source_root, directory_flags)
        descriptors.append(current_fd)
        for component in parts[:-1]:
            current_fd = os.open(
                component,
                directory_flags,
                dir_fd=current_fd,
            )
            descriptors.append(current_fd)

        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current_fd)
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise SourceValidationError("source must be a regular file")
        if before.st_size > MAX_SOURCE_BYTES:
            raise SourceValidationError(
                f"source exceeds the {MAX_SOURCE_BYTES // (1024 * 1024)} MiB size limit"
            )

        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise SourceValidationError(
                    f"source exceeds the {MAX_SOURCE_BYTES // (1024 * 1024)} MiB size limit"
                )

        after = os.fstat(file_fd)
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise SourceValidationError("source changed while it was being read")
        return b"".join(chunks), digest.hexdigest()
    except OSError as exc:
        raise SourceValidationError("source could not be opened safely") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
