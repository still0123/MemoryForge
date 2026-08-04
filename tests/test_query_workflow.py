from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import memoryforge.cli as cli_module
import memoryforge.query as query_module
import memoryforge.workspace as workspace_module
from memoryforge.cli import app
from memoryforge.provider import OpenAICompatibleProvider, ProviderConfig


def test_ask_answers_from_applied_wiki_with_verifiable_citation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "When do cache entries expire?",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "answered"
    assert payload["answer"] == "Cache entries expire after sixty seconds."
    assert payload["source_id"] == imported["source_id"]
    assert payload["source_version"] == 1
    assert payload["quote"] == payload["answer"]
    start_text, end_text = payload["locator"].removeprefix("chars:").split("-")
    source_text = "# Cache policy\n\nCache entries expire after sixty seconds.\n"
    assert source_text[int(start_text) : int(end_text)] == payload["quote"]


def test_ask_prefers_cjk_phrases_over_single_character_overlap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# 统计报告\n\n统计器读取 Manifest 真值，避免把模拟实验当成真实结论。\n",
    )
    _apply_pending_source(runner, workspace)

    result = query_module.answer_question(
        workspace,
        "怎样核验实验统计不是模拟出来的？",
    )

    assert result["status"] == "answered"
    assert "Manifest" in result["answer"]
    assert result["citations"][0]["source_id"] == imported["source_id"]


def test_ask_does_not_answer_cjk_question_from_generic_overlap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Evidence\n\nThe system records verified deployment facts.\n",
    )
    _apply_pending_source(runner, workspace)

    result = query_module.answer_question(workspace, "作者的生日是什么？")

    assert result["status"] == "unknown"
    assert result["answer"] == "不知道"


def test_ask_llm_summarizes_public_evidence_and_keeps_its_citation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)
    captured: list[dict[str, object]] = []

    def transport(request) -> bytes:
        captured.append(json.loads(request.data or b""))
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "缓存会在六十秒后过期。",
                                    "citation_indexes": [0],
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")

    result = query_module.answer_question(
        workspace,
        "When do cache entries expire?",
        provider=OpenAICompatibleProvider(
            ProviderConfig("https://example.test", "test-key", "test-model"),
            transport=transport,
        ),
    )

    assert result["answer"] == "缓存会在六十秒后过期。"
    assert result["citations"][0]["source_id"] == imported["source_id"]
    assert json.loads(captured[0]["messages"][1]["content"])["facts"] == [
        {"index": 0, "quote": "Cache entries expire after sixty seconds."}
    ]


def test_ask_llm_uses_candidate_facts_after_strict_match_misses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Statistics\n\n统计器读取 Manifest 和 RunMeasurement，保证报告基于终态证据。\n",
    )
    _apply_pending_source(runner, workspace)
    strict_result = query_module.answer_question(
        workspace,
        "怎样核验实验统计不是模拟出来的？",
    )
    assert strict_result["status"] == "unknown"
    captured: list[dict[str, object]] = []

    def transport(request) -> bytes:
        captured.append(json.loads(request.data or b""))
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "统计报告必须基于终态证据。",
                                    "citation_indexes": [0],
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")

    result = query_module.answer_question(
        workspace,
        "怎样核验实验统计不是模拟出来的？",
        provider=OpenAICompatibleProvider(
            ProviderConfig("https://example.test", "test-key", "test-model"),
            transport=transport,
        ),
    )

    assert result["answer"] == "统计报告必须基于终态证据。"
    assert captured
    assert captured[0]["messages"][1]["content"].find("RunMeasurement") >= 0


def test_ask_llm_rejects_local_only_evidence_before_calling_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        local_only=True,
    )
    _apply_pending_source(runner, workspace)

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: (_ for _ in ()).throw(AssertionError("must not call provider")),
    )

    with pytest.raises(ValueError, match="public source evidence"):
        query_module.answer_question(workspace, "When do cache entries expire?", provider=provider)


def test_ask_llm_allows_local_only_evidence_when_explicitly_trusted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
        local_only=True,
    )
    _apply_pending_source(runner, workspace)
    captured: list[dict[str, object]] = []

    def transport(request) -> bytes:
        captured.append(json.loads(request.data or b""))
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"answer": "缓存六十秒后过期。", "citation_indexes": [0]}
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")

    result = query_module.answer_question(
        workspace,
        "When do cache entries expire?",
        provider=OpenAICompatibleProvider(
            ProviderConfig("https://example.test", "test-key", "test-model"),
            transport=transport,
        ),
        allow_local=True,
    )

    assert result["status"] == "answered"
    assert result["citations"][0]["source_id"] == imported["source_id"]
    assert captured


def test_ask_cli_uses_llm_only_when_explicitly_requested(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    class StubProvider:
        def answer_with_evidence(self, _messages: object) -> tuple[str, tuple[int, ...]]:
            return "缓存会在六十秒后过期。", (0,)

    monkeypatch.setenv("MEMORYFORGE_API_BASE", "https://example.test")
    monkeypatch.setenv("MEMORYFORGE_API_KEY", "test-key")
    monkeypatch.setenv("MEMORYFORGE_MODEL", "test-model")
    monkeypatch.setattr(cli_module, "OpenAICompatibleProvider", lambda _config: StubProvider())

    result = runner.invoke(
        app,
        ["ask", "When do cache entries expire?", "--llm", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["answer"] == "缓存会在六十秒后过期。"
    assert "正在基于命中的公开 Wiki 证据生成回答" in result.stderr


def test_ask_does_not_use_imported_but_unapplied_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Launch notes\n\nThe launch code is amber.\n",
    )

    result = runner.invoke(
        app,
        ["ask", "What is the launch code?", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "unknown"
    assert payload["answer"] == "不知道"


def test_ask_returns_unknown_when_stable_wiki_has_no_matching_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Deployment\n\nDeployment runs every Friday.\n",
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "What is the deployment region?",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "unknown"
    assert payload["answer"] == "不知道"


def test_ask_does_not_enumerate_wiki_when_index_has_no_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    def fail_wiki_enumeration(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ask must not enumerate the Wiki when INDEX has no candidate")

    monkeypatch.setattr(query_module.Path, "glob", fail_wiki_enumeration)

    payload = query_module.answer_question(workspace, "Where is the nebula?")

    assert payload["status"] == "unknown"
    assert payload["wiki_pages"] == []


def test_ask_stop_word_only_question_skips_fts(tmp_path: Path, monkeypatch) -> None:
    def fail_fts(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise AssertionError("stop-word-only questions must not query FTS5")

    monkeypatch.setattr(query_module, "find_applied_page_paths", fail_fts)

    payload = query_module.answer_question(tmp_path, "What is the to?", debug=True)

    assert payload["status"] == "unknown"
    assert payload["answer"] == "不知道"
    assert payload["wiki_pages"] == []
    assert payload["trace"] == []


def test_ask_uses_applied_source_fts_when_index_has_no_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Unrelated](pages/unrelated.md) — unrelated notes\n",
        encoding="utf-8",
    )

    payload = query_module.answer_question(
        workspace,
        "When do cache entries expire?",
        debug=True,
    )

    assert payload["status"] == "answered"
    assert payload["answer"] == "Cache entries expire after sixty seconds."
    assert payload["trace"][0] == {"level": "L0", "artifact": "wiki/INDEX.md"}
    assert payload["trace"][1] == {
        "level": "L0",
        "artifact": "SQLite FTS5 applied-source index",
    }


def test_ask_prefers_fts_candidate_over_a_weak_index_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)
    weak_page = workspace / "wiki/pages/weak.md"
    weak_page.write_text("# Weak match\n\nRelease notes are archived monthly.\n", encoding="utf-8")
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Cache overview](pages/weak.md) — cache notes\n",
        encoding="utf-8",
    )

    payload = query_module.answer_question(
        workspace,
        "When do cache entries expire?",
        max_pages=1,
    )

    assert payload["status"] == "answered"
    assert payload["answer"] == "Cache entries expire after sixty seconds."


def test_verify_expands_every_selected_citation(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki/pages"
    pages.mkdir(parents=True)
    first_id = "a" * 64
    second_id = "b" * 64
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Cache](pages/cache.md) — cache seconds\n",
        encoding="utf-8",
    )
    (pages / "cache.md").write_text(
        "\n".join(
            [
                "# Cache",
                "",
                "## Verified facts",
                "",
                "- Cache entries expire after sixty seconds. [^first]",
                "- Cache refresh starts after thirty seconds. [^second]",
                "",
                "## Sources",
                "",
                f"[^first]: source `{first_id}` · revision `1` · `chars:0-42`",
                f"[^second]: source `{second_id}` · revision `2` · `chars:0-45`",
                "",
            ]
        ),
        encoding="utf-8",
    )

    class StubProvider:
        def answer_with_evidence(self, _messages: object) -> tuple[str, tuple[int, ...]]:
            return "Cache expiration and refresh are both time-bound.", (0, 1)

    monkeypatch.setattr(query_module, "is_public_source_version", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        query_module,
        "read_source_excerpt",
        lambda _workspace, *, source_id, **_kwargs: f"raw:{source_id}",
    )

    payload = query_module.answer_question(
        workspace,
        "cache seconds",
        verify=True,
        provider=StubProvider(),
    )

    assert [item["text"] for item in payload["evidence"]] == [
        f"raw:{first_id}",
        f"raw:{second_id}",
    ]


def test_ask_expands_no_more_than_the_page_budget(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki/pages"
    pages.mkdir(parents=True)
    (workspace / "wiki/INDEX.md").write_text(
        "\n".join(
            [
                "# Knowledge Index",
                "",
                "- [Cache A](pages/a.md) — cache policy",
                "- [Cache B](pages/b.md) — cache policy",
                "- [Cache C](pages/c.md) — cache policy",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (pages / "a.md").write_text("# Cache A\n", encoding="utf-8")
    (pages / "b.md").write_text(
        _wiki_page("Cache entries expire after sixty seconds."),
        encoding="utf-8",
    )
    (pages / "c.md").write_text("# Cache C\n", encoding="utf-8")
    original_read_text = Path.read_text
    read_pages: list[str] = []

    def track_page_reads(path: Path, *args: object, **kwargs: object) -> str:
        if path.parent == pages:
            read_pages.append(path.name)
            if path.name == "c.md":
                raise AssertionError("lower-ranked page must not be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(query_module.Path, "read_text", track_page_reads)

    payload = query_module.answer_question(workspace, "cache", max_pages=2)

    assert payload["status"] == "answered"
    assert read_pages == ["a.md", "b.md"]


def test_ask_ignores_symlinked_index_page_outside_wiki(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki/pages"
    pages.mkdir(parents=True)
    external = tmp_path / "external.md"
    external.write_text(
        _wiki_page("Cache entries expire after zero seconds."),
        encoding="utf-8",
    )
    escaped_page = pages / "escaped.md"
    escaped_page.symlink_to(external)
    normal_page = pages / "normal.md"
    normal_page.write_text(
        _wiki_page("Cache entries expire after sixty seconds."),
        encoding="utf-8",
    )
    (workspace / "wiki/INDEX.md").write_text(
        "\n".join(
            [
                "# Knowledge Index",
                "",
                "- [Escaped cache](pages/escaped.md) — cache policy",
                "- [Normal cache](pages/normal.md) — cache policy",
                "",
            ]
        ),
        encoding="utf-8",
    )
    original_read_text = Path.read_text

    def fail_external_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == escaped_page:
            raise AssertionError("default ask must not read symlinked Wiki pages")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(query_module.Path, "read_text", fail_external_read)

    payload = query_module.answer_question(workspace, "When do cache entries expire?")

    assert payload["status"] == "answered"
    assert payload["answer"] == "Cache entries expire after sixty seconds."
    assert payload["wiki_pages"] == ["wiki/pages/normal.md"]


def test_ask_ignores_symlinked_index_outside_wiki_and_falls_back_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    wiki = workspace / "wiki"
    (wiki / "pages").mkdir(parents=True)
    external = tmp_path / "external-index.md"
    external.write_text(
        "# External index\n\n- [Leaked](pages/leaked.md) — cache policy\n",
        encoding="utf-8",
    )
    index = wiki / "INDEX.md"
    index.symlink_to(external)
    original_read_text = Path.read_text
    fts_queries: list[tuple[str, int]] = []

    def fail_index_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == index:
            raise AssertionError("ask must not read a symlinked INDEX.md")
        return original_read_text(path, *args, **kwargs)

    def empty_fts(_workspace: Path, query: str, *, limit: int) -> tuple[str, ...]:
        fts_queries.append((query, limit))
        return ()

    monkeypatch.setattr(query_module.Path, "read_text", fail_index_read)
    monkeypatch.setattr(query_module, "find_applied_page_paths", empty_fts)

    payload = query_module.answer_question(workspace, "cache", debug=True)

    assert payload["status"] == "unknown"
    assert payload["trace"] == []
    assert fts_queries == [("cache", 3)]


@pytest.mark.parametrize("max_pages", [0, 11, True])
def test_ask_rejects_invalid_page_budget(tmp_path: Path, max_pages: int) -> None:
    with pytest.raises(ValueError, match="max_pages must be an integer between 1 and 10"):
        query_module.answer_question(tmp_path, "cache", max_pages=max_pages)


@pytest.mark.parametrize("max_citations", [0, 11, True])
def test_ask_rejects_invalid_citation_budget(tmp_path: Path, max_citations: int) -> None:
    with pytest.raises(ValueError, match="max_citations must be an integer between 1 and 10"):
        query_module.answer_question(tmp_path, "cache", max_citations=max_citations)


def test_ask_can_return_multiple_citations_when_requested(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache expires after sixty seconds.\n",
    )
    second = tmp_path / "repository" / "deploy.md"
    second.write_text("# Deploy\n\nDeployment runs every Friday.\n", encoding="utf-8")
    imported_second = runner.invoke(
        app,
        ["import", str(second), "--workspace", str(workspace)],
    )
    assert imported_second.exit_code == 0, imported_second.output
    _apply_pending_source(runner, workspace)

    result = query_module.answer_question(
        workspace,
        "Cache expires sixty seconds and deployment Friday",
        max_citations=2,
    )

    assert result["status"] == "answered"
    assert len(result["citations"]) == 2
    assert "sixty seconds" in result["answer"]
    assert "Friday" in result["answer"]


def test_top_matches_prefers_a_citation_that_covers_new_terms() -> None:
    first = {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-10",
        "quote": "cache expires after sixty",
    }
    repeated = {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:10-30",
        "quote": "cache expires deployment",
    }
    second = {
        "source_id": "b" * 64,
        "source_version": 1,
        "locator": "chars:0-10",
        "quote": "deployment Friday",
    }

    selected = query_module._top_matches(
        [
            ((1, 3, 6), "wiki/pages/first.md", first),
            ((1, 3, 6), "wiki/pages/repeated.md", repeated),
            ((1, 2, 4), "wiki/pages/second.md", second),
        ],
        2,
        question_terms={"cache", "expires", "sixty", "deployment", "friday"},
    )

    assert [citation["quote"] for _, citation in selected] == [
        "cache expires after sixty",
        "deployment Friday",
    ]


def test_candidate_pages_prioritize_explicit_terms_over_generic_cjk_terms(
    tmp_path: Path,
) -> None:
    pages = tmp_path / "workspace" / "wiki" / "pages"
    pages.mkdir(parents=True)
    (pages.parent / "INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [Generic](pages/generic.md) — 系统运行问题分别解决什么问题\n"
        "- [Engine](pages/engine.md) — 本地实验引擎依赖 Redis 和 Celery\n",
        encoding="utf-8",
    )
    (pages / "generic.md").write_text("# Generic\n", encoding="utf-8")
    (pages / "engine.md").write_text("# Engine\n", encoding="utf-8")
    trace: list[query_module.TraceStep] = []

    selected = query_module._candidate_pages(
        tmp_path / "workspace",
        query_module._terms("Redis Celery 与 FailureEvidenceBundle 不重新运行分别解决什么问题？"),
        max_pages=1,
        trace=trace,
        repository_id=None,
    )

    assert selected == [pages / "engine.md"]


def test_ask_prefers_specific_configuration_fact_over_runtime_overlap(
    tmp_path: Path,
) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [Evidence](pages/target.md) — Skill 配置证据\n"
        "- [Runtime](pages/wrong.md) — Skill 运行失败\n",
        encoding="utf-8",
    )
    (pages / "target.md").write_text(
        _wiki_page("配置中声明了 Skill 不等于 Skill 生效。"),
        encoding="utf-8",
    )
    (pages / "wrong.md").write_text(
        _wiki_page("Skill 优化必须从可复现失败开始，不能把运行错误当成优化信号。"),
        encoding="utf-8",
    )

    result = query_module.answer_question(
        tmp_path,
        "只写 Skill 配置为什么还不能说明它运行过？",
    )

    assert result["status"] == "answered"
    assert "配置中声明了 Skill" in result["answer"]


def test_ask_uses_multiline_fact_rendered_in_the_wiki_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_text = "# Release schedule\n\nDeployment runs every\nFriday morning.\n"
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        source_text,
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "When does deployment run every week?",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "answered"
    start_text, end_text = payload["locator"].removeprefix("chars:").split("-")
    blob = (workspace / imported["snapshot_path"]).read_text(encoding="utf-8")
    assert payload["quote"] == "Deployment runs every Friday morning."
    assert blob[int(start_text) : int(end_text)] == "Deployment runs every\nFriday morning."
    assert payload["citations"][0]["quote"] == payload["quote"]


def test_ask_does_not_read_raw_blob_by_default(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    def fail_blob_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("default ask must not read raw blobs")

    monkeypatch.setattr(workspace_module, "_read_blob_bytes", fail_blob_read)
    result = runner.invoke(
        app,
        ["ask", "When do cache entries expire?", "--workspace", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["answer"] == "Cache entries expire after sixty seconds."


def test_ask_rejects_unsupported_as_of_instead_of_using_current_wiki(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    result = runner.invoke(
        app,
        [
            "ask",
            "When do cache entries expire?",
            "--as-of",
            "2026-01-01",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 2
    assert "--as-of is not supported" in result.output


def test_ask_cli_rejects_invalid_page_budget(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["ask", "cache", "--max-pages", "0", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "Invalid value" in result.output


def _workspace_with_imported_source(
    tmp_path: Path,
    monkeypatch,
    source_text: str,
    *,
    local_only: bool = False,
) -> tuple[CliRunner, Path, dict[str, Any]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "note.md"
    source.write_text(source_text, encoding="utf-8")
    workspace = repository / "workspace"
    monkeypatch.chdir(repository)
    runner = CliRunner()

    initialized = runner.invoke(app, ["init", str(workspace)])
    arguments = ["import", str(source)]
    if local_only:
        arguments.append("--local-only")
    arguments.extend(["--workspace", str(workspace)])
    imported = runner.invoke(app, arguments)

    assert initialized.exit_code == 0
    assert imported.exit_code == 0
    return runner, workspace, json.loads(imported.stdout)


def _apply_pending_source(runner: CliRunner, workspace: Path) -> None:
    ingested = runner.invoke(
        app,
        ["ingest", "--pending", "--workspace", str(workspace)],
    )
    assert ingested.exit_code == 0
    changeset_id = json.loads(ingested.stdout)["changeset_id"]

    applied = runner.invoke(
        app,
        [
            "apply",
            changeset_id,
            "--approve",
            "--workspace",
            str(workspace),
        ],
    )
    assert applied.exit_code == 0


def _wiki_page(quote: str) -> str:
    source_id = "a" * 64
    return "\n".join(
        [
            "---",
            "type: concept",
            "---",
            "# Cache B",
            "",
            "## Verified facts",
            f"- {quote} [^fact-1]",
            "",
            f"[^fact-1]: source `{source_id}` · revision `1` · `chars:0-1`",
            "",
        ]
    )
