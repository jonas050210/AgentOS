"""Secret detection and redaction.

Every log line, every tool result and every GitHub payload passes through here before it is
persisted or shown. The rules are intentionally conservative: it is better to redact a
false positive than to write a token to disk.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Pattern

# Patterns that are almost always credentials.
_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abpsr]-[A-Za-z0-9\-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9\-._~+/=]{12,})")),
    ("basic_auth_url", re.compile(r"(?i)\b(https?://)([^/\s:@]+):([^/\s@]+)@")),
    ("assignment", re.compile(r"(?i)\b([A-Za-z0-9_]*(?:token|secret|passwd|password|api[_-]?key|apikey|credential)[A-Za-z0-9_]*)\s*[:=]\s*['\"]?([^\s'\"]{6,})")),
    ("json_secret", re.compile(r"(?i)\"([A-Za-z0-9_]*(?:token|secret|password|api[_-]?key)[A-Za-z0-9_]*)\"\s*:\s*\"([^\"]{6,})\"")),
]

# Environment variable names whose *values* must never be echoed.
SENSITIVE_ENV_HINTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "APIKEY",
    "API_KEY",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "SESSION",
    "COOKIE",
)

REDACTED = "‹redacted›"


def is_sensitive_env(name: str) -> bool:
    upper = name.upper()
    return any(hint in upper for hint in SENSITIVE_ENV_HINTS)


def redact_text(text: str, keep_prefix: int = 4) -> str:
    """Return ``text`` with credentials replaced by a marker."""
    if not text:
        return text
    out = text
    for name, pattern in _PATTERNS:
        if name in ("bearer", "basic_auth_url", "assignment", "json_secret"):
            # keep the key/prefix, drop the value
            def _sub(match: re.Match[str]) -> str:
                groups = match.groups()
                head = groups[0] if groups else ""
                if name == "basic_auth_url":
                    return f"{head}{groups[1]}:{REDACTED}@"
                return f"{head}{match.group(0)[len(head):len(head) + 1]}{REDACTED}" if head else REDACTED

            out = pattern.sub(_sub, out)
        else:
            def _plain(match: re.Match[str], _name: str = name) -> str:
                token = match.group(0)
                if _name == "private_key":
                    return f"-----BEGIN {REDACTED} PRIVATE KEY-----"
                prefix = token[:keep_prefix]
                return f"{prefix}{REDACTED}"

            out = pattern.sub(_plain, out)
    return out


def redact_env(env: dict[str, str]) -> dict[str, str]:
    return {k: (REDACTED if is_sensitive_env(k) else v) for k, v in env.items()}


def scrub_mapping(value: object, depth: int = 0) -> object:
    """Recursively redact strings inside dicts/lists (used before persisting payloads)."""
    if depth > 6:
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_env(key):
                out[key] = REDACTED
            else:
                out[key] = scrub_mapping(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub_mapping(item, depth + 1) for item in value]
    return value


class SecretRedactor:
    """Redacts an explicit set of secret values plus the built-in patterns."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._values: list[str] = []
        for secret in secrets:
            self.add(secret)

    def add(self, secret: str | None) -> None:
        if secret and len(secret) >= 6 and secret not in self._values:
            self._values.append(secret)

    def add_from_env(self, names: Iterable[str]) -> None:
        for name in names:
            self.add(os.environ.get(name))

    def redact(self, text: str) -> str:
        out = redact_text(text)
        for secret in self._values:
            out = out.replace(secret, REDACTED)
        return out

    def __call__(self, text: str) -> str:
        return self.redact(text)


GLOBAL_REDACTOR = SecretRedactor()


def register_secret(secret: str | None) -> None:
    GLOBAL_REDACTOR.add(secret)
