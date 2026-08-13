from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    redaction_count: int
    replacements: tuple[tuple[int, int, str], ...]


_PATTERN_PEM = (
    re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |)PRIVATE KEY-----"
        r"[\s\S]*?"
        r"-----END (?:RSA |EC |DSA |OPENSSH |PGP |)PRIVATE KEY-----",
        re.MULTILINE,
    ),
    "pem_key",
)

_PATTERN_BEARER = (
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    "bearer_token",
)

_PATTERN_GITHUB = (
    re.compile(r"(?:ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{82,})"),
    "github_token",
)

_PATTERN_OPENAI = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "openai_key",
)

_PATTERN_ENV_SECRET = (
    re.compile(
        r"(?P<key>(?:[A-Za-z_][A-Za-z0-9_]*))"
        r"=(?P<value>(?:\"[^\"]*\"|'[^']*'|[^\s\"';&|`$()<>]+))",
    ),
    "env_secret",
)

_ENV_SECRET_KEYWORDS = frozenset({"token", "secret", "password", "api_key"})

_PATTERN_USER_PRIVATE = (
    re.compile(r"<private>[\s\S]*?</private>", re.IGNORECASE | re.DOTALL),
    "user_private",
)

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    _PATTERN_PEM,
    _PATTERN_BEARER,
    _PATTERN_GITHUB,
    _PATTERN_OPENAI,
    _PATTERN_ENV_SECRET,
    _PATTERN_USER_PRIVATE,
]


def _is_env_secret_key(key: str) -> bool:
    lower = key.lower()
    return any(keyword in lower for keyword in _ENV_SECRET_KEYWORDS)


def redact_for_model(text: str) -> RedactionResult:
    matches: list[tuple[int, int, str]] = []

    for pattern, tag in PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if pattern is _PATTERN_ENV_SECRET[0]:
                key = match.group("key")
                if not _is_env_secret_key(key):
                    continue
            matches.append((start, end, tag))

    matches.sort(key=lambda m: m[0])

    filtered: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, tag in matches:
        if start >= last_end:
            filtered.append((start, end, tag))
            last_end = end

    result = text
    for start, end, tag in reversed(filtered):
        replacement_text = f"<redacted:{tag}>"
        result = result[:start] + replacement_text + result[end:]

    count = len(filtered)
    return RedactionResult(
        redacted_text=result,
        redaction_count=count,
        replacements=tuple(filtered),
    )
