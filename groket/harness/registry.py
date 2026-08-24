"""Registered disk adapters and session-ref resolution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .ref import SessionRef, parse_session_ref_string
from .types import HarnessAdapter

type PathResolver = Callable[[str], Path | None]

_ADAPTERS: tuple[HarnessAdapter, ...] | None = None


def _grok_adapter() -> HarnessAdapter:
    from .grok import GrokAdapter

    return GrokAdapter()


def adapters() -> tuple[HarnessAdapter, ...]:
    """Installed adapters."""
    global _ADAPTERS
    if _ADAPTERS is None:
        _ADAPTERS = (_grok_adapter(),)
    return _ADAPTERS


def adapter(harness_id: str) -> HarnessAdapter | None:
    """Return the adapter for *harness_id*, or None."""
    hid = (harness_id or "").strip()
    for item in adapters():
        if item.id == hid:
            return item
    return None


def enabled_host_ids() -> frozenset[str]:
    """Adapter ids enabled in ``[harness].host`` (defaults if no config)."""
    from ..config import load_app_config

    return frozenset(load_app_config().harness.host)


def enabled_host_adapters() -> tuple[HarnessAdapter, ...]:
    """Registered adapters allowed on the host catalog."""
    wanted = enabled_host_ids()
    return tuple(item for item in adapters() if item.id in wanted)


def host_adapters() -> tuple[HarnessAdapter, ...]:
    """Adapters that contribute native (non-eval) catalog rows."""
    return enabled_host_adapters()


def resolve_session_ref(
    reference: str,
    *,
    path_resolve: PathResolver | None = None,
) -> SessionRef | None:
    """Map a control ``session`` argument to a :class:`SessionRef`.

    Order: ``harness:id`` → each adapter ``ref_for_id`` → directory path
    (Grok).

    :param reference: Session id, directory, or ``harness:id``.
    :param path_resolve: Optional Grok directory resolver (catalog cache).
    :returns: Locator, or None when nothing matches.
    """
    raw = (reference or "").strip()
    if not raw:
        return None
    parsed = parse_session_ref_string(raw)
    if parsed is not None:
        hid, sid = parsed
        found = adapter(hid)
        if found is None:
            return None
        return found.ref_for_id(sid)
    for item in adapters():
        hit = item.ref_for_id(raw)
        if hit is not None:
            return hit
    if path_resolve is not None:
        path = path_resolve(raw)
        if path is not None and path.is_dir():
            return grok_ref_from_path(path)
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        grok = adapter("grok")
        if grok is not None and grok.looks_like(candidate):
            return grok_ref_from_path(candidate)
    return None


def grok_ref_from_path(path: Path, *, origin: str | None = None) -> SessionRef:
    """Build a Grok :class:`SessionRef` from a session directory."""
    from ..session.sources import ORIGIN_HOST, ORIGIN_WORK, is_under_host_grok_sessions
    from .grok import GROK_HARNESS_ID

    loc = Path(path).expanduser()
    try:
        loc = loc.resolve()
    except OSError:
        pass
    if origin is None:
        origin = ORIGIN_HOST if is_under_host_grok_sessions(loc) else ORIGIN_WORK
    return SessionRef(
        harness=GROK_HARNESS_ID,
        session_id=loc.name,
        origin=origin,
        locator=loc,
    )


__all__ = [
    "OPENCODE_HARNESS_ID",
    "PI_HARNESS_ID",
    "adapter",
    "adapters",
    "enabled_host_adapters",
    "enabled_host_ids",
    "grok_ref_from_path",
    "host_adapters",
    "resolve_session_ref",
]
