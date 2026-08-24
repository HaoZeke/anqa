"""Grok Build on-disk session adapter.

Public harness contract for harness id ``grok``. Implementation lives in
:mod:`groket.parser` and :mod:`groket.session.sources`; this module wraps
those APIs.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from ..fs_watch import TRACE_FILE_HINTS
from ..models import SessionMeta, TraceEvent
from ..parser import _looks_like_session_dir, find_sessions, load_session_meta_list
from ..parser import parse_timeline as parse_grok_timeline
from ..session.sources import (
    ORIGIN_HOST,
    ORIGIN_WORK,
    collect_host_session_dirs,
    host_grok_sessions_root,
    is_host_grok_sessions_root,
    is_under_host_grok_sessions,
)
from .ref import SessionRef

GROK_HARNESS_ID = "grok"


def _ref_for_dir(path: Path) -> SessionRef:
    origin = ORIGIN_HOST if is_under_host_grok_sessions(path) else ORIGIN_WORK
    loc = Path(path)
    try:
        loc = loc.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=GROK_HARNESS_ID,
        session_id=loc.name,
        origin=origin,
        locator=loc,
    )


def discover(roots: Sequence[Path | str]) -> list[SessionRef]:
    """List unique Grok sessions under *roots*.

    Host ``~/.grok/sessions`` uses
    :func:`~groket.session.sources.collect_host_session_dirs`. Every other
    root uses :func:`groket.parser.find_sessions`. Duplicate resolved paths
    are dropped (first-seen wins).

    :param roots: Trees to scan.
    :returns: Session refs in first-seen order.
    """
    found: list[SessionRef] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if is_host_grok_sessions_root(root):
            dirs = collect_host_session_dirs(root)
        else:
            dirs = find_sessions(root)
        for sd in dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append(_ref_for_dir(sd))
    return found


def looks_like(ref: Path | str) -> bool:
    """True when *ref* is a Grok session directory.

    Wraps :func:`groket.parser._looks_like_session_dir`.

    :param ref: Session directory path.
    :returns: True when the directory has Grok session artifacts.
    """
    path = Path(ref).expanduser()
    if not path.is_dir():
        return False
    names: set[str] = set()
    try:
        with os.scandir(path) as it:
            for ent in it:
                if ent.is_file(follow_symlinks=False):
                    names.add(ent.name)
    except OSError:
        return False
    return _looks_like_session_dir(path, names)


def load_meta(ref: Path | str) -> SessionMeta:
    """Load list-grade metadata for a Grok session directory.

    Wraps :func:`groket.parser.load_session_meta_list`. Sets ``origin`` to
    ``host`` when *ref* lives under the host Grok sessions tree, else
    ``work``.

    :param ref: Session directory path.
    :returns: Populated :class:`~groket.models.SessionMeta`.
    """
    path = Path(ref).expanduser()
    origin = ORIGIN_HOST if is_under_host_grok_sessions(path) else ORIGIN_WORK
    meta = load_session_meta_list(path, origin=origin)
    meta.harness = GROK_HARNESS_ID
    return meta


def parse_timeline(ref: Path | str) -> list[TraceEvent]:
    """Parse a Grok session directory into a linear timeline.

    Wraps :func:`groket.parser.parse_timeline`.

    :param ref: Session directory path.
    :returns: Coalesced :class:`~groket.models.TraceEvent` rows.
    """
    return parse_grok_timeline(Path(ref).expanduser())


def watch_hints() -> tuple[str, ...]:
    """Filenames that should trigger a live reload for Grok sessions.

    Same names as :data:`groket.fs_watch.TRACE_FILE_HINTS`.

    :returns: Basename hints (``updates.jsonl``, ``events.jsonl``, …).
    """
    return TRACE_FILE_HINTS


class GrokAdapter:
    """Directory-shaped Grok Build store."""

    id: str = GROK_HARNESS_ID
    product: str = "Grok Build"
    supported_version: str = "1.0.5"

    def default_host_roots(self) -> list[Path]:
        return [host_grok_sessions_root()]

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        return discover(self.default_host_roots() if roots is None else roots)

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == GROK_HARNESS_ID and looks_like(ref.locator)
        return looks_like(ref)

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not looks_like(path):
            return None
        return _ref_for_dir(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        if isinstance(ref, SessionRef):
            return load_meta(ref.locator)
        return load_meta(ref)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        if isinstance(ref, SessionRef):
            return parse_timeline(ref.locator)
        return parse_timeline(ref)

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        for ref in self.discover():
            if ref.session_id == sid:
                return ref
        return None

    def watch_hints(self) -> tuple[str, ...]:
        return watch_hints()


__all__ = [
    "GROK_HARNESS_ID",
    "GrokAdapter",
    "discover",
    "load_meta",
    "looks_like",
    "parse_timeline",
    "watch_hints",
]
