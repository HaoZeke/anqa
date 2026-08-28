"""Per-harness disk adapters.

Each store implements the same contract. Import a shipped adapter from
its module (``anqa.harness.grok``). Catalog code uses the factory here.
"""

from __future__ import annotations

from .ref import HARNESS_IDS, SessionRef, parse_session_ref_string
from .registry import (
    adapter,
    adapter_for,
    adapters,
    discover_dirs,
    require_adapter,
    resolve_session_ref,
)

__all__ = [
    "HARNESS_IDS",
    "SessionRef",
    "adapter",
    "adapter_for",
    "adapters",
    "discover_dirs",
    "parse_session_ref_string",
    "require_adapter",
    "resolve_session_ref",
]
