"""Harness adapter protocol."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ..models import SessionMeta, TraceEvent
from .ref import SessionRef


class HarnessAdapter(Protocol):
    """One on-disk coding-agent store.

    Missing product data stays unset. Adapters never invent context-meter
    percents, rewind snapshots, or a turn gate.
    """

    id: str
    product: str
    supported_version: str

    def default_host_roots(self) -> list[Path]:
        """Native stores to scan when the host catalog is included."""

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        """List operator-facing sessions under *roots* or the default host roots."""

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        """True when *ref* belongs to this harness."""

    def bind_locator(self, locator: Path) -> SessionRef | None:
        """Session ref when *locator* is one session this adapter can reopen."""

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        """List-grade metadata. Sets ``harness``."""

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        """Linear timeline using anqa event type names."""

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        """Reopen *session_id* from the default store, or None."""

    def watch_hints(self) -> tuple[str, ...]:
        """Basenames whose mtime should refresh the catalog / browser."""
