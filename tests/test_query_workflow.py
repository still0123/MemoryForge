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
from memoryforge.provider import (
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderUnavailableError,
)


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


def test_ask_uses_top_page_summary_for_cjk_paraphrase(tmp_path: Path) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    target_quote = (
        "统计器只读取 Manifest 真值和选中 Attempt 的 RunMeasurement，"
        "避免把进行中的实验包装成最终结论。"
    )
    competing_quote = "真实 Agent 实验不会把模拟结果包装成真实证据。"
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        f"- [Statistics](pages/a-statistics.md) — {target_quote}\n"
        f"- [Agent](pages/b-agent.md) — {competing_quote}\n",
        encoding="utf-8",
    )
    (pages / "a-statistics.md").write_text(_wiki_page(target_quote), encoding="utf-8")
    (pages / "b-agent.md").write_text(_wiki_page(competing_quote), encoding="utf-8")

    result = query_module.answer_question(
        tmp_path,
        "怎样核验实验统计不是模拟出来的？",
    )

    assert result["status"] == "answered"
    assert result["answer"] == target_quote
    assert result["wiki_pages"] == ["wiki/pages/a-statistics.md"]


def test_ask_does_not_relax_top_summary_without_matching_negation(tmp_path: Path) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    quote = "系统不调用付费模型，所有本地实验结果均为模拟数据。"
    (tmp_path / "wiki/INDEX.md").write_text(
        f"# Knowledge Index\n\n- [System](pages/system.md) — {quote}\n",
        encoding="utf-8",
    )
    (pages / "system.md").write_text(_wiki_page(quote), encoding="utf-8")

    result = query_module.answer_question(
        tmp_path,
        "系统使用什么 GPU 型号训练模型？",
    )

    assert result["status"] == "unknown"


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


def test_ask_llm_rejects_heading_only_candidate_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# MemoryForge\n\n这是一个把工程资料编译成可追溯 Wiki 的项目。\n",
    )
    _apply_pending_source(runner, workspace)
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: (_ for _ in ()).throw(
            AssertionError("heading-only candidates must not reach the model")
        ),
    )

    result = query_module.answer_question(
        workspace,
        "MemoryForge 的作者出生在哪一天？",
        provider=provider,
    )

    assert result["status"] == "unknown"
    assert result["answer"] == "不知道"


def test_mixed_language_code_symbol_is_direct_model_evidence() -> None:
    citation: query_module.CitationPayload = {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-21",
        "quote": "func CreateFileSystem",
        "section_path": "storage/accounts/create_bucket.go",
    }

    assert query_module._has_direct_evidence(
        query_module._terms("CreateFileSystem 的创建流程是什么？"),
        citation,
    )

    module_citation: query_module.CitationPayload = {
        **citation,
        "quote": "`storage/accounts`: 80 files",
        "section_path": "Code module: storage / Directories",
    }
    assert query_module._has_direct_evidence(
        query_module._terms("storage 文件夹主要作用是什么？"),
        module_citation,
    )


def test_ask_routes_module_question_to_module_structure_page(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Code module: storage\n\n"
        "This module contains 198 tracked Go files.\n\n"
        "## Exported capabilities\n\n"
        "- Main exported operations in `storage`: `CreateBucket`, `DescribeBuckets`\n"
        "- `storage/ops` exports code symbols: `CreateJob`, `DeleteJob`, `UpdateQuota`\n"
        "- `storage/accounts` exports code symbols: `CreateBucket`, `DescribeBuckets`\n\n"
        "## Directories\n\n"
        "- `storage/ops`: 61 files\n"
        "- `storage/accounts`: 136 files\n",
    )
    _apply_pending_source(runner, workspace)
    captured: list[dict[str, object]] = []

    def transport(request) -> bytes:
        payload = json.loads(request.data or b"")
        captured.append(payload)
        facts = json.loads(payload["messages"][1]["content"])["facts"]
        indexes = [fact["index"] for fact in facts if fact["quote"].startswith("`storage/")]
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "storage 按运维操作和账户接口组织代码。",
                                    "citation_indexes": indexes,
                                }
                            )
                        }
                    }
                ]
            }
        ).encode()

    result = query_module.answer_question(
        workspace,
        "storage 文件夹主要作用是什么？",
        provider=OpenAICompatibleProvider(
            ProviderConfig("https://example.test", "test-key", "test-model"),
            transport=transport,
        ),
    )

    assert result["answer"] == "storage 按运维操作和账户接口组织代码。"
    facts = json.loads(captured[0]["messages"][1]["content"])["facts"]
    assert {fact["quote"] for fact in facts} >= {
        "`storage/ops`: 61 files",
        "`storage/accounts`: 136 files",
    }
    assert any("CreateBucket" in fact["quote"] for fact in facts)
    assert "translate and summarize" in captured[0]["messages"][0]["content"]


def test_ask_understands_chinese_child_module_question(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Code module: storage\n\n## Child modules\n\n- `storage/ops`\n- `storage/accounts`\n",
    )
    _apply_pending_source(runner, workspace)

    result = query_module.answer_question(workspace, "storage 有哪些子模块？")

    assert result["status"] == "answered"
    assert "storage/ops" in result["answer"]
    assert "storage/accounts" in result["answer"]


def test_module_question_falls_back_to_exported_operations_when_provider_times_out(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Code module: storage\n\n"
        "## Exported capabilities\n\n"
        "- Main exported operations in `storage`: `CreateBucket`, `DescribeBuckets`\n",
    )
    _apply_pending_source(runner, workspace)
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: (_ for _ in ()).throw(
            ProviderUnavailableError("provider timed out")
        ),
    )

    result = query_module.answer_question(
        workspace,
        "storage 文件夹主要作用是什么？",
        provider=provider,
    )

    assert result["status"] == "answered"
    assert result["model_status"] == "fallback"
    assert result["answer"].startswith("storage 模块主要导出这些操作：")
    assert result["citations"][0]["quote"].startswith("Main exported operations")


def test_module_question_prefers_the_exact_parent_card(tmp_path: Path, monkeypatch) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Code module: services/accounts\n\n"
        "## Responsibilities\n\n"
        "- Main exported operations in `services/accounts`: `CreateAccount`, `DescribeAccount`\n",
    )
    _apply_pending_source(runner, workspace)
    child = workspace.parent / "child.md"
    child.write_text(
        "# Code module: services/accounts/storage\n\n"
        "## Responsibilities\n\n"
        "- Main exported operations in `services/accounts/storage`: `CreateBucket`\n",
        encoding="utf-8",
    )
    imported = runner.invoke(
        cli_module.app,
        ["import", str(child), "--workspace", str(workspace)],
    )
    assert imported.exit_code == 0, imported.output
    _apply_pending_source(runner, workspace)
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: (_ for _ in ()).throw(
            ProviderUnavailableError("provider timed out")
        ),
    )

    result = query_module.answer_question(
        workspace,
        "services/accounts 模块主要负责什么？",
        provider=provider,
        max_pages=5,
    )

    assert result["answer"].startswith("services/accounts 模块主要导出这些操作：")
    assert "CreateAccount" in result["answer"]
    assert "CreateBucket" not in result["answer"]


def test_ask_uses_two_facts_for_a_two_part_question(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Boundary](pages/boundary.md) — 本地资料与模型权限\n",
        encoding="utf-8",
    )
    source_id = "b" * 64
    (pages / "boundary.md").write_text(
        "---\n"
        "type: concept\n"
        "---\n"
        "# Boundary\n\n"
        "## Verified facts\n\n"
        "### 本地资料 / 模型权限\n\n"
        "- 本地资料默认标记为 `local_only`，模型不能读取。 [^source-1]\n"
        "- 只有传入 `--allow-local-llm` 后，模型才可以读取命中的本地资料。 [^source-2]\n\n"
        f"[^source-1]: source `{source_id}` · revision `1` · `chars:0-30`\n"
        f"[^source-2]: source `{source_id}` · revision `1` · `chars:31-70`\n",
        encoding="utf-8",
    )

    result = query_module.answer_question(
        workspace,
        "本地资料默认如何保护，什么时候才允许模型读取？",
    )

    assert result["status"] == "answered"
    assert len(result["citations"]) == 2
    assert "local_only" in result["answer"]
    assert "--allow-local-llm" in result["answer"]


def test_ask_expands_unavailable_to_a_citable_failure_fact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Model](pages/model.md) — 在线模型配置与回退\n",
        encoding="utf-8",
    )
    source_id = "c" * 64
    (pages / "model.md").write_text(
        "---\n"
        "type: concept\n"
        "---\n"
        "# Model\n\n"
        "## Verified facts\n\n"
        "### 在线模型\n\n"
        "- 在线模型只是可选增强能力。 [^source-1]\n"
        "- 在线接口超时或响应异常时，系统自动回退到本地分析。 [^source-2]\n\n"
        "### 远程模型不可用\n\n"
        "- 本地模式不受影响。 [^source-3]\n\n"
        f"[^source-1]: source `{source_id}` · revision `1` · `chars:0-18`\n"
        f"[^source-2]: source `{source_id}` · revision `1` · `chars:19-50`\n"
        f"[^source-3]: source `{source_id}` · revision `1` · `chars:51-62`\n",
        encoding="utf-8",
    )

    result = query_module.answer_question(workspace, "在线模型不可用时会怎样？")

    assert result["status"] == "answered"
    assert "回退到本地分析" in result["answer"]


def test_ask_prefers_environment_assignment_for_environment_variable_question(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Model](pages/model.md) — 在线模型配置\n",
        encoding="utf-8",
    )
    source_id = "d" * 64
    (pages / "model.md").write_text(
        "---\n"
        "type: concept\n"
        "---\n"
        "# Model\n\n"
        "## Verified facts\n\n"
        "### 在线模型\n\n"
        "- 在线模型为可选增强能力。 [^source-1]\n"
        "- export AD_VIDEO_LLM_ENABLED=1 export AD_VIDEO_LLM_API_KEY=example [^source-2]\n\n"
        f"[^source-1]: source `{source_id}` · revision `1` · `chars:0-16`\n"
        f"[^source-2]: source `{source_id}` · revision `1` · `chars:17-80`\n",
        encoding="utf-8",
    )

    result = query_module.answer_question(workspace, "启用在线模型要设置哪些环境变量？")

    assert result["status"] == "answered"
    assert "AD_VIDEO_LLM_ENABLED" in result["answer"]
    assert "AD_VIDEO_LLM_API_KEY" in result["answer"]


def test_ask_uses_cjk_question_focus_to_choose_the_right_environment_assignment(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Settings](pages/settings.md) — 广告视频系统模型配置\n",
        encoding="utf-8",
    )
    source_id = "e" * 64
    (pages / "settings.md").write_text(
        "---\n"
        "type: concept\n"
        "---\n"
        "# Settings\n\n"
        "## Verified facts\n\n"
        "### 广告视频分析系统 / 准备环境\n\n"
        "- export JAVA_HOME=/jdk17 [^source-1]\n\n"
        "### 在线模型\n\n"
        "- export AD_VIDEO_LLM_ENABLED=1 export AD_VIDEO_LLM_API_KEY=example [^source-2]\n\n"
        f"[^source-1]: source `{source_id}` · revision `1` · `chars:0-24`\n"
        f"[^source-2]: source `{source_id}` · revision `1` · `chars:25-90`\n",
        encoding="utf-8",
    )

    result = query_module.answer_question(
        workspace,
        "广告视频分析系统的在线模型要设置哪些环境变量？",
    )

    assert result["status"] == "answered"
    assert "AD_VIDEO_LLM_ENABLED" in result["answer"]
    assert "JAVA_HOME" not in result["answer"]


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
        {
            "index": 0,
            "quote": "Cache entries expire after sixty seconds.",
            "section": "Cache policy",
        }
    ]


def test_ask_llm_falls_back_to_verified_wiki_when_provider_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, imported = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)

    def unavailable(_request) -> bytes:
        raise ProviderUnavailableError("provider temporarily unavailable (HTTP 503)")

    result = query_module.answer_question(
        workspace,
        "When do cache entries expire?",
        provider=OpenAICompatibleProvider(
            ProviderConfig("https://example.test", "test-key", "test-model"),
            transport=unavailable,
        ),
    )

    assert result["answer"] == "Cache entries expire after sixty seconds."
    assert result["model_status"] == "fallback"
    assert result["citations"][0]["source_id"] == imported["source_id"]


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
        "怎样核验统计结论可信？",
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
        "怎样核验统计结论可信？",
        provider=OpenAICompatibleProvider(
            ProviderConfig("https://example.test", "test-key", "test-model"),
            transport=transport,
        ),
    )

    assert result["answer"] == "统计报告必须基于终态证据。"
    assert captured
    assert captured[0]["messages"][1]["content"].find("RunMeasurement") >= 0
    assert "question's condition or conclusion" in captured[0]["messages"][0]["content"]


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


def test_ask_uses_any_query_term_to_route_a_natural_language_question(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Agent\n\nMiniClaude Agent exposes search, read, and final tools.\n",
    )
    _apply_pending_source(runner, workspace)
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Unrelated](pages/unrelated.md) — unrelated notes\n",
        encoding="utf-8",
    )

    payload = query_module.answer_question(
        workspace,
        "Which MiniClaude Agent tools are available now?",
    )

    assert payload["status"] == "answered"
    assert "search, read, and final" in payload["answer"]


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
    fts_queries: list[tuple[str, int, bool]] = []

    def fail_index_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == index:
            raise AssertionError("ask must not read a symlinked INDEX.md")
        return original_read_text(path, *args, **kwargs)

    def empty_fts(
        _workspace: Path,
        query: str,
        *,
        limit: int,
        repository_id: str | None = None,
        require_all_terms: bool = True,
    ) -> tuple[str, ...]:
        fts_queries.append((query, limit, require_all_terms))
        return ()

    monkeypatch.setattr(query_module.Path, "read_text", fail_index_read)
    monkeypatch.setattr(query_module, "find_applied_page_paths", empty_fts)

    payload = query_module.answer_question(workspace, "cache", debug=True)

    assert payload["status"] == "unknown"
    assert payload["trace"] == []
    assert fts_queries == [("cache", 3, True), ("cache", 3, False)]


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


def test_rank_matches_prefers_summary_but_only_when_fact_terms_match() -> None:
    summary = {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-20",
        "quote": "The service stores Wiki facts.",
        "section_path": "Unrelated module overview",
    }
    detail = {
        "source_id": "b" * 64,
        "source_version": 1,
        "locator": "chars:0-20",
        "quote": "The service stores Wiki facts.",
        "section_path": "Wiki details",
    }

    ranked = query_module._rank_matches(
        [
            (frozenset({"service"}), True, "wiki/pages/summary.md", summary),
            (frozenset({"service"}), False, "wiki/pages/detail.md", detail),
        ],
        question_terms={"service", "module"},
        prefer_code_modules=False,
    )

    assert [page_path for _, page_path, _ in ranked] == [
        "wiki/pages/summary.md",
        "wiki/pages/detail.md",
    ]
    assert query_module._matching_terms({"service", "module"}, summary) == {"service"}
    assert query_module._direct_matching_terms({"service", "module"}, summary) == {"service"}


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
        "Redis Celery 与 FailureEvidenceBundle 不重新运行分别解决什么问题？",
        query_module._terms("Redis Celery 与 FailureEvidenceBundle 不重新运行分别解决什么问题？"),
        max_pages=1,
        trace=trace,
        repository_id=None,
    )

    assert selected == [pages / "engine.md"]


def test_candidate_pages_fill_the_remaining_budget_with_relaxed_fts_matches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pages = tmp_path / "workspace" / "wiki" / "pages"
    pages.mkdir(parents=True)
    (pages.parent / "INDEX.md").write_text("# Knowledge Index\n", encoding="utf-8")
    index_path = tmp_path / "workspace" / ".memoryforge" / "index.sqlite"
    index_path.parent.mkdir()
    index_path.touch()
    strict_page = pages / "strict.md"
    relaxed_page = pages / "relaxed.md"
    strict_page.write_text("# Strict\n", encoding="utf-8")
    relaxed_page.write_text("# Relaxed\n", encoding="utf-8")
    calls: list[tuple[int, bool]] = []

    def fake_fts(
        _workspace: Path,
        _question: str,
        *,
        limit: int,
        repository_id: str | None = None,
        require_all_terms: bool = True,
    ) -> tuple[str, ...]:
        calls.append((limit, require_all_terms))
        return ("wiki/pages/strict.md",) if require_all_terms else ("wiki/pages/relaxed.md",)

    monkeypatch.setattr(query_module, "find_applied_page_paths", fake_fts)

    selected = query_module._candidate_pages(
        tmp_path / "workspace",
        "cache policy expires",
        {"cache", "policy", "expires"},
        max_pages=2,
        trace=[],
        repository_id=None,
    )

    assert selected == [strict_page, relaxed_page]
    assert calls == [(2, True), (2, False)]


def test_candidate_pages_prefers_index_routes_before_relaxed_fts_matches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    index_page = pages / "statistics.md"
    broad_page = pages / "broad.md"
    another_broad_page = pages / "another-broad.md"
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [Statistics](pages/statistics.md) — Manifest RunMeasurement report truth\n",
        encoding="utf-8",
    )
    for page in (index_page, broad_page, another_broad_page):
        page.write_text("# Page\n", encoding="utf-8")
    index_path = workspace / ".memoryforge" / "index.sqlite"
    index_path.parent.mkdir()
    index_path.touch()
    calls: list[bool] = []

    def fake_fts(
        _workspace: Path,
        _question: str,
        *,
        limit: int,
        repository_id: str | None = None,
        require_all_terms: bool = True,
    ) -> tuple[str, ...]:
        calls.append(require_all_terms)
        if require_all_terms:
            return ()
        return ("wiki/pages/broad.md", "wiki/pages/another-broad.md")

    monkeypatch.setattr(query_module, "find_applied_page_paths", fake_fts)

    selected = query_module._candidate_pages(
        workspace,
        "Manifest RunMeasurement Provider",
        {"manifest", "runmeasurement", "provider"},
        max_pages=2,
        trace=[],
        repository_id=None,
        prefer_index_routes=True,
    )

    assert selected == [index_page, broad_page]
    assert calls == [True, False]


def test_candidate_pages_downweights_terms_shared_by_every_document(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki/pages"
    pages.mkdir(parents=True)
    generic = pages / "generic.md"
    target = pages / "target.md"
    generic.write_text(_wiki_page("Uvicorn server configuration."), encoding="utf-8")
    target.write_text(_wiki_page("Uvicorn uvloop event loop selection."), encoding="utf-8")
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [Generic](pages/generic.md) — Uvicorn server configuration\n",
        encoding="utf-8",
    )
    index_path = workspace / ".memoryforge/index.sqlite"
    index_path.parent.mkdir()
    index_path.touch()
    monkeypatch.setattr(query_module, "find_applied_page_paths", lambda *_args, **_kwargs: ())

    selected = query_module._candidate_pages(
        workspace,
        "How does Uvicorn choose uvloop?",
        query_module._terms("How does Uvicorn choose uvloop?"),
        max_pages=1,
        trace=[],
        repository_id=None,
        prefer_index_routes=True,
    )

    assert selected == [target]


def test_ask_keeps_the_original_question_when_querying_fts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    calls: list[tuple[str, int, bool]] = []

    def empty_fts(
        _workspace: Path,
        query: str,
        *,
        limit: int,
        repository_id: str | None = None,
        require_all_terms: bool = True,
    ) -> tuple[str, ...]:
        calls.append((query, limit, require_all_terms))
        return ()

    question = "AgentSkill-Eval 中哪个 RunnerAdapter 用于生产执行？"
    monkeypatch.setattr(query_module, "find_applied_page_paths", empty_fts)

    result = query_module.answer_question(workspace, question)

    assert result["status"] == "unknown"
    assert calls == [(question, 3, True), (question, 3, False)]


def test_terms_match_camel_case_identifier_suffixes() -> None:
    question_terms = query_module._terms("RunnerAdapter")
    fact_terms = query_module._terms("SkillUpRunnerAdapter")

    assert "runneradapter" in question_terms & fact_terms


def test_ask_prefers_a_camel_case_identifier_over_project_background(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    (workspace / "wiki/INDEX.md").write_text("# Knowledge Index\n", encoding="utf-8")
    index_path = workspace / ".memoryforge" / "index.sqlite"
    index_path.parent.mkdir()
    index_path.touch()
    background_page = pages / "background.md"
    adapter_page = pages / "adapter.md"
    background_page.write_text(
        _wiki_page("AgentSkill-Eval explains the project background."),
        encoding="utf-8",
    )
    adapter_page.write_text(
        _wiki_page("SkillUpRunnerAdapter 用于生产执行。"),
        encoding="utf-8",
    )

    def fake_fts(
        _workspace: Path,
        _question: str,
        *,
        limit: int,
        repository_id: str | None = None,
        require_all_terms: bool = True,
    ) -> tuple[str, ...]:
        return ("wiki/pages/background.md", "wiki/pages/adapter.md")

    monkeypatch.setattr(query_module, "find_applied_page_paths", fake_fts)

    result = query_module.answer_question(
        workspace,
        "AgentSkill-Eval 中哪个 RunnerAdapter 用于生产执行？",
    )

    assert result["answer"] == "SkillUpRunnerAdapter 用于生产执行。"


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


def test_ask_prefers_a_specific_cjk_fact_over_repeated_generic_terms(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_text = (
        "# 广告视频系统\n\n"
        "广告视频分析系统会整理素材和证据。\n\n"
        "项目默认使用本地证据分析，不配置外部大模型也能启动。\n\n"
        "| 模式 | 外部依赖 | 用途 |\n|---|---|---|\n| 本地证据分析 | 无 | 默认模式 |\n\n"
        "广告视频分析系统支持素材管理和内容检索。\n"
        "\n外部大模型只是默认模式的可选增强。\n"
    )
    runner, workspace, _ = _workspace_with_imported_source(tmp_path, monkeypatch, source_text)
    _apply_pending_source(runner, workspace)

    result = query_module.answer_question(workspace, "广告视频分析系统默认依赖外部大模型吗？")

    assert result["status"] == "answered"
    assert "不配置外部大模型" in result["answer"]


def test_ask_uses_verified_fact_section_path_for_cjk_routing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Settings](pages/settings.md) — 在线模型回退策略\n",
        encoding="utf-8",
    )
    source_id = "a" * 64
    (pages / "settings.md").write_text(
        "---\n"
        "type: concept\n"
        "---\n"
        "# Settings\n\n"
        "## Verified facts\n\n"
        "### 在线模型 / 回退\n\n"
        "- 在线接口超时后，系统自动回退到本地分析。 [^source-1]\n\n"
        "### 远程 Embeddings\n\n"
        "- 检索服务不可用时，系统继续使用本地短语和规则。 [^source-2]\n\n"
        f"[^source-1]: source `{source_id}` · revision `1` · `chars:0-20`\n"
        f"[^source-2]: source `{source_id}` · revision `1` · `chars:21-40`\n",
        encoding="utf-8",
    )

    result = query_module.answer_question(workspace, "在线模型不可用时如何回退？")

    assert result["status"] == "answered"
    assert result["answer"] == "在线接口超时后，系统自动回退到本地分析。"
    assert result["citations"][0]["section_path"] == "在线模型 / 回退"


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
