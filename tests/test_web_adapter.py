from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import memoryforge.web_adapter as web_adapter
from memoryforge.cli import app


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

    applied = runner.invoke(
        app,
        ["apply", changeset_id, "--approve", "--workspace", str(workspace)],
    )
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
