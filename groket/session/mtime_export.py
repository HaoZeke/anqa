"""Stamp-gated host catalog snapshot.

Host ``session/list`` rows are built by :func:`session_catalog_row` (summary,
signals, and the 64 KiB updates tail). Reuse a cached row when that
session's stamp (path + ``summary.json`` / ``signals.json`` /
``updates.jsonl`` mtimes) is unchanged. Serve and ``groket export-host``
share this file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from ..integrations.control_contract import PROTOCOL_VERSION
from ..models import JsonObject, as_json_object
from ..paths import cache_dir
from .sources import host_grok_sessions_root, list_host_session_dirs
from .subagents import drop_subagent_sessions

_STAMP_FILES = ("summary.json", "signals.json", "updates.jsonl")


def _mtime_ns(path: Path) -> int:
    try:
        st = path.stat()
    except OSError:
        return 0
    return int(st.st_mtime_ns)


def host_source_stamp(session_dir: Path) -> tuple[str, int, int, int]:
    """Identity for one host session: path plus summary/signals/updates mtimes."""
    return (
        str(session_dir),
        _mtime_ns(session_dir / _STAMP_FILES[0]),
        _mtime_ns(session_dir / _STAMP_FILES[1]),
        _mtime_ns(session_dir / _STAMP_FILES[2]),
    )


def default_catalog_snapshot(root: Path) -> Path:
    """Per-root snapshot under the local cache directory."""
    key = hashlib.sha256(str(Path(root).expanduser()).encode()).hexdigest()[:16]
    return cache_dir() / f"catalog-{key}.json"


def _stamp_ns(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_stamps(raw: object) -> list[tuple[str, int, int, int]] | None:
    if not isinstance(raw, list):
        return None
    got: list[tuple[str, int, int, int]] = []
    for item in raw:
        if not (isinstance(item, list) and len(item) == 4):
            return None
        t0, t1, t2 = _stamp_ns(item[1]), _stamp_ns(item[2]), _stamp_ns(item[3])
        if t0 is None or t1 is None or t2 is None:
            return None
        got.append((str(item[0]), t0, t1, t2))
    return got


def export_is_stale(stamps: list[tuple[str, int, int, int]], dest: Path) -> bool:
    """True when *dest* is missing or its stored stamps do not match *stamps*."""
    cached = _read_payload(dest)
    if cached is None or cached.get("version") != PROTOCOL_VERSION:
        return True
    got = _parse_stamps(cached.get("stamps"))
    return got is None or got != stamps


def _read_payload(dest: Path) -> JsonObject | None:
    try:
        if not dest.is_file():
            return None
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _cached_stamp_map(cached: JsonObject | None) -> dict[str, tuple[int, int, int]] | None:
    if cached is None or cached.get("version") != PROTOCOL_VERSION:
        return None
    parsed = _parse_stamps(cached.get("stamps"))
    if parsed is None:
        return None
    return {path: (a, b, c) for path, a, b, c in parsed}


def _cached_rows_by_path(cached: JsonObject | None) -> dict[str, JsonObject]:
    if cached is None:
        return {}
    raw = cached.get("sessions")
    if not isinstance(raw, list):
        return {}
    out: dict[str, JsonObject] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = as_json_object(item)
        path = str(row.get("path") or "").strip()
        sid = str(row.get("sessionId") or "").strip()
        if path:
            out[path] = row
        if sid:
            out[sid] = row
    return out


def _resolved_str(session_dir: Path) -> str:
    try:
        return str(session_dir.resolve())
    except OSError:
        return str(session_dir)


def _row_for_dir(
    session_dir: Path,
    *,
    now: tuple[int, int, int],
    prev_stamps: dict[str, tuple[int, int, int]] | None,
    prev_rows: dict[str, JsonObject],
    build_row: Callable[[Path], JsonObject | None],
) -> JsonObject | None:
    key = str(session_dir)
    if prev_stamps is not None and prev_stamps.get(key) == now:
        reused = prev_rows.get(key) or prev_rows.get(_resolved_str(session_dir))
        if reused is None:
            reused = prev_rows.get(session_dir.name)
        if reused is not None:
            return reused
    return build_row(session_dir)


def _write_snapshot(
    dest: Path,
    *,
    root: Path,
    stamps: list[tuple[str, int, int, int]],
    rows: list[JsonObject],
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "version": PROTOCOL_VERSION,
                "root": str(root),
                "stamps": [list(s) for s in stamps],
                "sessions": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_or_rebuild_catalog(
    root: Path,
    *,
    dest: Path | None = None,
    build_row: Callable[[Path], JsonObject | None],
    list_dirs: Callable[[Path], list[Path]] | None = None,
) -> list[JsonObject]:
    """Return catalog rows for *root*, rebuilding only sessions whose stamps changed."""
    root = Path(root).expanduser()
    dest_path = Path(dest).expanduser() if dest is not None else default_catalog_snapshot(root)
    list_fn = list_dirs if list_dirs is not None else list_host_session_dirs
    dirs = list_fn(root)
    stamps = [host_source_stamp(sd) for sd in dirs]
    cached = _read_payload(dest_path)
    if cached is not None and not export_is_stale(stamps, dest_path):
        sessions = cached.get("sessions")
        if isinstance(sessions, list):
            return [as_json_object(row) for row in sessions if isinstance(row, dict)]
    now_by_path = {path: (a, b, c) for path, a, b, c in stamps}
    prev_stamps = _cached_stamp_map(cached)
    prev_rows = _cached_rows_by_path(cached)
    rows: list[JsonObject] = []
    for sd in drop_subagent_sessions(dirs):
        now = now_by_path.get(str(sd))
        if now is None:
            continue
        row = _row_for_dir(
            sd,
            now=now,
            prev_stamps=prev_stamps,
            prev_rows=prev_rows,
            build_row=build_row,
        )
        if row is not None:
            rows.append(row)
    _write_snapshot(dest_path, root=root, stamps=stamps, rows=rows)
    return rows


def write_host_catalog_export(
    dest: Path,
    *,
    host_root: Path | None = None,
    build_row: Callable[[Path], JsonObject | None] | None = None,
) -> Path:
    """Write the host catalog snapshot to *dest*. Does not start serve."""
    from .catalog import session_catalog_row

    root = Path(host_root).expanduser() if host_root is not None else host_grok_sessions_root()
    builder = build_row or (lambda sd: session_catalog_row(sd, origin="host"))
    load_or_rebuild_catalog(root, dest=dest, build_row=builder)
    return Path(dest).expanduser()
