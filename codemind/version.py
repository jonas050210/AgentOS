"""Version and identity information for CodeMind."""

from __future__ import annotations

import platform
import sys

APP_NAME = "CodeMind"
APP_TAGLINE = "Autonomous local coding AI"
APP_VERSION = "1.0.0"
APP_ID = "codemind"
SCHEMA_VERSION = 3


def _python() -> str:
    return f"{sys.implementation.name} {platform.python_version()}"


BUILD_INFO: dict[str, str] = {
    "app": APP_NAME,
    "version": APP_VERSION,
    "python": _python(),
    "platform": platform.platform(),
    "machine": platform.machine(),
    "schema": str(SCHEMA_VERSION),
}
