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
    """Enabled adapter stores that need a membership watch.

    Directory stores are already walked for catalog discover. File stores
    (sqlite, a transcript) are listed here so serve can watch them.
    """
    extra: list[Path] = []
    seen: set[str] = set()
    for item in enabled_host_adapters():
        for raw in adapter_host_roots(item):
            path = Path(raw).expanduser()
            if path.is_dir():
                continue
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            extra.append(path)
    return extra


def host_adapters() -> tuple[HarnessAdapter, ...]:
    """Adapters that contribute native host-store catalog rows."""
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
            return ref_from_path(path)
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


def adapter_for(ref: SessionRef | Path | str) -> HarnessAdapter | None:
    """Return the adapter that owns *ref*, or None.

    *ref* is a :class:`SessionRef`, a session directory, or ``harness:id``.
    """
    if isinstance(ref, SessionRef):
        return adapter(ref.harness)
    if isinstance(ref, str):
        parsed = parse_session_ref_string(ref)
        if parsed is not None:
            return adapter(parsed[0])
        path = Path(ref)
    else:
        path = ref
    bound = ref_from_path(path)
    if bound is None:
        return None
    return adapter(bound.harness)


def require_adapter(ref: SessionRef | Path | str) -> HarnessAdapter:
    """Return the adapter that owns *ref*.

    :raises FileNotFoundError: No registered adapter claims *ref*.
    """
    item = adapter_for(ref)
    if item is None:
        raise FileNotFoundError(f"no adapter for session: {ref}")
    return item


__all__ = [
    "adapter",
    "adapter_for",
    "adapter_host_roots",
    "adapter_store_watch_paths",
    "adapters",
    "enabled_host_adapters",
    "enabled_host_ids",
    "host_adapters",
    "ref_from_path",
    "require_adapter",
    "resolve_session_ref",
]
