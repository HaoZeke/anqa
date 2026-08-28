"""Grok Build store path. Leaf module — no anqa imports."""

from __future__ import annotations

from pathlib import Path


def default_sessions_root() -> Path:
    """Native Grok Build store: ``~/.grok/sessions``."""
    return Path.home() / ".grok" / "sessions"
