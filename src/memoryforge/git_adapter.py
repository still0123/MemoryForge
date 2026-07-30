"""Read documentation files from an existing local Git checkout."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

from memoryforge.models import LocalDocument, Sensitivity, SourceCategory


class GitRepositoryError(ValueError):
    """Raised when a path cannot be scanned as a Git checkout."""


@dataclass(frozen=True)
class GitSnapshot:
    repository_root: Path
    revision: str
    repository_identity: str
    remote_name: str | None = None
    remote_url: str | None = None


def scan_git_documentation(
    checkout: Path,
    *,
    sensitivity: Sensitivity = Sensitivity.LOCAL_ONLY,
) -> tuple[LocalDocument, ...]:
    """Return tracked documentation files from one already-cloned repository."""
    snapshot = snapshot_git_repository(checkout)
    return scan_git_snapshot_documentation(snapshot, sensitivity=sensitivity)


def scan_git_snapshot_documentation(
    snapshot: GitSnapshot,
    *,
    sensitivity: Sensitivity = Sensitivity.LOCAL_ONLY,
) -> tuple[LocalDocument, ...]:
    """Read documentation from the exact committed snapshot already selected."""
    tree_result = _run_git_bytes(
        snapshot.repository_root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        snapshot.revision,
    )
    if tree_result.returncode != 0:
        raise GitRepositoryError(f"could not list HEAD tree in {snapshot.repository_root}")

    documents = []
    for relative_path, object_id in _documentation_blobs(tree_result.stdout):
        content = _read_text_blob(snapshot.repository_root, object_id)
        if content is None:
            continue
        suffix = _document_suffix(relative_path)
        documents.append(
            LocalDocument(
                source_uri=_source_uri(snapshot.repository_identity, relative_path),
                source_path=relative_path,
                media_type=(
                    "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
                ),
                category=SourceCategory.REFS,
                suffix=suffix,
                title=PurePosixPath(relative_path).stem,
                content=content,
                sensitivity=sensitivity,
            )
        )
    return tuple(documents)


def scan_git_snapshot_code(
    snapshot: GitSnapshot,
    selections: tuple[str, ...],
    *,
    sensitivity: Sensitivity = Sensitivity.LOCAL_ONLY,
) -> tuple[LocalDocument, ...]:
    """Read selected Go and Python files from one committed Git snapshot."""
    normalized = tuple(_normalise_code_selection(selection) for selection in selections)
    if not normalized:
        return ()
    tree_result = _run_git_bytes(
        snapshot.repository_root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        snapshot.revision,
    )
    if tree_result.returncode != 0:
        raise GitRepositoryError(f"could not list HEAD tree in {snapshot.repository_root}")

    documents = []
    for relative_path, object_id in _tracked_blobs(tree_result.stdout):
        suffix = PurePosixPath(relative_path).suffix.lower()
        if suffix not in {".go", ".py"} or not _matches_code_selection(relative_path, normalized):
            continue
        content = _read_text_blob(snapshot.repository_root, object_id)
        if content is None:
            continue
        documents.append(
            LocalDocument(
                source_uri=_source_uri(snapshot.repository_identity, relative_path),
                source_path=relative_path,
                media_type="text/plain",
                category=SourceCategory.REFS,
                suffix=_code_suffix(suffix),
                title=f"Code: {relative_path}",
                content=content,
                sensitivity=sensitivity,
                tags=("code", suffix.removeprefix(".")),
            )
        )
    return tuple(documents)


def snapshot_git_repository(checkout: Path) -> GitSnapshot:
    """Describe the checked-out revision and stable identity of a local repository."""
    root_result = _run_git(checkout, "rev-parse", "--show-toplevel")
    if root_result.returncode != 0:
        raise GitRepositoryError(f"not a Git checkout: {checkout}")
    repository_root = Path(root_result.stdout.strip()).resolve()

    revision_result = _run_git(repository_root, "rev-parse", "HEAD")
    if revision_result.returncode != 0:
        raise GitRepositoryError(f"Git checkout has no HEAD revision: {repository_root}")

    remote_result = _run_git(repository_root, "config", "--get", "remote.origin.url")
    configured_remote = remote_result.stdout.strip() if remote_result.returncode == 0 else ""
    remote_url = _sanitize_remote_url(configured_remote) or _sanitize_scp_remote(
        configured_remote
    )
    return GitSnapshot(
        repository_root=repository_root,
        revision=revision_result.stdout.strip(),
        repository_identity=remote_url or str(repository_root),
        remote_name="origin" if remote_url is not None else None,
        remote_url=remote_url,
    )


def _run_git(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise GitRepositoryError(f"could not run Git in {checkout}") from exc


def _run_git_bytes(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise GitRepositoryError(f"could not run Git in {checkout}") from exc


def _documentation_blobs(output: bytes) -> tuple[tuple[str, str], ...]:
    return tuple(
        (relative_path, object_id)
        for relative_path, object_id in _tracked_blobs(output)
        if _is_documentation_path(relative_path)
    )


def _tracked_blobs(output: bytes) -> tuple[tuple[str, str], ...]:
    blobs = []
    for record in output.split(b"\0"):
        if not record:
            continue
        header, separator, raw_path = record.partition(b"\t")
        if not separator:
            continue
        parts = header.split()
        if len(parts) != 3:
            continue
        mode, object_type, object_id = parts
        if mode not in {b"100644", b"100755"} or object_type != b"blob":
            continue
        try:
            relative_path = raw_path.decode("utf-8")
            blob_id = object_id.decode("ascii")
        except UnicodeDecodeError:
            continue
        blobs.append((relative_path, blob_id))
    return tuple(sorted(blobs))


def _normalise_code_selection(selection: str) -> str:
    path = PurePosixPath(selection.strip())
    if (
        not selection.strip()
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in selection
    ):
        raise GitRepositoryError("code path must be a safe path relative to the Git repository")
    return path.as_posix()


def _matches_code_selection(relative_path: str, selections: tuple[str, ...]) -> bool:
    path = PurePosixPath(relative_path)
    return any(
        path == PurePosixPath(selection) or path.is_relative_to(PurePosixPath(selection))
        for selection in selections
    )


def _code_suffix(suffix: str) -> Literal[".go", ".py"]:
    if suffix == ".go":
        return ".go"
    return ".py"


def _read_text_blob(repository_root: Path, object_id: str) -> str | None:
    result = _run_git_bytes(repository_root, "cat-file", "-p", object_id)
    if result.returncode != 0:
        raise GitRepositoryError(f"could not read Git blob {object_id}")
    if b"\0" in result.stdout:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_documentation_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts:
        return False
    is_root_readme = len(path.parts) == 1 and (
        path.name.startswith("README") or path.name.startswith("CHANGELOG")
    )
    is_document_directory = path.parts and path.parts[0] in {"docs", "adr"}
    if not (is_root_readme or is_document_directory):
        return False
    return _is_allowed_document_name(path, is_root_readme)


def _is_allowed_document_name(path: PurePosixPath, is_root_readme: bool) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return True
    return is_root_readme and path.name in {"README", "CHANGELOG"}


def _document_suffix(relative_path: str) -> Literal[".md", ".markdown", ".txt"]:
    suffix = PurePosixPath(relative_path).suffix.lower()
    if suffix == ".md":
        return ".md"
    if suffix == ".markdown":
        return ".markdown"
    return ".txt"


def _source_uri(repository_identity: str, relative_path: str) -> str:
    return f"mf://git/{quote(repository_identity, safe='')}/{quote(relative_path, safe='/')}"


def _repository_identity(remote_url: str, repository_root: Path) -> str:
    """Return a credential-free remote identity, or the local root when unsure."""
    return _sanitize_remote_url(remote_url) or _sanitize_scp_remote(remote_url) or str(
        repository_root
    )


def _sanitize_remote_url(remote_url: str) -> str | None:
    """Keep only scheme, host, optional numeric port, and path from URL remotes."""
    try:
        parsed = urlsplit(remote_url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https", "ssh", "git"}:
        return None
    if not remote_url.startswith(f"{parsed.scheme}://"):
        return None
    if not parsed.hostname or not parsed.path:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None

    host = parsed.hostname
    if any(character.isspace() for character in host):
        return None
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{port}" if port is not None else rendered_host
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))


def _sanitize_scp_remote(remote_url: str) -> str | None:
    """Turn a Git SCP-style remote into host:path without its user or suffixes."""
    if "://" in remote_url or "@" not in remote_url:
        return None
    user_and_host, separator, path_and_suffix = remote_url.partition(":")
    if not separator or user_and_host.count("@") != 1:
        return None
    _user, host = user_and_host.split("@", maxsplit=1)
    path = path_and_suffix.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    if (
        not host
        or not path
        or any(character.isspace() for character in host + path)
        or any(character in host for character in "@:/?#")
    ):
        return None
    return f"{host}:{path}"
