from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import memoryforge.interface.cli as cli_module
import memoryforge.query.query as query_module
import memoryforge.storage.workspace as workspace_module
from memoryforge.compiler.egress_policy import upsert_rule
from memoryforge.core.egress_models import EgressClass, SourceEgressRule
from memoryforge.interface.cli import app
from memoryforge.portal.local_portal import LocalPortalApp
from memoryforge.query.agent_access import query_workspace_context
from memoryforge.query.provider import (
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderUnavailableError,
)
from tests.cli_helpers import review_approve_apply


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


def test_code_page_answers_cjk_paraphrase_from_two_english_facts(tmp_path: Path) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    source_id = "a" * 64
    page = (
        "---\n"
        'title: "Code: common/standard_page.go"\n'
        "type: concept\n"
        'summary: "Pagination offset and limit conversion."\n'
        f'sources: ["{source_id}"]\n'
        "---\n"
        "# Pagination\n\n"
        "## Verified symbols\n\n"
        "### TransformPageToOffsetLimit\n\n"
        "- `offset = int((pageNumber - 1) * pageSize)` [^source-1]\n"
        "- `limit = int(pageSize)` [^source-2]\n\n"
        f"[^source-1]: source `{source_id}` · revision `1` · `chars:0-41`\n"
        f"[^source-2]: source `{source_id}` · revision `1` · `chars:42-63`\n"
    )
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [Code: common/standard_page.go](pages/page.md) — "
        "Pagination offset and limit conversion.\n",
        encoding="utf-8",
    )
    (pages / "page.md").write_text(page, encoding="utf-8")

    result = query_module.answer_question(
        tmp_path,
        "分页页码和每页数量怎样换算成数据库查询的 offset 与 limit？",
    )

    assert result["status"] == "answered"
    assert "offset = int((pageNumber - 1) * pageSize)" in result["answer"]
    assert "limit = int(pageSize)" in result["answer"]


def test_exact_symbol_routes_without_code_kind_keyword(tmp_path: Path, monkeypatch) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    (tmp_path / ".memoryforge").mkdir()
    (tmp_path / ".memoryforge/index.sqlite").touch()
    source_id = "a" * 64
    target_page = (
        "---\n"
        'title: "Code: common/standard_page.go"\n'
        "type: concept\n"
        'summary: "Pagination offset and limit conversion."\n'
        f'sources: ["{source_id}"]\n'
        "---\n"
        "# Pagination\n\n"
        "## Verified symbols\n\n"
        "### TransformPageToOffsetLimit\n\n"
        "- `func TransformPageToOffsetLimit(pageNumber, pageSize int32)` [^source-1]\n"
        "- `pageSize = 10` when the supplied size is invalid. [^source-2]\n"
        "- `offset = int((pageNumber - 1) * pageSize)` and `limit = int(pageSize)`. "
        "[^source-3]\n\n"
        f"[^source-1]: source `{source_id}` · revision `1` · `chars:0-58`\n"
        f"[^source-2]: source `{source_id}` · revision `1` · `chars:59-126`\n"
        f"[^source-3]: source `{source_id}` · revision `1` · `chars:127-194`\n"
    )
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Tests](pages/unrelated.md) — TestTransformPageToOffsetLimit.\n",
        encoding="utf-8",
    )
    (pages / "target.md").write_text(target_page, encoding="utf-8")
    (pages / "unrelated.md").write_text(
        _wiki_page("func TestTransformPageToOffsetLimit"),
        encoding="utf-8",
    )
    monkeypatch.setattr(query_module, "find_applied_page_paths", lambda *_args, **_kwargs: ())

    class StubProvider:
        def answer_with_evidence(self, messages: object) -> tuple[str, tuple[int, ...]]:
            assert isinstance(messages, list)
            assert "what a code symbol does" in messages[0]["content"]
            facts = json.loads(messages[1]["content"])["facts"]
            assert all("TestTransformPageToOffsetLimit" not in fact["quote"] for fact in facts)
            indexes = tuple(
                fact["index"]
                for fact in facts
                if "offset =" in fact["quote"] or "pageSize =" in fact["quote"]
            )
            assert indexes
            return "它把分页参数转换为数据库查询使用的 offset 和 limit。", indexes

    result = query_module.answer_question(
        tmp_path,
        "TransformPageToOffsetLimit 的作用是什么？",
        provider=StubProvider(),  # type: ignore[arg-type]
        allow_local=True,
    )

    assert result["status"] == "answered"
    assert result["answer"] == "它把分页参数转换为数据库查询使用的 offset 和 limit。"
    assert result["wiki_pages"] == ["wiki/pages/target.md"]


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
    with workspace_module._connect(workspace / ".memoryforge" / "index.sqlite") as connection:
        upsert_rule(
            connection,
            SourceEgressRule(
                source_id=imported["source_id"],
                egress_class=EgressClass.HOST_ALLOWED,
                allowed_hosts=("local-cli",),
                updated_at=datetime.now(UTC),
                actor="test",
            ),
        )
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


def test_ask_llm_does_not_egress_local_evidence_without_host_policy(
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
    result = query_module.answer_question(
        workspace,
        "When do cache entries expire?",
        provider=provider,
        allow_local=True,
    )

    assert result["status"] == "unknown"


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
        min_source_count=2,
    )

    assert result["status"] == "answered"
    assert len(result["citations"]) == 2
    assert (
        len(
            {
                (citation["source_id"], citation["source_version"])
                for citation in result["citations"]
            }
        )
        == 2
    )
    assert "sixty seconds" in result["answer"]
    assert "Friday" in result["answer"]


def test_top_matches_does_not_fill_budget_with_same_term_noise() -> None:
    useful = {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-40",
        "quote": "登录链路使用 Kerberos 连接跳板机。",
    }
    noise = {
        "source_id": "b" * 64,
        "source_version": 1,
        "locator": "chars:0-80",
        "quote": "该代码常量用于跳板机登录配置。",
    }

    selected = query_module._top_matches(
        [((2,), "wiki/pages/runbook.md", useful), ((1,), "wiki/pages/code.md", noise)],
        6,
        question_terms=query_module._expanded_question_terms(
            query_module._terms("跳板机怎么登录？")
        ),
    )

    assert selected == [("wiki/pages/runbook.md", useful)]


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


def test_local_english_matching_handles_inflections_without_changing_terms() -> None:
    citation = {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-20",
        "quote": "Reusing the loader keeps cached directories.",
    }

    assert query_module._local_english_matching_terms(
        {"reuse", "cache", "directory"},
        citation,
        enabled=True,
    ) == {"reuse", "cache", "directory"}
    assert (
        query_module._local_english_matching_terms(
            {"reuse", "cache", "directory"},
            citation,
            enabled=False,
        )
        == set()
    )


def test_ask_admits_a_fact_with_one_local_inflection_match(tmp_path: Path) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    quote = "The loader stores cached directory listings."
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Loader](pages/loader.md) — loader cache configuration\n",
        encoding="utf-8",
    )
    (pages / "loader.md").write_text(_wiki_page(quote), encoding="utf-8")

    result = query_module.answer_question(
        tmp_path,
        "Which GriffeLoader option enables cache?",
    )

    assert result["answer"] == quote


def test_rank_matches_prefers_local_morphology_then_page_rank() -> None:
    summary = {
        "source_id": "a" * 64,
        "source_version": 1,
        "locator": "chars:0-20",
        "quote": "Loader package overview.",
    }
    detail = {
        "source_id": "b" * 64,
        "source_version": 1,
        "locator": "chars:0-20",
        "quote": "Reusing the loader keeps cached directories.",
    }

    ranked = query_module._rank_matches(
        [
            (frozenset({"loader"}), True, "wiki/pages/first.md", summary),
            (
                frozenset({"reuse", "loader", "cache"}),
                False,
                "wiki/pages/first.md",
                detail,
            ),
            (
                frozenset({"reuse", "loader", "cache"}),
                False,
                "wiki/pages/second.md",
                detail,
            ),
        ],
        question_terms={"reuse", "loader", "cache"},
        page_ranks={"wiki/pages/first.md": 0, "wiki/pages/second.md": 1},
        local_morphology_pages={"wiki/pages/first.md", "wiki/pages/second.md"},
    )

    assert [(page, citation["quote"]) for _, page, citation in ranked[:2]] == [
        ("wiki/pages/first.md", detail["quote"]),
        ("wiki/pages/second.md", detail["quote"]),
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
        "Redis Celery 与 FailureEvidenceBundle 不重新运行分别解决什么问题？",
        query_module._terms("Redis Celery 与 FailureEvidenceBundle 不重新运行分别解决什么问题？"),
        max_pages=1,
        trace=trace,
        repository_id=None,
    )

    assert selected == [pages / "engine.md"]


def test_candidate_pages_definition_question_prefers_explanatory_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki" / "pages"
    pages.mkdir(parents=True)
    code_page = pages / "code.md"
    definition_page = pages / "definition.md"
    official_page = pages / "official.md"
    (workspace / "wiki" / "INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [Code: testcases/EFS/fixture.py](pages/code.md) — Test helpers for EFS\n"
        "- [学习手册 / EFS 代码结构](pages/guide.md) — EFS 代码如何组织\n"
        "- [EFS 弹性文件存储](pages/definition.md) — EFS 是全托管共享文件存储\n"
        "- [EFS 官方定义](pages/official.md) — EFS 是共享文件存储\n",
        encoding="utf-8",
    )
    code_page.write_text(
        '---\ntitle: "Code: testcases/EFS/fixture.py"\ngenerated: code_wiki\n---\n\n'
        "# Code: testcases/EFS/fixture.py\n",
        encoding="utf-8",
    )
    (pages / "guide.md").write_text("# 学习手册 / EFS 代码结构\n", encoding="utf-8")
    definition_page.write_text("# EFS 弹性文件存储\n", encoding="utf-8")
    official_page.write_text("# EFS 官方定义\n", encoding="utf-8")
    monkeypatch.setattr(query_module, "_exact_code_pages", lambda *_args, **_kwargs: (code_page,))

    selected = query_module._candidate_pages(
        workspace,
        "EFS是什么？",
        query_module._terms("EFS是什么？"),
        max_pages=1,
        trace=[],
        repository_id=None,
        prefer_index_routes=True,
    )

    assert selected == [official_page]


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


def test_scoped_query_does_not_rebuild_code_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pages = tmp_path / "wiki/pages"
    pages.mkdir(parents=True)
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Config](pages/config.md) — bandwidth configuration\n",
        encoding="utf-8",
    )
    (pages / "config.md").write_text(
        _wiki_page("ProvisionedBandwidth is configured in FileSystemCreator."),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        query_module,
        "repository_page_paths",
        lambda *_args: ("wiki/pages/config.md",),
    )
    monkeypatch.setattr(
        query_module,
        "_retrieval_wiki_facts",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        "memoryforge.code.code_index.build_code_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("queries must use persisted Wiki facts")
        ),
    )

    result = query_module.answer_question(
        tmp_path,
        "Where is ProvisionedBandwidth configured in FileSystemCreator?",
        repository_id="repo-id",
    )

    assert result["status"] in {"answered", "unknown"}


def test_candidate_pages_prioritize_retrieval_v2_pages(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki/pages"
    pages.mkdir(parents=True)
    preferred_page = pages / "preferred.md"
    strict_page = pages / "strict.md"
    preferred_page.write_text("# Preferred\n", encoding="utf-8")
    strict_page.write_text("# Strict\n", encoding="utf-8")
    (workspace / "wiki/INDEX.md").write_text("# Knowledge Index\n", encoding="utf-8")
    index_path = workspace / ".memoryforge/index.sqlite"
    index_path.parent.mkdir()
    index_path.touch()
    monkeypatch.setattr(
        query_module,
        "find_applied_page_paths",
        lambda *_args, **_kwargs: ("wiki/pages/strict.md",),
    )

    selected = query_module._candidate_pages(
        workspace,
        "where is the setting defined",
        {"setting", "defined"},
        max_pages=1,
        trace=[],
        repository_id=None,
        preferred_page_paths=("wiki/pages/preferred.md",),
    )

    assert selected == [preferred_page]


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
        "# Knowledge Index\n\n- [Generic](pages/generic.md) — Uvicorn server configuration\n",
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


def test_business_acronyms_do_not_force_a_code_route() -> None:
    assert not query_module._is_explicit_code_question(
        "EFS 数据流动如何实现冷热数据分层并降低 TCO？"
    )
    assert query_module._is_explicit_code_question("EFS 模块代码在哪个文件？")


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


def test_ask_does_not_define_a_subject_from_an_incidental_mention(
    tmp_path: Path,
) -> None:
    pages = tmp_path / "wiki/pages"
    pages.mkdir(parents=True)
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Storage](pages/storage.md) — Storage glossary with caching\n",
        encoding="utf-8",
    )
    (pages / "storage.md").write_text(
        _wiki_page("Anser 负责对象存储之间的数据搬运和缓存。"),
        encoding="utf-8",
    )

    result = query_module.answer_question(tmp_path, "缓存是什么意思？")

    assert result["evidence_status"] == "no_local_evidence"
    assert result["citations"] == []


def test_ask_does_not_define_an_abbreviation_from_a_namespace_match(
    tmp_path: Path,
) -> None:
    pages = tmp_path / "wiki/pages"
    pages.mkdir(parents=True)
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [AP](pages/ap.md) — AP permission types\n",
        encoding="utf-8",
    )
    source_id = "a" * 64
    (pages / "ap.md").write_text(
        "\n".join(
            [
                "---",
                'title: "Code module: sm/user/ap"',
                "generated: code_module_overview",
                "---",
                "# Code module: sm/user/ap",
                "",
                "## Verified facts",
                "- `sm.user.ap.PermissionRule` (struct): "
                "`PermissionRule struct { ID string }` [^fact-1]",
                "",
                f"[^fact-1]: source `{source_id}` · revision `1` · `chars:0-1`",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = query_module.answer_question(tmp_path, "AP 是什么？")

    assert result["evidence_status"] == "no_local_evidence"
    assert result["citations"] == []


def test_test_lifecycle_filter_keeps_only_run_cleanup_facts() -> None:
    def citation(symbol: str, kind: str = "method") -> query_module.CitationPayload:
        return {
            "source_id": "a" * 64,
            "source_version": 1,
            "locator": "chars:0-1",
            "quote": f"`{symbol}` ({kind}): `def lifecycle():`",
            "grounding": "exact",
        }

    assert query_module._is_test_lifecycle_fact(
        citation("testcases.EFS.efs_mgr.dfp.delete.Delete.run_test")
    )
    assert query_module._is_test_lifecycle_fact(
        citation("testcases.EFS.efs_mgr.dfp.delete.Delete.post_test")
    )
    assert query_module._is_test_lifecycle_fact(
        citation("testcases.EFS.efs_mgr.dfp.delete.Delete._wait_policy_deleted")
    )
    assert not query_module._is_test_lifecycle_fact(
        citation("testcases.EFS.efs_mgr.dfp.delete", kind="module")
    )
    assert not query_module._is_test_lifecycle_fact(
        citation("framework.robot.testset.get_log_path")
    )


def test_ask_does_not_add_citations_to_cover_repository_name_terms(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pages = tmp_path / "wiki/pages"
    pages.mkdir(parents=True)
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [Config](pages/config.md) — bandwidth performance settings\n",
        encoding="utf-8",
    )
    source_id = "a" * 64
    (pages / "config.md").write_text(
        "\n".join(
            [
                "---",
                "type: concept",
                "---",
                "# Config",
                "",
                "## Verified facts",
                "- ProvisionedBandwidth and PerformanceDensity are defined in "
                "FileSystemCreator. [^fact-1]",
                "- GetEvents accepts an mgr request. [^fact-2]",
                "- QueryTradeAccounts returns an efs account. [^fact-3]",
                "",
                f"[^fact-1]: source `{source_id}` · revision `1` · `chars:0-1`",
                f"[^fact-2]: source `{source_id}` · revision `1` · `chars:2-3`",
                f"[^fact-3]: source `{source_id}` · revision `1` · `chars:4-5`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(query_module, "_repository_name_terms", lambda *_args: {"efs", "mgr"})
    monkeypatch.setattr(
        query_module,
        "repository_page_paths",
        lambda *_args: ("wiki/pages/config.md",),
    )

    result = query_module.answer_question(
        tmp_path,
        "efs-mgr 里预置带宽或性能密度相关配置在哪里定义？",
        repository_id="repo-id",
        max_citations=3,
    )

    assert result["status"] == "answered"
    assert len(result["citations"]) == 1
    assert "FileSystemCreator" in result["citations"][0]["quote"]


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


def test_explicit_year_must_appear_in_fact_body(tmp_path: Path) -> None:
    pages = tmp_path / "wiki/pages"
    pages.mkdir(parents=True)
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n- [2027 目标](pages/target.md) — EFS 营收目标\n",
        encoding="utf-8",
    )
    target = pages / "target.md"
    target.write_text(
        _wiki_page("2027 年 EFS 精确营收目标是 100 亿元。"),
        encoding="utf-8",
    )

    supported = query_module.answer_question(tmp_path, "EFS 2027 年营收目标是什么？")

    assert supported["status"] == "answered"
    assert "2027 年" in supported["answer"]

    target.write_text(
        _wiki_page("EFS 是弹性文件存储产品。"),
        encoding="utf-8",
    )
    unsupported = query_module.answer_question(tmp_path, "EFS 2027 年营收目标是什么？")

    assert unsupported["evidence_status"] == "no_local_evidence"
    assert unsupported["citations"] == []


def test_explicit_feishu_question_excludes_conversation_pages(tmp_path: Path) -> None:
    pages = tmp_path / "wiki" / "pages"
    pages.mkdir(parents=True)
    feishu_source = "a" * 64
    conversation_source = "b" * 64
    feishu_quote = "EFS 是按量计费的商业化云产品。"
    conversation_quote = "2027 年 EFS 精确营收目标是 100 亿元。"
    (tmp_path / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [EFS 飞书资料](pages/feishu.md) — EFS 商业模式。\n"
        "- [EFS 历史会话](pages/conversation.md) — 2027 年 EFS 营收目标。\n",
        encoding="utf-8",
    )
    (pages / "feishu.md").write_text(
        "---\n"
        'title: "EFS 飞书资料"\n'
        "type: concept\n"
        'summary: "EFS 商业模式。"\n'
        'tags: ["feishu"]\n'
        f'sources: ["{feishu_source}"]\n'
        "---\n"
        "# EFS 飞书资料\n\n"
        "## Verified facts\n\n"
        f"- {feishu_quote} [^fact-1]\n\n"
        f"[^fact-1]: source `{feishu_source}` · revision `1` · `chars:0-20`\n",
        encoding="utf-8",
    )
    (pages / "conversation.md").write_text(
        "---\n"
        'title: "EFS 历史会话"\n'
        "type: concept\n"
        'summary: "2027 年 EFS 营收目标。"\n'
        'tags: ["conversation"]\n'
        f'sources: ["{conversation_source}"]\n'
        "---\n"
        "# EFS 历史会话\n\n"
        "## Conversation notes (unverified)\n\n"
        "### Assistant conclusions\n\n"
        f"- {conversation_quote} [^fact-1]\n\n"
        f"[^fact-1]: source `{conversation_source}` · revision `1` · `chars:0-28`\n",
        encoding="utf-8",
    )

    result = query_module.answer_question(
        tmp_path,
        "飞书资料是否说明了 2027 年 EFS 精确营收目标？",
    )

    assert result["status"] == "unknown"
    assert result["citations"] == []


def test_explicit_feishu_title_excludes_other_feishu_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, sources = _explicit_title_workspace(tmp_path, monkeypatch)

    result = query_module.answer_question(
        workspace,
        "飞书文档《飞书章节 A》规定缓存条目何时过期？",
    )

    assert result["status"] == "answered"
    assert {citation["source_id"] for citation in result["citations"]} == {
        sources["feishu_a"]
    }
    assert "六十秒" in result["answer"]


def test_explicit_ai_title_excludes_readme_code_and_other_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, sources = _explicit_title_workspace(tmp_path, monkeypatch)

    result = query_module.answer_question(
        workspace,
        "AI 会话《Codex 会话：缓存排障》说明缓存写入失败时如何处理？",
        max_citations=6,
    )

    assert result["status"] == "answered"
    assert {citation["source_id"] for citation in result["citations"]} == {
        sources["conversation_a"]
    }
    assert "事务回滚" in result["answer"]


def test_explicit_title_filters_source_version_inside_merged_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, sources = _explicit_title_workspace(tmp_path, monkeypatch)

    result = query_module.answer_question(
        workspace,
        "AI 会话《Codex 会话：缓存排障》说明缓存写入失败时如何处理？",
        max_citations=6,
    )

    assert result["wiki_pages"] == ["wiki/pages/merged-conversations.md"]
    assert all(
        (citation["source_id"], citation["source_version"])
        == (sources["conversation_a"], 3)
        for citation in result["citations"]
    )


def test_missing_explicit_title_does_not_fall_back_to_adjacent_title(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, _ = _explicit_title_workspace(tmp_path, monkeypatch)

    result = query_module.answer_question(
        workspace,
        "飞书文档《飞书章节 C》规定缓存条目何时过期？",
    )

    assert result["evidence_status"] == "no_local_evidence"
    assert result["citations"] == []


def test_explicit_title_without_facts_makes_cross_source_answer_partial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, sources = _explicit_title_workspace(tmp_path, monkeypatch)

    result = query_module.answer_question(
        workspace,
        "飞书文档《飞书章节 A》与 AI 会话《Codex 会话：空白记录》"
        "如何说明缓存条目在六十秒后过期？",
        max_citations=6,
    )

    assert result["evidence_status"] == "partial"
    assert {citation["source_id"] for citation in result["citations"]} == {
        sources["feishu_a"]
    }
    assert result["support"]["failed_hard_gates"] == ["required_source_group_incomplete"]
    assert result["unsupported_aspects"] == [
        "required_source_group_incomplete:Codex 会话：空白记录"
    ]


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


def test_ask_cli_scopes_an_explicit_repository_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    opened = type("Opened", (), {"root": tmp_path})()
    scope = type("Scope", (), {"repository_id": "repo-id"})()
    captured = {}
    monkeypatch.setattr(cli_module.Workspace, "open_readonly", lambda _workspace: opened)
    monkeypatch.setattr(cli_module, "_named_repository_scope", lambda *_args: scope)

    def fake_answer(root: Path, question: str, **kwargs: object) -> dict[str, object]:
        captured.update(root=root, question=question, **kwargs)
        return {
            "status": "unknown",
            "evidence_status": "no_local_evidence",
            "answer": "不知道",
            "supported_claims": [],
            "unsupported_aspects": ["no_local_evidence"],
            "citations": [],
            "wiki_pages": [],
        }

    monkeypatch.setattr(cli_module, "answer_question", fake_answer)

    result = CliRunner().invoke(
        app,
        ["ask", "efs-mgr 有什么配置？", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert captured["repository_id"] == "repo-id"


def test_ask_cli_rejects_invalid_page_budget(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["ask", "cache", "--max-pages", "0", "--workspace", str(tmp_path)],
    )

    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_query_clients_share_default_budget_and_core_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner, workspace, _ = _workspace_with_imported_source(
        tmp_path,
        monkeypatch,
        "# Cache policy\n\nCache entries expire after sixty seconds.\n",
    )
    _apply_pending_source(runner, workspace)
    question = "When do cache entries expire?"

    direct = query_module.answer_question(workspace, question)
    cli_result = runner.invoke(
        app,
        ["ask", question, "--workspace", str(workspace)],
    )
    assert cli_result.exit_code == 0, cli_result.output
    cli = json.loads(cli_result.stdout)
    portal_app = LocalPortalApp(workspace, allow_local_llm=True)
    portal_status, _, portal_body = portal_app.dispatch_post(
        "/api/ask",
        {"question": question},
    )
    portal_app.close()
    assert portal_status == 200
    portal = json.loads(portal_body)
    mcp = query_workspace_context(workspace, question, allow_local=True)

    def core(payload: dict[str, Any], answer_key: str) -> dict[str, Any]:
        return {
            "evidence_status": payload["evidence_status"],
            "answer": payload[answer_key],
            "wiki_pages": payload["wiki_pages"],
            "citations": [
                (
                    citation["source_id"],
                    citation["source_version"],
                    citation["locator"],
                )
                for citation in payload["citations"]
            ],
            "hard_gates": payload.get("support", {}).get("failed_hard_gates", []),
        }

    expected = core(direct, "answer")
    assert core(cli, "answer") == expected
    assert core(portal, "answer") == expected
    assert core(mcp, "project_answer") == expected
    assert mcp["budget"]["max_pages"] == 3
    assert mcp["budget"]["max_citations"] == 6


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
    if not local_only:
        arguments.append("--public")
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

    applied = review_approve_apply(runner, changeset_id, workspace)
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


def _explicit_title_workspace(
    tmp_path: Path,
    monkeypatch,
) -> tuple[Path, dict[str, str]]:
    monkeypatch.setattr(query_module, "find_applied_page_paths", lambda *_args, **_kwargs: ())
    workspace = tmp_path / "workspace"
    pages = workspace / "wiki/pages"
    pages.mkdir(parents=True)
    database = workspace / ".memoryforge/index.sqlite"
    database.parent.mkdir()
    sources = {
        "feishu_a": "a" * 64,
        "feishu_b": "b" * 64,
        "conversation_a": "c" * 64,
        "conversation_b": "d" * 64,
        "readme": "e" * 64,
        "empty_conversation": "f" * 64,
    }
    records = (
        (1, sources["feishu_a"], "飞书章节 A", '["feishu"]', "wiki/pages/feishu-a.md"),
        (2, sources["feishu_b"], "飞书章节 B", '["feishu"]', "wiki/pages/feishu-b.md"),
        (
            3,
            sources["conversation_a"],
            "Codex 会话：缓存排障",
            '["conversation"]',
            "wiki/pages/merged-conversations.md",
        ),
        (
            4,
            sources["conversation_b"],
            "Codex 会话：其他排障",
            '["conversation"]',
            "wiki/pages/merged-conversations.md",
        ),
        (5, sources["readme"], "README", '["code"]', "wiki/pages/readme.md"),
        (
            6,
            sources["empty_conversation"],
            "Codex 会话：空白记录",
            '["conversation"]',
            None,
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL UNIQUE,
                source_path TEXT NOT NULL
            );
            CREATE TABLE source_versions (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                is_current INTEGER NOT NULL
            );
            CREATE TABLE applied_source_versions (
                source_id TEXT PRIMARY KEY,
                source_version_id INTEGER NOT NULL
            );
            CREATE TABLE wiki_facts (
                id INTEGER PRIMARY KEY,
                page_path TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_version INTEGER NOT NULL
            );
            """
        )
        for version, source_id, title, tags, page_path in records:
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?)",
                (version, source_id, f"source-{version}.md"),
            )
            connection.execute(
                "INSERT INTO source_versions VALUES (?, ?, ?, ?, 1)",
                (version, version, title, tags),
            )
            connection.execute(
                "INSERT INTO applied_source_versions VALUES (?, ?)",
                (source_id, version),
            )
            if page_path is not None:
                connection.execute(
                    "INSERT INTO wiki_facts VALUES (?, ?, ?, ?)",
                    (version, page_path, source_id, version),
                )

    (workspace / "wiki/INDEX.md").write_text(
        "# Knowledge Index\n\n"
        "- [飞书章节 A](pages/feishu-a.md) — 缓存条目过期规则。\n"
        "- [飞书章节 B](pages/feishu-b.md) — 缓存条目过期规则。\n"
        "- [Merged conversation](pages/merged-conversations.md) — 缓存写入失败处理。\n"
        "- [README](pages/readme.md) — 缓存写入失败处理。\n",
        encoding="utf-8",
    )
    (pages / "feishu-a.md").write_text(
        _source_page(
            "飞书章节 A",
            "feishu",
            ((sources["feishu_a"], 1, "缓存条目在六十秒后过期。"),),
        ),
        encoding="utf-8",
    )
    (pages / "feishu-b.md").write_text(
        _source_page(
            "飞书章节 B",
            "feishu",
            ((sources["feishu_b"], 2, "缓存条目永久保留，不会过期。"),),
        ),
        encoding="utf-8",
    )
    (pages / "merged-conversations.md").write_text(
        _source_page(
            "Merged conversation",
            "conversation",
            (
                (sources["conversation_a"], 3, "缓存写入失败时执行事务回滚。"),
                (sources["conversation_b"], 4, "缓存写入失败时保留脏数据。"),
            ),
        ),
        encoding="utf-8",
    )
    (pages / "readme.md").write_text(
        _source_page(
            "Code: README",
            "code",
            ((sources["readme"], 5, "README 建议缓存写入失败时重试。"),),
        ),
        encoding="utf-8",
    )
    return workspace, sources


def _source_page(
    title: str,
    tag: str,
    facts: tuple[tuple[str, int, str], ...],
) -> str:
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        "type: concept",
        f'tags: ["{tag}"]',
        "---",
        f"# {title}",
        "",
    ]
    if tag == "conversation":
        lines.extend(["## Conversation notes (unverified)", "", "### Assistant conclusions", ""])
    else:
        lines.extend(["## Verified facts", ""])
    for index, (_source_id, _version, quote) in enumerate(facts, start=1):
        lines.append(f"- {quote} [^fact-{index}]")
    lines.append("")
    for index, (source_id, version, quote) in enumerate(facts, start=1):
        lines.append(
            f"[^fact-{index}]: source `{source_id}` · revision `{version}` · "
            f"`chars:0-{len(quote)}`"
        )
    lines.append("")
    return "\n".join(lines)
