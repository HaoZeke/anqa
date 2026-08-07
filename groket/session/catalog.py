"""Domain session catalog for control plane and headless owners.

Builds wire-shaped catalog rows and resolves session references from disk
without Textual app state. Shared by the control daemon and any client that
needs the same discovery rules as the TUI home list.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from ..models import JsonObject, SessionMeta
from ..parser import load_session_meta_list, session_trace_mtime
from ..paths import app_config_path
from .sources import SessionScanRoot, collect_session_dirs, session_scan_roots

logger = logging.getLogger(__name__)


def _parse_iso_epoch(raw: object) -> float:
    """Parse ISO-ish timestamps to epoch seconds; 0 when missing/invalid."""
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if not s:
        return 0.0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return float(dt.timestamp())
    except (TypeError, ValueError, OSError):
        return 0.0


def catalog_row_sort_epoch(row: JsonObject, *, session_dir: Path | None = None) -> float:
    """Best-effort “latest activity” epoch for newest-first catalog order."""
    for key in ("sortEpoch", "updatedAt", "createdAt", "updated_at", "created_at"):
        if key == "sortEpoch":
            raw = row.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return float(raw)
            continue
        ts = _parse_iso_epoch(row.get(key))
        if ts > 0:
            return ts
    path = session_dir
    if path is None:
        path_raw = str(row.get("path") or "").strip()
        if path_raw:
            path = Path(path_raw)
    if path is not None:
        try:
            mt = session_trace_mtime(path)
            if mt > 0:
                return float(mt)
        except OSError:
            pass
        try:
            return float(path.stat().st_mtime)
        except OSError:
            pass
    return 0.0


def show_host_sessions_from_config() -> bool:
    """Whether operator config includes host Grok sessions in the catalog.

    Reads ``show_host_sessions`` from ``~/.groket/config.json`` (same key as
    the TUI ``H`` toggle). Used by the headless control owner so editor
    ``session/list`` matches the TUI home list without importing the UI package.
    """
    path = app_config_path()
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        logger.debug("catalog: could not read %s for show_host_sessions", path, exc_info=True)
        return False
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("show_host_sessions", False))


def effective_include_host(include_host: bool | None) -> bool:
    """Resolve catalog host inclusion: explicit flag, else config pref."""
    if include_host is not None:
        return bool(include_host)
    return show_host_sessions_from_config()


def catalog_scan_roots(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> list[SessionScanRoot]:
    """Scan roots for the control/domain session catalog.

    :param work_dir: Work root (``runs/traces`` lives under this).
    :param traces_path: Optional extra traces path (CLI ``-P`` override).
    :param include_host: When true, include host Grok sessions; when false,
        work only; when None, follow ``show_host_sessions`` in config.
    :param host_root: Override for the host sessions root (tests).
    :returns: Ordered scan roots (work first).
    """
    return session_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=effective_include_host(include_host),
        host_root=host_root,
    )


def session_catalog_row(
    session_dir: Path,
    *,
    origin: str = "work",
    label: str | None = None,
) -> JsonObject | None:
    """Build one ``session/list`` wire row for *session_dir*, or None on failure.

    :param session_dir: Session directory on disk.
    :param origin: Catalog origin (``work`` / ``host``).
    :param label: Optional display label; defaults to meta label.
    :returns: Wire row mapping, or None when meta cannot be loaded.
    """
    try:
        meta = load_session_meta_list(session_dir, origin=origin)
    except Exception:
        logger.debug("catalog meta failed for %s", session_dir, exc_info=True)
        return None
    meta.origin = origin
    session_id = (meta.session_id or session_dir.name).strip()
    try:
        path_str = str(session_dir.resolve())
    except OSError:
        path_str = str(session_dir)
    created = str(meta.created_at or "").strip()
    updated = str(meta.updated_at or "").strip()
    sort_epoch = _parse_iso_epoch(updated) or _parse_iso_epoch(created)
    if sort_epoch <= 0:
        try:
            sort_epoch = float(session_trace_mtime(session_dir))
        except OSError:
            sort_epoch = 0.0
    if sort_epoch <= 0:
        try:
            sort_epoch = float(session_dir.stat().st_mtime)
        except OSError:
            sort_epoch = 0.0
    return {
        "sessionId": session_id,
        "path": path_str,
        "title": meta.title or "",
        "label": label if label is not None else meta.label,
        "model": meta.model_display,
        "status": meta.list_status_label(),
        "outcome": meta.turn_outcome or "",
        "origin": meta.origin or origin,
        # Home-list columns for attach-mode TUI (and any rich client).
        "taskId": meta.task_id or "",
        "durationSeconds": float(meta.duration_seconds or 0),
        "numEvents": int(meta.num_events or 0),
        "contextUsageCompact": meta.context_usage_compact or "",
        # Structured context so attach hydrate rebuilds context_usage_compact.
        "contextWindowUsagePct": meta.context_window_usage_pct,
        "contextTokensUsed": meta.context_tokens_used,
        "contextWindowTokens": meta.context_window_tokens,
        "toolCallCount": int(meta.tool_call_count or 0),
        "errorCount": int(meta.error_count or 0),
        # Newest-first list ordering for all control clients.
        "createdAt": created,
        "updatedAt": updated,
        "sortEpoch": sort_epoch,
    }


def list_session_catalog(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> list[JsonObject]:
    """Scan catalog roots and return wire-shaped rows for ``session/list``.

    Row meta loads are independent and I/O-bound; build them in a small
    thread pool so hundreds of sessions stay under the HUD control timeout.

    :param work_dir: Work root owning eval traces.
    :param traces_path: Optional traces path override.
    :param include_host: Host inclusion (True/False force; None = config pref).
    :param host_root: Optional host root override (tests).
    :returns: Catalog rows sorted newest activity first (``sortEpoch`` desc).
    """
    roots = catalog_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    dirs = list(collect_session_dirs(roots))
    if not dirs:
        return []
    # Cap workers: enough for disk latency, not enough to thrash a laptop.
    workers = min(16, max(4, (len(dirs) + 7) // 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        built = list(
            pool.map(
                lambda item: session_catalog_row(item[0], origin=item[1]),
                dirs,
            )
        )
    rows = [row for row in built if row is not None]
    rows.sort(
        key=lambda r: (
            -catalog_row_sort_epoch(r),
            str(r.get("sessionId") or ""),
        )
    )
    return rows


def catalog_roots_fingerprint(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> tuple[tuple[str, int, int], ...]:
    """Cheap identity for catalog roots (path, mtime_ns, entry count).

    Used by the headless owner to refresh the warm cache when the tree changes
    without always waiting for TTL expiry.
    """
    roots = catalog_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    parts: list[tuple[str, int, int]] = []
    for root in roots:
        path = Path(root.path)
        try:
            st = path.stat()
            mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        except OSError:
            parts.append((str(path), 0, 0))
            continue
        try:
            n = sum(1 for _ in path.iterdir())
        except OSError:
            n = 0
        parts.append((str(path), mtime_ns, n))
    return tuple(parts)


class SessionCatalogCache:
    """Single-flight TTL + root-fingerprint cache for ``session/list`` rows.

    Shared by the headless control owner so warm-on-start, periodic refresh, and
    client RPCs share one scan instead of serial full walks.
    """

    DEFAULT_TTL = 20.0

    def __init__(
        self,
        work_dir: Path,
        *,
        traces_path: Path | None = None,
        include_host: bool | None = None,
        host_root: Path | None = None,
        ttl: float = DEFAULT_TTL,
    ) -> None:
        import threading
        import time

        self._work_dir = Path(work_dir).expanduser()
        self._traces_path = Path(traces_path).expanduser() if traces_path is not None else None
        self._include_host = include_host
        self._host_root = host_root
        self._ttl = max(1.0, float(ttl))
        self._lock = threading.Lock()
        self._rows: list[JsonObject] | None = None
        self._mono = 0.0
        self._host_key: bool | None = None
        self._fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._building = False
        self._build_done = threading.Event()
        self._build_done.set()
        self._time = time

    def _host_key_now(self) -> bool:
        return effective_include_host(self._include_host)

    def _fp_now(self) -> tuple[tuple[str, int, int], ...]:
        return catalog_roots_fingerprint(
            self._work_dir,
            traces_path=self._traces_path,
            include_host=self._include_host,
            host_root=self._host_root,
        )

    def invalidate(self) -> None:
        """Drop cached rows so the next :meth:`get` rebuilds."""
        with self._lock:
            self._rows = None
            self._mono = 0.0
            self._fingerprint = None

    def get(self, *, force: bool = False) -> list[JsonObject]:
        """Return catalog rows, rebuilding when stale, forced, or roots changed."""
        host_key = self._host_key_now()
        while True:
            now = self._time.monotonic()
            fp = self._fp_now()
            with self._lock:
                fresh = (
                    not force
                    and self._rows is not None
                    and self._host_key is host_key
                    and self._fingerprint == fp
                    and (now - self._mono) < self._ttl
                )
                if fresh:
                    assert self._rows is not None
                    return list(self._rows)
                if self._building:
                    waiter = True
                else:
                    self._building = True
                    self._build_done.clear()
                    waiter = False
            if not waiter:
                break
            self._build_done.wait(timeout=120.0)
        try:
            rows = list_session_catalog(
                self._work_dir,
                traces_path=self._traces_path,
                include_host=self._include_host,
                host_root=self._host_root,
            )
            with self._lock:
                self._rows = rows
                self._mono = self._time.monotonic()
                self._host_key = host_key
                self._fingerprint = fp
            return list(rows)
        finally:
            with self._lock:
                self._building = False
            self._build_done.set()


def session_meta_from_catalog_row(row: JsonObject) -> SessionMeta | None:
    """Hydrate a minimal :class:`~groket.models.SessionMeta` from a list wire row.

    Used when the TUI attaches as a control client and must not re-scan disk for
    the home list. Status strings map back to outcomes so
    :meth:`~groket.models.SessionMeta.list_status_label` stays consistent.
    """
    path_raw = str(row.get("path") or "").strip()
    sid = str(row.get("sessionId") or "").strip()
    if not path_raw and not sid:
        return None
    session_dir = Path(path_raw) if path_raw else Path(sid)
    meta = SessionMeta(
        session_id=sid or session_dir.name,
        session_dir=session_dir,
        origin=str(row.get("origin") or "work").strip() or "work",
    )
    title = str(row.get("title") or "").strip()
    if title:
        meta.title = title
    model = str(row.get("model") or "").strip()
    if model:
        if ":" in model:
            mid, _, eff = model.partition(":")
            meta.model_id = mid or "unknown"
            meta.reasoning_effort = eff
        else:
            meta.model_id = model
    outcome = str(row.get("outcome") or "").strip()
    status = str(row.get("status") or "").strip().lower()
    if outcome:
        meta.turn_outcome = outcome
    elif status == "awaiting":
        meta.turn_outcome = "awaiting_follow_up"
    elif status == "running":
        meta.turn_outcome = "running"
    elif status == "ending":
        meta.turn_outcome = "ending"
    elif status == "cancelled":
        meta.turn_outcome = "cancelled"
    elif status == "complete":
        meta.turn_outcome = "success"
    task_id = str(row.get("taskId") or "").strip()
    if task_id:
        meta.task_id = task_id
    created = str(row.get("createdAt") or row.get("created_at") or "").strip()
    if created:
        meta.created_at = created
    updated = str(row.get("updatedAt") or row.get("updated_at") or "").strip()
    if updated:
        meta.updated_at = updated

    def _as_float(value: object, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return default
        return default

    def _as_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    def _opt_int(key: str) -> int | None:
        raw = row.get(key)
        if raw is None or raw == "":
            return None
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                return None
        return None

    meta.duration_seconds = _as_float(row.get("durationSeconds"), 0.0)
    meta.num_events = _as_int(row.get("numEvents"), 0)
    meta.tool_call_count = _as_int(row.get("toolCallCount"), 0)
    meta.error_count = _as_int(row.get("errorCount"), 0)

    pct = _opt_int("contextWindowUsagePct")
    if pct is not None:
        meta.context_window_usage_pct = max(0, pct)
    used = _opt_int("contextTokensUsed")
    if used is not None:
        meta.context_tokens_used = max(0, used)
    window = _opt_int("contextWindowTokens")
    if window is not None and window > 0:
        meta.context_window_tokens = window
    return meta


def resolve_session_reference(
    reference: str,
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> Path | None:
    """Resolve a path or catalog session id to an existing session directory.

    :param reference: Absolute/relative path, or a session directory name / id.
    :param work_dir: Work root for catalog roots.
    :param traces_path: Optional traces path override.
    :param include_host: Host inclusion (True/False force; None = config pref).
    :param host_root: Optional host root override (tests).
    :returns: Resolved directory path, or None when not found.
    """
    ref = (reference or "").strip()
    if not ref:
        return None
    candidate = Path(ref).expanduser()
    if candidate.is_dir():
        try:
            return candidate.resolve()
        except OSError:
            return candidate
    roots = catalog_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    for root in roots:
        direct = root.path / ref
        if direct.is_dir():
            try:
                return direct.resolve()
            except OSError:
                return direct
    for session_dir, origin in collect_session_dirs(roots):
        _ = origin
        if session_dir.name == ref:
            try:
                return session_dir.resolve()
            except OSError:
                return session_dir
        row = session_catalog_row(session_dir, origin=origin)
        if row is not None and str(row.get("sessionId") or "") == ref:
            try:
                return session_dir.resolve()
            except OSError:
                return session_dir
    return None


__all__ = [
    "SessionCatalogCache",
    "catalog_roots_fingerprint",
    "catalog_scan_roots",
    "effective_include_host",
    "list_session_catalog",
    "resolve_session_reference",
    "session_catalog_row",
    "catalog_row_sort_epoch",
    "session_meta_from_catalog_row",
    "show_host_sessions_from_config",
]
