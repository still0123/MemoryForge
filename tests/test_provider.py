from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from pydantic import ValidationError

from memoryforge.models import CompilationPlan, PageChange, TopicGroup
from memoryforge.provider import (
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderUnavailableError,
)

SOURCE_ID = "a" * 64


def _page_change() -> dict[str, object]:
    return {
        "path": "wiki/pages/cache-design.md",
        "title": "Cache design",
        "page_type": "concept",
        "summary": "The service uses a local cache.",
        "body": "# Cache design\n\nThe service uses a local cache.",
        "source_ids": [SOURCE_ID],
        "citations": [{"source_id": SOURCE_ID, "locator": "chars:0-33"}],
    }


@pytest.mark.parametrize(
    "missing",
    ["MEMORYFORGE_API_BASE", "MEMORYFORGE_API_KEY", "MEMORYFORGE_MODEL"],
)
def test_provider_config_requires_each_environment_value(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("MEMORYFORGE_API_BASE", "MEMORYFORGE_API_KEY", "MEMORYFORGE_MODEL"):
        monkeypatch.setenv(name, "configured")
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match=missing):
        ProviderConfig.from_environment()


def test_provider_config_constructor_does_not_read_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEMORYFORGE_API_BASE", raising=False)
    config = ProviderConfig("https://example.test/v1", "test-key", "test-model")

    assert config.model == "test-model"


def test_provider_config_reads_project_dotenv_without_overriding_environment(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MEMORYFORGE_API_BASE=https://dotenv.test",
                "MEMORYFORGE_API_KEY=dotenv-key",
                "MEMORYFORGE_MODEL=dotenv-model",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in ("MEMORYFORGE_API_BASE", "MEMORYFORGE_API_KEY", "MEMORYFORGE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MEMORYFORGE_MODEL", "environment-model")

    config = ProviderConfig.from_environment()

    assert config == ProviderConfig("https://dotenv.test", "dotenv-key", "environment-model")


def test_provider_posts_expected_chat_completions_request() -> None:
    captured: list[Request] = []

    def transport(request: Request) -> bytes:
        captured.append(request)
        return _chat_response({"changes": []})

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test/v1/", "secret", "test-model"),
        transport=transport,
    )

    assert provider.compile_pages([{"role": "user", "content": "compile this"}]) == ()

    request = captured[0]
    assert request.full_url == "https://example.test/v1/chat/completions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer secret"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data or b"") == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "compile this"}],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "max_tokens": 4096,
    }


def test_provider_parses_valid_page_changes() -> None:
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: _chat_response({"changes": [_page_change()]}),
    )

    changes = provider.compile_pages([{"role": "user", "content": "compile this"}])

    assert changes == (PageChange.model_validate(_page_change()),)


def test_provider_parses_compilation_plan() -> None:
    plan = {
        "pages": [
            {
                "path": "wiki/pages/cache-design.md",
                "action": "create",
                "source_ids": [SOURCE_ID],
                "reason": "Create the cache concept page.",
                "related_pages": [],
            }
        ],
        "conflicts": [],
    }
    captured: list[Request] = []

    def transport(request: Request) -> bytes:
        captured.append(request)
        return _chat_response({"plan": plan})

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=transport,
    )

    result = provider.plan_pages([{"role": "user", "content": "plan this"}])

    assert result == CompilationPlan.model_validate(plan)
    payload = json.loads(captured[0].data or b"")
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 2048


def test_provider_accepts_a_bare_compilation_plan() -> None:
    plan = {
        "pages": [
            {
                "path": "wiki/pages/cache-design.md",
                "action": "create",
                "source_ids": [SOURCE_ID],
                "reason": "Create the cache concept page.",
                "related_pages": [],
            }
        ],
        "conflicts": [],
    }
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: _chat_response(plan),
    )

    assert provider.plan_pages([]) == CompilationPlan.model_validate(plan)


def test_provider_parses_topic_groups() -> None:
    topic = {
        "title": "Cache behavior",
        "summary": "Pages about cache design and invalidation.",
        "source_ids": [SOURCE_ID],
    }
    captured: list[Request] = []

    def transport(request: Request) -> bytes:
        captured.append(request)
        return _chat_response({"topics": [topic]})

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=transport,
    )

    topics = provider.organize_topics([{"role": "user", "content": "organize this"}])

    assert topics == (TopicGroup.model_validate(topic),)
    assert json.loads(captured[0].data or b"")["thinking"] == {"type": "disabled"}
    assert json.loads(captured[0].data or b"")["max_tokens"] == 4096


def test_provider_parses_evidence_answer() -> None:
    captured: list[Request] = []

    def transport(request: Request) -> bytes:
        captured.append(request)
        return _chat_response(
            {"answer": "Cache entries expire after sixty seconds.", "citation_indexes": [0]}
        )

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=transport,
    )

    assert provider.answer_with_evidence([{"role": "user", "content": "answer this"}]) == (
        "Cache entries expire after sixty seconds.",
        (0,),
    )
    payload = json.loads(captured[0].data or b"")
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 1024


def test_provider_parses_optional_wiki_update() -> None:
    captured: list[Request] = []

    def transport(request: Request) -> bytes:
        captured.append(request)
        return _chat_response({"change": _page_change()})

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=transport,
    )

    result = provider.propose_update([{"role": "user", "content": "propose this"}])

    assert result == PageChange.model_validate(_page_change())
    payload = json.loads(captured[0].data or b"")
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 2048


def test_provider_keeps_unknown_agent_action_for_loop_observation() -> None:
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: _chat_response(
            {"action": "summarize", "call_id": "model-call-7"}
        ),
    )

    result = provider.agent_step([{"role": "user", "content": "choose a tool"}])

    assert result.action == "summarize"
    assert result.call_id == "model-call-7"


def test_provider_rejects_invalid_model_json() -> None:
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: _chat_response_content("not json"),
    )

    with pytest.raises(ValueError, match="content is not valid JSON"):
        provider.compile_pages([])


def test_provider_rejects_missing_choices() -> None:
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: b'{"choices": []}',
    )

    with pytest.raises(ValueError, match="no choices"):
        provider.compile_pages([])


def test_provider_reports_http_errors() -> None:
    def transport(request: Request) -> bytes:
        raise HTTPError(request.full_url, 401, "Unauthorized", None, None)

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=transport,
    )

    with pytest.raises(ValueError, match="HTTP 401"):
        provider.compile_pages([])


def test_provider_classifies_transient_http_errors() -> None:
    def transport(request: Request) -> bytes:
        raise HTTPError(request.full_url, 503, "Service Unavailable", None, None)

    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=transport,
    )

    with pytest.raises(ProviderUnavailableError, match="temporarily unavailable"):
        provider.compile_pages([])


def test_provider_reports_timeouts() -> None:
    provider = OpenAICompatibleProvider(
        ProviderConfig("https://example.test", "test-key", "test-model"),
        transport=lambda _request: (_ for _ in ()).throw(TimeoutError()),
    )

    with pytest.raises(ValueError, match="timed out"):
        provider.organize_topics([])


def test_page_change_rejects_invalid_path_duplicate_sources_and_unknown_citation() -> None:
    invalid = _page_change()
    invalid["path"] = "wiki/concepts/cache.md"
    with pytest.raises(ValidationError, match="wiki/pages"):
        PageChange.model_validate(invalid)

    duplicate = _page_change()
    duplicate["source_ids"] = [SOURCE_ID, SOURCE_ID]
    with pytest.raises(ValidationError, match="duplicates"):
        PageChange.model_validate(duplicate)

    unknown_citation = _page_change()
    unknown_citation["citations"] = [{"source_id": "b" * 64, "locator": "chars:0-33"}]
    with pytest.raises(ValidationError, match="declared source_ids"):
        PageChange.model_validate(unknown_citation)

    missing_citation = _page_change()
    missing_citation["source_ids"] = [SOURCE_ID, "b" * 64]
    with pytest.raises(ValidationError, match="cover exactly"):
        PageChange.model_validate(missing_citation)


def _chat_response(payload: dict[str, object]) -> bytes:
    return _chat_response_content(json.dumps(payload))


def _chat_response_content(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
