"""Harness adapter protocol."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ..models import JsonObject, SessionMeta, TraceEvent
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

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        """Write the native session archive to *dest*. Return member names."""

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        """Materialize *src* under *dest_root* and return a bound session.

        *src* is this adapter's native archive (whatever
        :meth:`write_archive` wrote). The locator may be a directory, a
        file, or a database row. Domain does not assume a directory tree.
        """

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        """Full session metadata (browser, export, document)."""

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        """Cheap live-refresh stamp for the timeline files."""

    def trace_mtime(self, ref: SessionRef | Path | str) -> float:
        """Newest trace-file mtime for catalog / poll invalidation."""

    def updates_size(self, ref: SessionRef | Path | str) -> int:
        """Byte size of the live updates log, or 0."""

    def scheduler_state(self, state: JsonObject) -> JsonObject | None:
        """Scheduler block from ``resources_state.json``, if this store has one."""

    def reported_completion_ids(self, state: JsonObject) -> set[str]:
        """Task ids this store lists as already reported complete."""

    def list_turn_outcome(self, ref: SessionRef | Path | str) -> str:
        """Cheap list-row turn outcome from disk, or empty."""

    def delete_session(self, ref: SessionRef | Path | str) -> None:
        """Remove this session from the native store (directory, file, or row)."""
