"""Import one readable public web page through the normal source pipeline."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from http.client import HTTPMessage
from pathlib import Path
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from memoryforge.adapters.importer import import_local_document, read_local_text_file
from memoryforge.core.errors import MemoryForgeError
from memoryforge.core.models import ImportResult, LocalDocument, Sensitivity, SourceCategory

_MAX_WEB_BYTES = 5 * 1024 * 1024
_BLOCK_TAGS = {"article", "blockquote", "br", "div", "h1", "h2", "h3", "li", "p", "pre"}
_SKIPPED_TAGS = {"script", "style", "svg", "template"}
_ARTICLE_MARKERS = ("article-content", "article-body", "post-content", "post-body")
_HTML_SUFFIXES = frozenset({".html", ".htm"})


class WebPageError(MemoryForgeError):
    """Raised when an explicit public URL cannot be imported as readable text."""


@dataclass(frozen=True)
class FetchedWebPage:
    url: str
    media_type: str
    content: str


class _PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            _require_public_url(newurl),
        )


def import_web_page(
    workspace: Path,
    url: str,
    *,
    category: str = "refs",
    tags: tuple[str, ...] = (),
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> ImportResult:
    """Fetch one static HTTP(S) page and store a Markdown snapshot of its readable text."""
    try:
        normalized_category = SourceCategory(category)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceCategory)
        raise WebPageError(f"category must be one of: {allowed}") from exc

    source_url = _normalise_url(url)
    page = _fetch_web_page(source_url)
    return _import_fetched_page(
        workspace,
        source_url,
        page,
        category=normalized_category,
        tags=tags,
        sensitivity=sensitivity,
    )


def import_html_file(
    workspace: Path,
    html_path: Path,
    *,
    url: str,
    category: str = "refs",
    tags: tuple[str, ...] = (),
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
    source_root: Path | None = None,
) -> ImportResult:
    """Convert one browser-saved HTML file to a Markdown source without using browser state."""
    try:
        normalized_category = SourceCategory(category)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SourceCategory)
        raise WebPageError(f"category must be one of: {allowed}") from exc
    source_url = _normalise_url(url)
    content = read_local_text_file(
        html_path,
        source_root=source_root if source_root is not None else Path.cwd(),
        allowed_suffixes=_HTML_SUFFIXES,
    )
    return _import_fetched_page(
        workspace,
        source_url,
        FetchedWebPage(url=source_url, media_type="text/html", content=content),
        category=normalized_category,
        tags=tags,
        sensitivity=sensitivity,
    )


def _import_fetched_page(
    workspace: Path,
    source_url: str,
    page: FetchedWebPage,
    *,
    category: SourceCategory,
    tags: tuple[str, ...],
    sensitivity: Sensitivity,
) -> ImportResult:
    title, body = _readable_page(page)
    source_id = hashlib.sha256(f"web:{source_url}".encode()).hexdigest()
    host = urlsplit(source_url).hostname or "web"
    document = LocalDocument(
        source_uri=f"mf://source/{source_id}",
        source_path=f"web/{host}/{source_id[:12]}.md",
        media_type="text/markdown",
        category=category,
        suffix=".md",
        title=title,
        content=(f"# {title}\n\n{body}\n\n## Web source\n\n{page.url}\n"),
        sensitivity=sensitivity,
        tags=tuple(sorted({"web", *(tag.strip() for tag in tags if tag.strip())})),
    )
    return import_local_document(workspace, document, source_id=source_id)


def _fetch_web_page(url: str) -> FetchedWebPage:
    url = _require_public_url(url)
    request = Request(
        url,
        headers={
            "Accept": "text/html, text/plain;q=0.9",
            "User-Agent": "Mozilla/5.0 (compatible; MemoryForge/0.1)",
        },
    )
    try:
        with build_opener(_PublicRedirectHandler()).open(request, timeout=20) as response:
            media_type = response.headers.get_content_type()
            if media_type not in {"text/html", "text/plain"}:
                raise WebPageError("web page must return text/html or text/plain")
            raw = response.read(_MAX_WEB_BYTES + 1)
            final_url = _require_public_url(response.geturl())
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise WebPageError(f"web page request failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise WebPageError(f"web page request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise WebPageError("web page request timed out after 20 seconds") from exc
    if len(raw) > _MAX_WEB_BYTES:
        raise WebPageError("web page exceeds the 5 MiB import limit")
    try:
        content = raw.decode(charset)
    except (LookupError, UnicodeDecodeError) as exc:
        raise WebPageError("web page is not readable text") from exc
    return FetchedWebPage(url=final_url, media_type=media_type, content=content)


def _normalise_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise WebPageError("web page must be a public HTTP(S) URL without credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise WebPageError("web page URL has an invalid port") from exc
    host = parsed.hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{port}" if port is not None else rendered_host
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _require_public_url(value: str) -> str:
    url = _normalise_url(value)
    parsed = urlsplit(url)
    try:
        addresses = {
            ipaddress.ip_address(address[4][0])
            for address in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, ValueError) as exc:
        raise WebPageError("web page host cannot be resolved safely") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise WebPageError("web page must resolve only to public network addresses")
    return url


def _readable_page(page: FetchedWebPage) -> tuple[str, str]:
    if page.media_type == "text/plain":
        body = _normalise_text(page.content)
        title = body.splitlines()[0] if body else "Web page"
    else:
        parser = _ReadableHTMLParser()
        parser.feed(page.content)
        parser.close()
        title = parser.title or urlsplit(page.url).hostname or "Web page"
        body = parser.article_text or parser.all_text
    title = re.sub(r"\s+", " ", title).strip()
    body = _remove_repeated_title(_normalise_text(body), title)
    if len(body) < 80:
        raise WebPageError(
            "web page has no readable article body; it may require JavaScript, login, or CAPTCHA"
        )
    return title, body


def _normalise_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    nonempty = [line for line in lines if line]
    return "\n\n".join(
        line for index, line in enumerate(nonempty) if index == 0 or line != nonempty[index - 1]
    )


def _remove_repeated_title(body: str, title: str) -> str:
    lines = body.split("\n\n")
    if lines and lines[0].casefold() == title.casefold():
        return "\n\n".join(lines[1:])
    return body


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._all: list[str] = []
        self._article: list[str] = []
        self._article_roots: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._title: list[str] = []
        self._meta_title: str | None = None

    @property
    def title(self) -> str:
        return self._meta_title or " ".join(self._title).strip()

    @property
    def all_text(self) -> str:
        return "".join(self._all)

    @property
    def article_text(self) -> str:
        return "".join(self._article)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("property") == "og:title":
            title = attributes.get("content")
            if title:
                self._meta_title = title
        if tag == "title":
            self._in_title = True
            return
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if _is_article_root(tag, attributes):
            self._article_roots.append(tag)
        if tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._append("\n")
        if self._article_roots and tag == self._article_roots[-1]:
            self._article_roots.pop()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        if not self._skip_depth and not self._in_title:
            self._append(data)

    def _append(self, value: str) -> None:
        self._all.append(value)
        if self._article_roots:
            self._article.append(value)


def _is_article_root(tag: str, attributes: dict[str, str | None]) -> bool:
    if tag in {"article", "main"}:
        return True
    marker = " ".join(
        value for value in (attributes.get("id"), attributes.get("class")) if value
    ).lower()
    return any(item in marker for item in _ARTICLE_MARKERS)
