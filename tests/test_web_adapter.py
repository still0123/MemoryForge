from __future__ import annotations

import json
import socket
from http.client import HTTPMessage
from pathlib import Path

import pytest
from typer.testing import CliRunner

import memoryforge.adapters.web_adapter as web_adapter
from memoryforge.interface.cli import app
from tests.cli_helpers import review_approve_apply


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"],
)
def test_web_import_rejects_non_public_network_targets(
    monkeypatch,
    address: str,
) -> None:
    monkeypatch.setattr(
        web_adapter.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, 0, 0, "", (address, 443))
        ],
    )

    with pytest.raises(web_adapter.WebPageError, match="public network"):
        web_adapter._require_public_url("https://example.com/article")


def test_web_import_pins_the_address_validated_before_connect(monkeypatch) -> None:
    resolutions = 0
    connected: list[tuple[str, int, str]] = []

    def resolve(*_args, **_kwargs):
        nonlocal resolutions
        resolutions += 1
        address = "93.184.216.34" if resolutions == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]

    class FakeResponse:
        status = 200
        headers = HTTPMessage()
        headers["Content-Type"] = "text/plain; charset=utf-8"

        @staticmethod
        def read(_limit: int) -> bytes:
            return b"Public article content " * 8

    class FakeConnection:
        def __init__(self, host: str, port: int, address: str) -> None:
            connected.append((host, port, address))

        def request(self, *_args, **_kwargs) -> None:
            pass

        @staticmethod
        def getresponse() -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(web_adapter.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(web_adapter, "_PinnedHTTPSConnection", FakeConnection)

    page = web_adapter._fetch_web_page("https://example.com/article")

    assert page.media_type == "text/plain"
    assert resolutions == 1
    assert connected == [("example.com", 443, "93.184.216.34")]


def test_web_import_keeps_article_text_and_uses_the_normal_wiki_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    article_url = "https://example.com/posts/cache-design"
    html = """
    <html><head><title>Cache Design</title></head><body>
    <nav>Unrelated navigation text that should not enter the article.</nav>
    <article><h1>Cache Design</h1>
    <p>Cache entries expire after sixty seconds to limit stale responses.</p>
    <div><p>Requests use a namespaced key so different services do not collide.</p></div>
    <script>const ignored = 'not source material';</script></article>
    </body></html>
    """

    def fetch(url: str) -> web_adapter.FetchedWebPage:
        assert url == article_url
        return web_adapter.FetchedWebPage(url=url, media_type="text/html", content=html)

    monkeypatch.setattr(web_adapter, "_fetch_web_page", fetch)
    repository = tmp_path / "repository"
    repository.mkdir()
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    imported = runner.invoke(
        app,
        ["web-import", article_url, "--workspace", str(workspace)],
    )
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["title"] == "Cache Design"

    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    changeset_id = json.loads(staged.stdout)["changeset_id"]
    reviewed = runner.invoke(
        app,
        ["review", changeset_id, "--workspace", str(workspace)],
    )
    assert reviewed.exit_code == 0, reviewed.output
    candidates = json.loads(reviewed.stdout)["candidate_files"]
    page = next(content for path, content in candidates.items() if path != "wiki/INDEX.md")
    assert "Cache entries expire after sixty seconds" in page
    assert "Unrelated navigation" not in page

    applied = review_approve_apply(runner, changeset_id, workspace)
    assert applied.exit_code == 0, applied.output
    answer = runner.invoke(
        app,
        ["ask", "Cache entries expire", "--workspace", str(workspace)],
    )
    assert answer.exit_code == 0, answer.output
    assert "sixty seconds" in json.loads(answer.stdout)["answer"]


def test_html_import_converts_a_browser_saved_page_without_fetching(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    saved_page = repository / "article.html"
    saved_page.write_text(
        """
        <html><head><title>Retry Design</title></head><body><main>
        <h1>Retry Design</h1>
        <p>Retries stop after three attempts to avoid amplifying an upstream outage.</p>
        <p>Each retry uses bounded exponential backoff before the request is attempted again.</p>
        </main></body></html>
        """,
        encoding="utf-8",
    )
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    imported = runner.invoke(
        app,
        [
            "html-import",
            str(saved_page),
            "--url",
            "https://juejin.cn/post/7637856870833635343",
            "--workspace",
            str(workspace),
        ],
    )

    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["title"] == "Retry Design"
    staged = runner.invoke(app, ["ingest", "--pending", "--workspace", str(workspace)])
    assert staged.exit_code == 0, staged.output
    reviewed = runner.invoke(
        app,
        ["review", json.loads(staged.stdout)["changeset_id"], "--workspace", str(workspace)],
    )
    assert reviewed.exit_code == 0, reviewed.output
    candidates = json.loads(reviewed.stdout)["candidate_files"]
    page = next(content for path, content in candidates.items() if path != "wiki/INDEX.md")
    assert "Retries stop after three attempts" in page
