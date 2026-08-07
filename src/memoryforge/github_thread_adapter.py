"""Import one public GitHub Issue or Pull Request thread."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from memoryforge.errors import MemoryForgeError
from memoryforge.importer import (
    import_local_document,
    read_local_text_file,
    validate_local_document,
)
from memoryforge.models import ImportResult, LocalDocument, Sensitivity, SourceCategory
from memoryforge.workspace import Workspace, deactivate_current_source

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_NEXT_LINK = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')
_JSON_SUFFIXES = frozenset({".json"})
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_PAGES = 10
_MAX_CONTRIBUTIONS = 1000


class GitHubThreadError(MemoryForgeError):
    """Raised when one public GitHub thread cannot be imported safely."""


@dataclass(frozen=True)
class GitHubThreadIdentity:
    kind: Literal["issue", "pull"]
    owner: str
    repository: str
    number: int
    url: str


class GitHubThreadResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["issue", "pull"]
    owner: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    number: int = Field(gt=0)
    title: str = Field(min_length=1)
    body: str
    state: str = Field(min_length=1)
    author: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    html_url: str = Field(min_length=1)


class GitHubThreadContribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["issue_comment", "review", "review_comment"]
    id: str = Field(min_length=1)
    author: str = Field(min_length=1)
    body: str
    created_at: datetime
    updated_at: datetime
    html_url: str = Field(min_length=1)


class GitHubThreadSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_url: str = Field(min_length=1)
    resource: GitHubThreadResource
    contributions: tuple[GitHubThreadContribution, ...] = ()

    @model_validator(mode="after")
    def validate_identity_and_order(self) -> GitHubThreadSnapshot:
        identity = parse_github_thread_url(self.source_url)
        resource = self.resource
        if (
            resource.kind != identity.kind
            or resource.owner.casefold() != identity.owner
            or resource.repository.casefold() != identity.repository
            or resource.number != identity.number
            or parse_github_thread_url(resource.html_url) != identity
        ):
            raise ValueError("GitHub thread resource identity does not match source_url")
        if resource.created_at.tzinfo is None or resource.updated_at.tzinfo is None:
            raise ValueError("GitHub thread timestamps must include a timezone")
        keys = [(item.kind, item.id) for item in self.contributions]
        if len(keys) != len(set(keys)):
            raise ValueError("GitHub thread contributions must be unique")
        expected = tuple(sorted(self.contributions, key=_contribution_sort_key))
        if self.contributions != expected:
            raise ValueError("GitHub thread contributions must be sorted")
        for contribution in self.contributions:
            if (
                contribution.created_at.tzinfo is None
                or contribution.updated_at.tzinfo is None
                or not _is_contribution_locator(contribution.html_url, identity.url)
            ):
                raise ValueError("GitHub thread contribution metadata is invalid")
        return self


class GitHubThreadDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: str = Field(min_length=1)
    deleted: bool


class _GitHubRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        expected_path = urlsplit(req.full_url).path
        _validate_api_url(newurl, expected_path=expected_path)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def parse_github_thread_url(value: str) -> GitHubThreadIdentity:
    """Parse one exact public GitHub Issue or Pull Request URL."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GitHubThreadError("GitHub thread must be a public credential-free HTTPS URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4 or parts[2] not in {"issues", "pull"}:
        raise GitHubThreadError("GitHub thread URL must identify one Issue or Pull Request")
    owner, repository, path_kind, raw_number = parts
    if (
        _OWNER.fullmatch(owner) is None
        or _REPOSITORY.fullmatch(repository) is None
        or repository in {".", ".."}
        or not raw_number.isdigit()
        or int(raw_number) < 1
    ):
        raise GitHubThreadError("GitHub thread URL contains an invalid repository or number")
    kind: Literal["issue", "pull"] = "issue" if path_kind == "issues" else "pull"
    owner = owner.casefold()
    repository = repository.casefold()
    number = int(raw_number)
    canonical_path = "issues" if kind == "issue" else "pull"
    url = f"https://github.com/{owner}/{repository}/{canonical_path}/{number}"
    return GitHubThreadIdentity(
        kind=kind,
        owner=owner,
        repository=repository,
        number=number,
        url=url,
    )


def import_github_thread(
    workspace: Path,
    url: str,
    *,
    save_json: Path | None = None,
    category: str = "refs",
    tags: tuple[str, ...] = (),
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> ImportResult:
    """Fetch and import exactly one public GitHub thread."""
    identity = parse_github_thread_url(url)
    snapshot = _fetch_github_snapshot(identity)
    return _import_snapshot(
        workspace,
        snapshot,
        save_json=save_json,
        category=category,
        tags=tags,
        sensitivity=sensitivity,
    )


def import_github_thread_json(
    workspace: Path,
    json_path: Path,
    *,
    source_root: Path | None = None,
    category: str = "refs",
    tags: tuple[str, ...] = (),
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> ImportResult:
    """Import one normalized saved thread without making a network request."""
    content = read_local_text_file(
        json_path,
        source_root=source_root if source_root is not None else Path.cwd(),
        allowed_suffixes=_JSON_SUFFIXES,
    )
    try:
        payload = json.loads(content)
        snapshot = GitHubThreadSnapshot.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GitHubThreadError("saved GitHub thread JSON is invalid") from exc
    return _import_snapshot(
        workspace,
        snapshot,
        category=category,
        tags=tags,
        sensitivity=sensitivity,
    )


def delete_github_thread(workspace: Path, url: str) -> GitHubThreadDeleteResult:
    """Explicitly deactivate one imported public thread while retaining its history."""
    identity = parse_github_thread_url(url)
    source_id = _source_id(identity.url)
    source_path = _source_path(identity)
    opened = Workspace.open(workspace)
    with opened.exclusive_lock():
        deleted = deactivate_current_source(
            opened,
            source_id=source_id,
            expected_source_path=source_path,
        )
    return GitHubThreadDeleteResult(
        source_id=source_id,
        source_url=identity.url,
        deleted=deleted,
    )


def _fetch_github_snapshot(identity: GitHubThreadIdentity) -> GitHubThreadSnapshot:
    base = f"https://api.github.com/repos/{identity.owner}/{identity.repository}"
    resource_path = (
        f"/repos/{identity.owner}/{identity.repository}/issues/{identity.number}"
        if identity.kind == "issue"
        else f"/repos/{identity.owner}/{identity.repository}/pulls/{identity.number}"
    )
    resource_payload, next_url = _request_json_page(f"https://api.github.com{resource_path}")
    if next_url is not None or not isinstance(resource_payload, dict):
        raise GitHubThreadError("GitHub resource response is invalid")
    resource = _normalise_resource(resource_payload, identity)
    contributions = _fetch_contributions(base, identity)
    return GitHubThreadSnapshot(
        source_url=identity.url,
        resource=resource,
        contributions=contributions,
    )


def _fetch_contributions(
    base: str,
    identity: GitHubThreadIdentity,
) -> tuple[GitHubThreadContribution, ...]:
    issue_comments_path = (
        f"/repos/{identity.owner}/{identity.repository}/issues/{identity.number}/comments"
    )
    contributions = [
        _normalise_comment(item, "issue_comment")
        for item in _fetch_collection(
            f"{base}/issues/{identity.number}/comments", issue_comments_path
        )
    ]
    if identity.kind == "pull":
        reviews_path = (
            f"/repos/{identity.owner}/{identity.repository}/pulls/{identity.number}/reviews"
        )
        review_comments_path = (
            f"/repos/{identity.owner}/{identity.repository}/pulls/{identity.number}/comments"
        )
        contributions.extend(
            _normalise_review(item)
            for item in _fetch_collection(f"{base}/pulls/{identity.number}/reviews", reviews_path)
        )
        contributions.extend(
            _normalise_comment(item, "review_comment")
            for item in _fetch_collection(
                f"{base}/pulls/{identity.number}/comments",
                review_comments_path,
            )
        )
    deduplicated = {(item.kind, item.id): item for item in contributions}
    if len(deduplicated) > _MAX_CONTRIBUTIONS:
        raise GitHubThreadError("GitHub thread exceeds the contribution limit")
    return tuple(sorted(deduplicated.values(), key=_contribution_sort_key))


def _fetch_collection(url: str, expected_path: str) -> list[dict[str, Any]]:
    next_url: str | None = f"{url}?per_page=100"
    items: list[dict[str, Any]] = []
    pages = 0
    while next_url is not None:
        pages += 1
        if pages > _MAX_PAGES:
            raise GitHubThreadError("GitHub thread exceeds the pagination limit")
        _validate_api_url(next_url, expected_path=expected_path)
        payload, next_url = _request_json_page(next_url)
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise GitHubThreadError("GitHub contribution response is invalid")
        items.extend(payload)
        if len(items) > _MAX_CONTRIBUTIONS:
            raise GitHubThreadError("GitHub thread exceeds the contribution limit")
        if next_url is not None:
            _validate_api_url(next_url, expected_path=expected_path)
    return items


def _request_json_page(url: str) -> tuple[object, str | None]:
    expected_path = urlsplit(url).path
    _validate_api_url(url, expected_path=expected_path)
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MemoryForge/0.3",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with build_opener(_GitHubRedirectHandler()).open(request, timeout=20) as response:
            final_url = response.geturl()
            _validate_api_url(final_url, expected_path=expected_path)
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            link = response.headers.get("Link", "")
    except HTTPError as exc:
        raise GitHubThreadError(f"GitHub API request failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise GitHubThreadError(f"GitHub API request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GitHubThreadError("GitHub API request timed out after 20 seconds") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise GitHubThreadError("GitHub API response exceeds the 5 MiB limit")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubThreadError("GitHub API response is not valid UTF-8 JSON") from exc
    match = _NEXT_LINK.search(link)
    return payload, match.group("url") if match is not None else None


def _validate_api_url(value: str, *, expected_path: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != "api.github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path != expected_path
        or parsed.fragment
    ):
        raise GitHubThreadError("GitHub API URL escaped the selected resource")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key not in {"page", "per_page"} for key in query):
        raise GitHubThreadError("GitHub API pagination query is invalid")
    for key, values in query.items():
        if len(values) != 1 or not values[0].isdigit() or int(values[0]) < 1:
            raise GitHubThreadError("GitHub API pagination query is invalid")
        if key == "per_page" and values[0] != "100":
            raise GitHubThreadError("GitHub API pagination size is invalid")
    return urlunsplit(("https", "api.github.com", parsed.path, parsed.query, ""))


def _normalise_resource(
    payload: dict[str, Any],
    identity: GitHubThreadIdentity,
) -> GitHubThreadResource:
    raw_url = _required_string(payload, "html_url")
    if parse_github_thread_url(raw_url) != identity:
        raise GitHubThreadError("GitHub resource response changed thread identity")
    return GitHubThreadResource(
        kind=identity.kind,
        owner=identity.owner,
        repository=identity.repository,
        number=identity.number,
        title=_required_string(payload, "title"),
        body=_optional_body(payload.get("body")),
        state=_required_string(payload, "state"),
        author=_user_login(payload.get("user")),
        created_at=_required_datetime(payload, "created_at"),
        updated_at=_required_datetime(payload, "updated_at"),
        html_url=identity.url,
    )


def _normalise_comment(
    payload: dict[str, Any],
    kind: Literal["issue_comment", "review_comment"],
) -> GitHubThreadContribution:
    return GitHubThreadContribution(
        kind=kind,
        id=str(payload.get("id", "")),
        author=_user_login(payload.get("user")),
        body=_optional_body(payload.get("body")),
        created_at=_required_datetime(payload, "created_at"),
        updated_at=_required_datetime(payload, "updated_at"),
        html_url=_required_string(payload, "html_url"),
    )


def _normalise_review(payload: dict[str, Any]) -> GitHubThreadContribution:
    submitted_at = _required_datetime(payload, "submitted_at")
    return GitHubThreadContribution(
        kind="review",
        id=str(payload.get("id", "")),
        author=_user_login(payload.get("user")),
        body=_optional_body(payload.get("body")),
        created_at=submitted_at,
        updated_at=submitted_at,
        html_url=_required_string(payload, "html_url"),
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GitHubThreadError(f"GitHub response is missing required field: {key}")
    return value.strip()


def _required_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = _required_string(payload, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GitHubThreadError(f"GitHub response timestamp is invalid: {key}") from exc
    if parsed.tzinfo is None:
        raise GitHubThreadError(f"GitHub response timestamp lacks timezone: {key}")
    return parsed


def _optional_body(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GitHubThreadError("GitHub response body must be text")
    return value


def _user_login(value: object) -> str:
    if value is None:
        return "ghost"
    if not isinstance(value, dict):
        raise GitHubThreadError("GitHub response user is invalid")
    login = value.get("login")
    return login if isinstance(login, str) and login else "ghost"


def _contribution_sort_key(
    contribution: GitHubThreadContribution,
) -> tuple[datetime, str, str]:
    return contribution.created_at, contribution.kind, contribution.id


def _is_contribution_locator(value: str, source_url: str) -> bool:
    parsed = urlsplit(value)
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "github.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.query
        and bool(parsed.fragment)
        and base.casefold() == source_url
    )


def _import_snapshot(
    workspace: Path,
    snapshot: GitHubThreadSnapshot,
    *,
    save_json: Path | None = None,
    category: str,
    tags: tuple[str, ...],
    sensitivity: Sensitivity,
) -> ImportResult:
    try:
        normalized_category = SourceCategory(category)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceCategory)
        raise GitHubThreadError(f"category must be one of: {allowed}") from exc
    identity = parse_github_thread_url(snapshot.source_url)
    source_id = _source_id(identity.url)
    document = LocalDocument(
        source_uri=f"mf://source/{source_id}",
        source_path=_source_path(identity),
        media_type="text/markdown",
        category=normalized_category,
        suffix=".md",
        title=snapshot.resource.title,
        content=_render_snapshot(snapshot),
        sensitivity=sensitivity,
        tags=tuple(
            sorted(
                {
                    "github-thread",
                    f"github-{identity.kind}",
                    f"repository:{identity.owner}/{identity.repository}",
                    *(tag.strip() for tag in tags if tag.strip()),
                }
            )
        ),
    )
    validate_local_document(document)
    if save_json is not None:
        _write_saved_snapshot(save_json, snapshot)
    return import_local_document(workspace, document, source_id=source_id)


def _render_snapshot(snapshot: GitHubThreadSnapshot) -> str:
    resource = snapshot.resource
    kind = "Issue" if resource.kind == "issue" else "Pull Request"
    lines = [
        f"# {resource.title}",
        "",
        "## Thread metadata",
        "",
        f"- Type: {kind}",
        f"- State: {resource.state}",
        f"- Author: @{resource.author}",
        f"- Created: {_format_datetime(resource.created_at)}",
        f"- Updated: {_format_datetime(resource.updated_at)}",
        f"- Locator: {snapshot.source_url}",
        "",
        "## Body",
        "",
        resource.body or "No body provided.",
        "",
        "## Contributions",
        "",
    ]
    if not snapshot.contributions:
        lines.append("No comments or reviews.")
    for index, contribution in enumerate(snapshot.contributions, start=1):
        label = contribution.kind.replace("_", " ").title()
        lines.extend(
            [
                f"### {label} {index}",
                "",
                f"- Author: @{contribution.author}",
                f"- Created: {_format_datetime(contribution.created_at)}",
                f"- Updated: {_format_datetime(contribution.updated_at)}",
                f"- Locator: {contribution.html_url}",
                "",
                contribution.body or "No body provided.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _source_id(source_url: str) -> str:
    return hashlib.sha256(f"github-thread:{source_url}".encode()).hexdigest()


def _source_path(identity: GitHubThreadIdentity) -> str:
    return (
        f"github/{identity.owner}/{identity.repository}/"
        f"{'issue' if identity.kind == 'issue' else 'pull'}-{identity.number}.md"
    )


def _write_saved_snapshot(path: Path, snapshot: GitHubThreadSnapshot) -> None:
    candidate = path.expanduser()
    if candidate.suffix.lower() != ".json" or candidate.is_symlink():
        raise GitHubThreadError("saved GitHub thread path must be a real .json file")
    try:
        parent = candidate.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GitHubThreadError("saved GitHub thread directory is invalid") from exc
    if not parent.is_dir():
        raise GitHubThreadError("saved GitHub thread directory is invalid")
    destination = parent / candidate.name
    if destination.exists():
        metadata = os.lstat(destination)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise GitHubThreadError("saved GitHub thread destination is unsafe")
    rendered = (
        json.dumps(
            snapshot.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary = parent / f".{candidate.name}.tmp-{uuid.uuid4().hex}"
    descriptor = -1
    directory_fd = -1
    try:
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open(
            temporary.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        written = 0
        while written < len(rendered):
            written += os.write(descriptor, rendered[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary.name,
            destination.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.chmod(destination.name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise GitHubThreadError("saved GitHub thread could not be written safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            with suppress(FileNotFoundError):
                os.unlink(temporary.name, dir_fd=directory_fd)
            os.close(directory_fd)
