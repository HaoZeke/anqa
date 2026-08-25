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
    """Registered adapter ids minus ``[catalog].ignore``."""
    from ..config import load_app_config

    ignored = {item.casefold() for item in load_app_config().catalog.ignore}
    return frozenset(item.id for item in adapters() if item.id not in ignored)


def enabled_host_adapters() -> tuple[HarnessAdapter, ...]:
    """Registered adapters included on the host catalog."""
    wanted = enabled_host_ids()
    return tuple(item for item in adapters() if item.id in wanted)


def adapter_host_roots(item: HarnessAdapter) -> list[Path]:
    """Discover roots for *item*: ``[catalog.roots]`` override, else defaults."""
    from ..config import load_app_config

    override = load_app_config().catalog.roots.get(item.id)
    if override:
        return [Path(raw).expanduser() for raw in override]
    return item.default_host_roots()


def adapter_store_watch_paths() -> list[Path]:
    """Enabled adapter stores that are not the Grok directory walk.

    Files (sqlite) and extra dirs are membership-only watch targets.
    They must never be passed to :func:`~anqa.parser.find_sessions`.
    """
    grok = adapter("grok")
    walked: set[str] = set()
    if grok is not None:
        for raw in adapter_host_roots(grok):
            path = Path(raw).expanduser()
            try:
                walked.add(str(path.resolve()))
            except OSError:
                walked.add(str(path))
    extra: list[Path] = []
    for item in enabled_host_adapters():
        for raw in adapter_host_roots(item):
            path = Path(raw).expanduser()
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in walked:
                continue
            extra.append(path)
    return extra


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
    via ``bind_locator``.

    :param reference: Session id, directory, or ``harness:id``.
    :param path_resolve: Optional directory resolver (catalog cache).
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
            bound = ref_from_path(path)
            if bound is not None:
                return bound
            from .grok import _ref_for_dir

            return _ref_for_dir(path)
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return ref_from_path(candidate)
    return None


def ref_from_path(path: Path) -> SessionRef | None:
    """Ask each adapter to bind *path* as one session."""
    loc = Path(path).expanduser()
    for item in adapters():
        hit = item.bind_locator(loc)
        if hit is not None:
            return hit
    return None


__all__ = [
    "adapter",
    "adapter_host_roots",
    "adapter_store_watch_paths",
    "adapters",
    "enabled_host_adapters",
    "enabled_host_ids",
    "host_adapters",
    "ref_from_path",
    "resolve_session_ref",
]
