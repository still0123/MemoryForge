"""Read documentation files from an existing local Git checkout."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from urllib.parse import quote, urlsplit, urlunsplit

from memoryforge.core.models import LocalDocument, Sensitivity, SourceCategory


class GitRepositoryError(ValueError):
    """Raised when a path cannot be scanned as a Git checkout."""


CODE_WIKI_VERSION = "symbols-v4"


@dataclass(frozen=True)
class GitSnapshot:
    repository_root: Path
    revision: str
    repository_identity: str
    remote_name: str | None = None
    remote_url: str | None = None
    branch: str | None = None


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
        if content is None or not content.strip():
            continue
        suffix = _document_suffix(relative_path)
        documents.append(
            LocalDocument(
                source_uri=_source_uri(snapshot.repository_identity, relative_path),
                source_path=relative_path,
                media_type=("text/markdown" if suffix in {".md", ".markdown"} else "text/plain"),
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
    """Read selected Go, Python, and TypeScript files from one committed Git snapshot."""
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
        if suffix not in {".go", ".py", ".ts", ".tsx"} or not _matches_code_selection(
            relative_path,
            normalized,
        ):
            continue
        content = _read_text_blob(snapshot.repository_root, object_id)
        if content is None or not content.strip():
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
                tags=("code", suffix.removeprefix("."), CODE_WIKI_VERSION),
            )
        )
    if documents:
        documents.extend(
            _code_module_documents(
                snapshot,
                tuple(documents),
                sensitivity=sensitivity,
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
    remote_url = _sanitize_remote_url(configured_remote) or _sanitize_scp_remote(configured_remote)
    branch_result = _run_git(repository_root, "branch", "--show-current")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    return GitSnapshot(
        repository_root=repository_root,
        revision=revision_result.stdout.strip(),
        repository_identity=remote_url or str(repository_root),
        remote_name="origin" if remote_url is not None else None,
        remote_url=remote_url,
        branch=branch or None,
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


def _code_module_documents(
    snapshot: GitSnapshot,
    code_documents: tuple[LocalDocument, ...],
    *,
    sensitivity: Sensitivity,
) -> tuple[LocalDocument, ...]:
    """Build small structural cards alongside raw code sources for old Wiki queries."""
    grouped: dict[str, list[LocalDocument]] = {}
    direct_files: dict[str, list[LocalDocument]] = {}
    for document in code_documents:
        path = PurePosixPath(document.source_path)
        parent = path.parent
        if parent == PurePosixPath("."):
            grouped.setdefault("root", []).append(document)
            direct_files.setdefault("root", []).append(document)
            continue
        direct_files.setdefault(parent.as_posix(), []).append(document)
        while parent != PurePosixPath("."):
            grouped.setdefault(parent.as_posix(), []).append(document)
            parent = parent.parent

    modules: list[LocalDocument] = []
    module_names = set(grouped)
    for module, documents in sorted(grouped.items()):
        source_path = f".memoryforge/code-modules/{module}.md"
        paths = sorted(document.source_path for document in documents)
        own_files = sorted(direct_files.get(module, []), key=lambda item: item.source_path)
        children = sorted(
            directory
            for directory in grouped
            if PurePosixPath(directory).parent.as_posix() == module
        )
        implementation_documents = tuple(
            document for document in documents if not _is_test_file(document.source_path)
        )
        symbols = tuple(
            dict.fromkeys(
                symbol
                for document in (*own_files, *implementation_documents)
                if not _is_test_file(document.source_path)
                for symbol in _exported_code_symbols(document)
            )
        )
        operations = _representative_operations(symbols)
        entry_points = _code_entry_points(implementation_documents)
        dependencies = _internal_dependencies(
            tuple(document for document in own_files if not _is_test_file(document.source_path)),
            module_names,
            module,
        )
        tests = tuple(
            document.source_path for document in documents if _is_test_file(document.source_path)
        )
        aliases = tuple(dict.fromkeys((PurePosixPath(module).name, module)))
        suffixes = {document.suffix for document in documents}
        tracked_label = (
            "Go/Python files"
            if suffixes <= {".go", ".py"}
            else "TypeScript files"
            if suffixes <= {".ts", ".tsx"}
            else "supported source files"
        )
        content = "\n".join(
            [
                f"# Code module: {module}",
                "",
                "## Identity",
                "",
                f"- Canonical module path: `{module}`",
                "- Search aliases: " + ", ".join(f"`{alias}`" for alias in aliases),
                f"- Contains {len(paths)} tracked "
                f"{tracked_label}; "
                f"{len(own_files)} are directly inside this directory.",
                "",
                "## Responsibilities",
                "",
                *(
                    [
                        f"- Main exported operations in `{module}`: "
                        + ", ".join(f"`{symbol}`" for symbol in operations)
                    ]
                    if operations
                    else []
                ),
                *(
                    [
                        "- Other exported code symbols: "
                        + ", ".join(f"`{symbol}`" for symbol in symbols[:20])
                    ]
                    if symbols
                    else []
                ),
                "",
                "## Entry points and handlers",
                "",
                *(
                    [
                        f"- `{path}`: " + ", ".join(f"`{symbol}`" for symbol in names)
                        for path, names in entry_points
                    ]
                    if entry_points
                    else ["- No explicit entry point or handler was detected."]
                ),
                "",
                "## Module dependencies",
                "",
                *(
                    [f"- Imports module `{dependency}`" for dependency in dependencies]
                    if dependencies
                    else ["- No direct module import was detected."]
                ),
                "",
                "## Tests",
                "",
                *([f"- `{path}`" for path in tests[:12]] if tests else ["- No test file found."]),
                "",
                "## Child modules",
                "",
                *(
                    [f"- `{child}`" for child in children]
                    if children
                    else ["- No child code directories."]
                ),
                "",
                "## Representative files",
                "",
                *(f"- `{document.source_path}`" for document in own_files[:12]),
                *([f"- `{path}`" for path in paths[:12]] if not own_files else []),
                "",
            ]
        )
        modules.append(
            LocalDocument(
                source_uri=_source_uri(snapshot.repository_identity, source_path),
                source_path=source_path,
                media_type="text/markdown",
                category=SourceCategory.REFS,
                suffix=".md",
                title=f"Code module: {module}",
                content=content,
                sensitivity=sensitivity,
                tags=("code-module",),
            )
        )
    return tuple(modules)


def _exported_code_symbols(document: LocalDocument) -> tuple[str, ...]:
    if document.suffix == ".go":
        pattern = r"^(?:type\s+|func\s+(?:\([^)]*\)\s*)?)([A-Z]\w*)"
    elif document.suffix == ".py":
        pattern = r"^(?:class|def)\s+([A-Za-z]\w*)"
    else:
        pattern = r"^(?:class|interface|type|function|const)\s+([A-Za-z]\w*)"
    return tuple(dict.fromkeys(re.findall(pattern, document.content, re.MULTILINE)))


def _representative_operations(symbols: tuple[str, ...]) -> tuple[str, ...]:
    prefixes = (
        "Create",
        "Describe",
        "Update",
        "Delete",
        "Enable",
        "Disable",
        "Start",
        "Stop",
        "Cancel",
        "Add",
        "Remove",
        "List",
        "Check",
        "Set",
        "Get",
        "New",
        "Run",
        "Serve",
        "Handle",
        "Register",
    )
    selected = [symbol for symbol in symbols if symbol.startswith(prefixes)]
    return tuple((selected or list(symbols))[:16])


def _code_entry_points(
    documents: tuple[LocalDocument, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    prefixes = ("New", "Run", "Serve", "Handle", "Register", "Execute")
    entries: list[tuple[str, tuple[str, ...]]] = []
    for document in documents:
        names = tuple(
            symbol for symbol in _exported_code_symbols(document) if symbol.startswith(prefixes)
        )
        if document.suffix == ".go" and re.search(r"^func\s+main\s*\(", document.content, re.M):
            names = tuple(dict.fromkeys(("main", *names)))
        if document.suffix == ".py" and 'if __name__ == "__main__":' in document.content:
            names = tuple(dict.fromkeys(("main", *names)))
        if names:
            entries.append((document.source_path, names[:12]))
    return tuple(entries[:12])


def _internal_dependencies(
    documents: tuple[LocalDocument, ...],
    modules: set[str],
    current_module: str,
) -> tuple[str, ...]:
    imported: list[str] = []
    for document in documents:
        if document.suffix == ".go":
            candidates = re.findall(r'"([^"\n]+)"', document.content)
        else:
            candidates = re.findall(
                r"^(?:from|import)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
                document.content,
                re.MULTILINE,
            )
        for candidate in candidates:
            normalized = candidate.replace(".", "/")
            match = next(
                (
                    module
                    for module in sorted(modules, key=len, reverse=True)
                    if module != current_module
                    and (normalized == module or normalized.endswith("/" + module))
                ),
                None,
            )
            if match:
                imported.append(match)
    return tuple(dict.fromkeys(imported))


def _is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name.endswith(("_test.go", "_test.py", "_test.ts", "_test.tsx"))
        or name.startswith("test_")
        or name.endswith((".spec.ts", ".spec.tsx"))
    )


def _code_suffix(suffix: str) -> Literal[".go", ".py", ".ts", ".tsx"]:
    if suffix not in {".go", ".py", ".ts", ".tsx"}:
        raise GitRepositoryError(f"unsupported code suffix: {suffix}")
    return cast(Literal[".go", ".py", ".ts", ".tsx"], suffix)


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
