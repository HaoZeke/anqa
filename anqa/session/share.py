"""Read ``anqa-share.json`` written beside a session (viewing only)."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import JsonObject, json_as_int

SHARE_FILENAME = "anqa-share.json"
SHARE_FILENAMES = (SHARE_FILENAME, "groket-share.json")


def share_path_for(session_dir: Path | str) -> Path:
    """Path of ``anqa-share.json`` under *session_dir*."""
    return Path(session_dir) / SHARE_FILENAME


def _read_share_file(session_dir: Path | str) -> JsonObject | None:
    root = Path(session_dir)
    for name in SHARE_FILENAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def get_share_url(session_dir: Path | str) -> str:
    """Share URL from disk, or empty."""
    data = _read_share_file(session_dir)
    if not data:
        return ""
    err = str(data.get("error") or "")
    url = str(data.get("share_url") or "")
    return url if url and not err else ""


def get_share_display(session_dir: Path | str) -> JsonObject:
    """Fields for Summary / share key."""
    out: JsonObject = {
        "share_url": "",
        "error": "",
        "source": "",
        "method": "",
        "snapshot_n": 0,
        "snapshot_at": "",
        "updated_at": "",
        "note": "",
        "pending": False,
        "ready": False,
    }
    data = _read_share_file(session_dir)
    if not data:
        return out
    try:
        snap_n = json_as_int(data.get("snapshot_n"), 0)
    except (TypeError, ValueError):
        snap_n = 0
    err = str(data.get("error") or "")
    url = str(data.get("share_url") or "")
    ready = bool(url) and not err
    out.update(
        {
            "share_url": url if ready else "",
            "error": err,
            "source": str(data.get("source") or ""),
            "method": str(data.get("method") or ""),
            "snapshot_n": snap_n,
            "snapshot_at": str(data.get("snapshot_at") or ""),
            "updated_at": str(data.get("updated_at") or ""),
            "note": str(data.get("note") or ""),
            "pending": not ready,
            "ready": ready,
        }
    )
    return out


def refresh_share_from_disk(session_dir: Path | str) -> str:
    """Re-read disk; return URL or empty."""
    return get_share_url(session_dir)
