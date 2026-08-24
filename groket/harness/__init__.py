"""Per-harness disk adapters.

``grok`` (session directories) ships today. Other stores register the same
contract in later commits.
"""

from __future__ import annotations

from .grok import (
    GROK_HARNESS_ID,
    GrokAdapter,
    discover,
    load_meta,
    looks_like,
    parse_timeline,
    watch_hints,
)
from .ref import HARNESS_IDS, SessionRef, parse_session_ref_string
from .registry import adapter, adapters, resolve_session_ref

__all__ = [
    "GROK_HARNESS_ID",
    "HARNESS_IDS",
    "GrokAdapter",
    "SessionRef",
    "adapter",
    "adapters",
    "discover",
    "load_meta",
    "looks_like",
    "parse_session_ref_string",
    "parse_timeline",
    "resolve_session_ref",
    "watch_hints",
]
