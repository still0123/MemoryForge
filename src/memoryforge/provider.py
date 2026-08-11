"""Minimal OpenAI-compatible PageChange compiler provider."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from memoryforge.code_models import ModuleNarrative
from memoryforge.models import CompilationPlan, PageChange, TopicGroup

Transport = Callable[[Request], bytes]
_PROVIDER_ENV_NAMES = (
    "MEMORYFORGE_API_BASE",
    "MEMORYFORGE_API_KEY",
    "MEMORYFORGE_MODEL",
)
_REQUEST_TIMEOUT_SECONDS = 30
_TRANSIENT_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


class ProviderUnavailableError(ValueError):
    """The configured model service did not respond normally."""


@dataclass(frozen=True)
class ProviderConfig:
    api_base: str
    api_key: str
    model: str

    @classmethod
    def from_environment(cls) -> ProviderConfig:
        dotenv = _dotenv_values()
        values = {
            name: os.environ.get(name, "").strip() or dotenv.get(name, "")
            for name in _PROVIDER_ENV_NAMES
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"missing provider environment variable: {', '.join(missing)}")
        return cls(
            api_base=values["MEMORYFORGE_API_BASE"],
            api_key=values["MEMORYFORGE_API_KEY"],
            model=values["MEMORYFORGE_MODEL"],
        )


def _dotenv_values() -> dict[str, str]:
    path = Path.cwd() / ".env"
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and name in _PROVIDER_ENV_NAMES:
            values[name] = value.strip().strip("'\"")
    return values


class _ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    changes: tuple[PageChange, ...]


class _PlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: CompilationPlan


class _TopicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topics: tuple[TopicGroup, ...]


class _AnswerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str
    citation_indexes: tuple[int, ...]


class _UpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    change: PageChange | None


class AgentStep(BaseModel):
    """One JSON decision in the small Wiki-backed Agent loop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1)
    call_id: str | None = None
    query: str | None = None
    citation_index: int | None = None
    answer: str | None = None
    citation_indexes: tuple[int, ...] = ()


def _urlopen_transport(request: Request) -> bytes:
    with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read()
    if not isinstance(body, bytes):
        raise ValueError("provider transport returned non-bytes response")
    return body


class OpenAICompatibleProvider:
    """Calls the OpenAI Chat Completions JSON endpoint without an SDK."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: Transport = _urlopen_transport,
    ) -> None:
        self.config = config
        self._transport = transport

    def compile_pages(self, messages: Sequence[Mapping[str, str]]) -> tuple[PageChange, ...]:
        decoded = self._request_json(messages, disable_thinking=True, max_tokens=4096)
        try:
            return _ProviderResponse.model_validate(decoded).changes
        except ValidationError as exc:
            raise ValueError("provider response does not match PageChange contract") from exc

    def plan_pages(self, messages: Sequence[Mapping[str, str]]) -> CompilationPlan:
        decoded = self._request_json(messages, disable_thinking=True, max_tokens=2048)
        try:
            return _PlanResponse.model_validate(decoded).plan
        except ValidationError:
            try:
                return CompilationPlan.model_validate(decoded)
            except ValidationError as exc:
                raise ValueError(
                    "provider response does not match CompilationPlan contract"
                ) from exc

    def organize_topics(self, messages: Sequence[Mapping[str, str]]) -> tuple[TopicGroup, ...]:
        """Return small navigation groups for public, already-compiled source pages."""
        decoded = self._request_json(messages, disable_thinking=True, max_tokens=4096)
        try:
            return _TopicResponse.model_validate(decoded).topics
        except ValidationError as exc:
            raise ValueError("provider response does not match TopicGroup contract") from exc

    def summarize_code_module(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> ModuleNarrative:
        """Describe one deterministic module without changing its code facts."""
        decoded = self._request_json(messages, disable_thinking=True, max_tokens=2048)
        try:
            return ModuleNarrative.model_validate(decoded)
        except ValidationError as exc:
            raise ValueError("provider response does not match ModuleNarrative contract") from exc

    def answer_with_evidence(
        self,
        messages: Sequence[Mapping[str, str]],
    ) -> tuple[str, tuple[int, ...]]:
        """Summarize selected public Wiki facts without choosing new evidence."""
        decoded = self._request_json(messages, disable_thinking=True, max_tokens=1024)
        try:
            response = _AnswerResponse.model_validate(decoded)
        except ValidationError as exc:
            raise ValueError("provider response does not match evidence answer contract") from exc
        return response.answer, response.citation_indexes

    def agent_step(self, messages: Sequence[Mapping[str, str]]) -> AgentStep:
        """Choose one small Wiki tool action or finish the answer."""
        decoded = self._request_json(messages, disable_thinking=True, max_tokens=1024)
        try:
            return AgentStep.model_validate(decoded)
        except ValidationError as exc:
            raise ValueError("provider response does not match Agent step contract") from exc

    def propose_update(self, messages: Sequence[Mapping[str, str]]) -> PageChange | None:
        """Propose one reviewable page update from already-read evidence."""
        decoded = self._request_json(messages, disable_thinking=True, max_tokens=2048)
        try:
            return _UpdateResponse.model_validate(decoded).change
        except ValidationError as exc:
            raise ValueError("provider response does not match Wiki update contract") from exc

    def _request_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        disable_thinking: bool = False,
        max_tokens: int | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [dict(message) for message in messages],
            "response_format": {"type": "json_object"},
        }
        if disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        request = Request(
            f"{self.config.api_base.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._transport(request)
        except TimeoutError as exc:
            raise ProviderUnavailableError(
                f"provider request timed out after {_REQUEST_TIMEOUT_SECONDS} seconds"
            ) from exc
        except HTTPError as exc:
            if exc.code in _TRANSIENT_HTTP_CODES:
                raise ProviderUnavailableError(
                    f"provider temporarily unavailable (HTTP {exc.code})"
                ) from exc
            raise ValueError(f"provider request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise ProviderUnavailableError(f"provider connection failed: {exc.reason}") from exc

        content = _response_content(response)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("provider response content is not valid JSON") from exc


def _response_content(response: bytes) -> str:
    try:
        decoded: Any = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("provider response is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("provider response must be a JSON object")
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("provider response has no choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("provider response has an invalid first choice")
    message = first_choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("provider response choice has no message content")
    content = message["content"]
    if not isinstance(content, str):
        raise ValueError("provider response choice has no message content")
    return content
