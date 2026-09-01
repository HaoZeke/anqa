"""Cheap list-meta for one-jsonl stores.

A catalog row reads the first object and a 64 KiB tail. It never loads
the full transcript. Adapters still parse the whole file on open.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import JsonObject, SessionMeta, as_json_object
from ..stamp import Stamp


def _object_line(line: str) -> JsonObject | None:
    try:
        val = json.loads(line)
    except json.JSONDecodeError:
        return None
    return as_json_object(val) if isinstance(val, dict) else None


_TAIL_BYTES = 64 * 1024
_HEAD_LIMIT = 16
_TAIL_LIMIT = 16


def first_json_objects(path: Path, *, limit: int = _HEAD_LIMIT) -> list[JsonObject]:
    """Up to *limit* JSON objects from the start of *path*."""
    out: list[JsonObject] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                row = _object_line(line)
                if row is None:
                    continue
                out.append(row)
                if len(out) >= limit:
                    break
    except OSError:
        return []
    return out


def first_json_object(path: Path) -> JsonObject | None:
    """First JSON object in *path*, or None."""
    rows = first_json_objects(path, limit=1)
    return rows[0] if rows else None


def last_json_objects(path: Path, *, limit: int = _TAIL_LIMIT) -> list[JsonObject]:
    """Up to *limit* JSON objects from the last 64 KiB of *path*."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _TAIL_BYTES:
                handle.seek(size - _TAIL_BYTES)
                handle.readline()
            raw = handle.read()
    except OSError:
        return []
    out: list[JsonObject] = []
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        row = _object_line(line.decode("utf-8", errors="replace"))
        if row is not None:
            out.append(row)
    return out[-limit:]


def list_window(
    path: Path, *, head: int = _HEAD_LIMIT, tail: int = _TAIL_LIMIT
) -> list[JsonObject]:
    """Header and tail objects. Files at or under 64 KiB are read once."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size <= _TAIL_BYTES:
        return last_json_objects(path, limit=max(head + tail, 10_000))
    return first_json_objects(path, limit=head) + last_json_objects(path, limit=tail)


def file_list_meta(
    path: Path,
    *,
    session_id: str,
    harness: str,
    title: str = "",
    model_id: str = "",
    created_at: str = "",
    updated_at: str = "",
    turn_outcome: str = "",
    tool_call_count: int = 0,
    harness_version: str = "",
    run_dir: str = "",
    has_subagents: bool = False,
    subagent_count: int = 0,
) -> SessionMeta:
    """List-grade meta with file mtime as the updated stamp when missing."""
    stamp = Stamp.file(path)
    updated = updated_at or Stamp.iso(stamp[0]) or ""
    start = Stamp.epoch(created_at)
    end = Stamp.epoch(updated) or int(stamp[0] or 0)
    duration = float(max(0, end - start)) if start else 0.0
    return SessionMeta(
        session_id=session_id,
        session_dir=path,
        model_id=model_id or "unknown",
        title=title,
        created_at=created_at,
        updated_at=updated,
        duration_seconds=duration,
        tool_call_count=tool_call_count,
        run_dir=run_dir,
        turn_outcome=turn_outcome,
        harness=harness,
        harness_version=harness_version,
        has_subagents=has_subagents,
        subagent_count=subagent_count,
    )
