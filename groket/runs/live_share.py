"""Grok share URLs for eval sessions — host side is read-only.

Share creation happens **only in the eval container** via the entrypoint loop::

    grok share <session-id>

which writes ``groket-share.json`` next to the session (bind-mounted into traces).

The host TUI only reads that file for Jobs / Summary / Stats / open-share.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject, json_as_int

SHARE_FILENAME = "groket-share.json"
LIVE_SHARES_INDEX = "live_shares.jsonl"  # under work_dir/runs/ (optional operator index)
_LOCK = threading.Lock()
_REGISTRY: dict[str, ShareResult] = {}


@dataclass
class ShareResult:
    session_id: str
    session_dir: str
    share_url: str = ""
    error: str = ""
    created_at: str = ""
    source: str = ""  # incontainer | pending | …
    method: str = ""  # cli
    snapshot_n: int = 0
    snapshot_at: str = ""
    updated_at: str = ""
    note: str = ""

    def to_dict(self) -> JsonObject:
        d: JsonObject = {
            "session_id": self.session_id,
            "session_dir": self.session_dir,
            "share_url": self.share_url,
            "error": self.error,
            "created_at": self.created_at,
            "source": self.source,
        }
        if self.method:
            d["method"] = self.method
        if self.snapshot_n:
            d["snapshot_n"] = self.snapshot_n
        if self.snapshot_at:
            d["snapshot_at"] = self.snapshot_at
        if self.updated_at:
            d["updated_at"] = self.updated_at
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, data: JsonObject, *, session_dir: Path | None = None) -> ShareResult:
        try:
            snap_n = json_as_int(data.get("snapshot_n"), 0)
        except (TypeError, ValueError):
            snap_n = 0
        return cls(
            session_id=str(data.get("session_id") or ""),
            session_dir=str(data.get("session_dir") or (session_dir or "")),
            share_url=str(data.get("share_url") or ""),
            error=str(data.get("error") or ""),
            created_at=str(data.get("created_at") or ""),
            source=str(data.get("source") or ""),
            method=str(data.get("method") or ""),
            snapshot_n=snap_n,
            snapshot_at=str(data.get("snapshot_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            note=str(data.get("note") or ""),
        )


def share_path_for(session_dir: Path | str) -> Path:
    return Path(session_dir) / SHARE_FILENAME


def _read_share_file(session_dir: Path | str) -> JsonObject | None:
    sp = share_path_for(session_dir)
    if not sp.is_file():
        return None
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def is_share_not_ready_error(err: str) -> bool:
    e = (err or "").lower()
    return "no messages to share" in e or "nothing to share" in e


def load_cached_share(session_dir: Path | str) -> ShareResult | None:
    """Return share only when a real URL is present in groket-share.json."""
    sd = Path(session_dir)
    data = _read_share_file(sd)
    if not data:
        return None
    res = ShareResult.from_dict(data, session_dir=sd)
    if not res.share_url:
        return None
    try:
        key = str(sd.resolve())
    except Exception:
        key = str(sd)
    with _LOCK:
        _REGISTRY[key] = res
    return res


def get_share_url(session_dir: Path | str) -> str:
    """Latest share URL from disk (empty if container has not written one yet)."""
    sd = Path(session_dir)
    res = load_cached_share(sd)
    if res and res.share_url:
        return res.share_url
    try:
        key = str(sd.resolve())
    except Exception:
        key = str(sd)
    with _LOCK:
        reg = _REGISTRY.get(key)
        if reg and reg.share_url:
            return reg.share_url
    return ""


def get_share_display(session_dir: Path | str) -> JsonObject:
    """Fields for Summary / Stats (always re-reads groket-share.json)."""
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
        out["pending"] = True
        out["note"] = "Waiting for container entrypoint to run: grok share <session-id>"
        return out

    try:
        snap_n = json_as_int(data.get("snapshot_n"), 0)
    except (TypeError, ValueError):
        snap_n = 0
    err = str(data.get("error") or "")
    url = str(data.get("share_url") or "")
    src = str(data.get("source") or "")
    pending = not url and (src == "pending" or is_share_not_ready_error(err) or not err)
    if not url and err and not is_share_not_ready_error(err):
        pending = False

    out.update(
        {
            "share_url": url,
            "error": err,
            "source": src,
            "method": str(data.get("method") or ""),
            "snapshot_n": snap_n,
            "snapshot_at": str(data.get("snapshot_at") or ""),
            "updated_at": str(data.get("updated_at") or ""),
            "note": str(data.get("note") or ""),
            "pending": pending and not url,
            "ready": bool(url),
        }
    )
    return out


def format_share_summary_markdown(session_dir: Path | str) -> str:
    """Markdown block for Summary tab."""
    info = get_share_display(session_dir)
    lines = ["## Grok share", ""]
    url = info.get("share_url") or ""

    if url:
        lines.append(f"- **URL:** {url}")
        lines.append("")
        lines.append(f"  `{url}`")
    elif info.get("pending"):
        lines.append("- **URL:** _pending_ (container has not produced a share yet)")
        lines.append(
            "- **How it works:** eval entrypoint runs `grok share <session-id>` "
            "periodically and writes `groket-share.json`. Press **F5** to refresh."
        )
    else:
        lines.append("- **URL:** _not available_")

    if info.get("snapshot_n"):
        lines.append(f"- **Snapshot #:** {info['snapshot_n']}")
    if info.get("snapshot_at"):
        lines.append(f"- **Snapshot at:** `{info['snapshot_at']}`")
    if info.get("method"):
        lines.append(f"- **Method:** `{info['method']}`")
    if info.get("source"):
        lines.append(f"- **Source:** `{info['source']}`")
    if info.get("error") and not url:
        lines.append(f"- **Error:** {str(info['error'])[:500]}")
    elif info.get("error") and url and not is_share_not_ready_error(str(info["error"])):
        lines.append(f"- **Note (last non-fatal):** {str(info['error'])[:200]}")

    lines.append(
        "- **Tip:** Share is created in the container only. Reload the share page "
        "after a new snapshot; the TUI only displays `groket-share.json` (key **s** opens URL)."
    )
    if info.get("note") and not url:
        lines.append(f"- **Detail:** {info['note']}")
    return "\n".join(lines)


def format_share_stats_line(session_dir: Path | str) -> str:
    """Plain-text share line for summary-style displays."""
    info = get_share_display(session_dir)
    url = info.get("share_url") or ""
    if url:
        lines = [f"  Share URL: {url}\n"]
        meta = []
        if info.get("snapshot_n"):
            meta.append(f"#{info['snapshot_n']}")
        if info.get("method"):
            meta.append(str(info["method"]))
        if info.get("snapshot_at"):
            meta.append(str(info["snapshot_at"]))
        if meta:
            lines.append(f"  Share meta: {', '.join(meta)}\n")
        lines.append("  Share tip:  reload page for latest snapshot; s opens URL\n")
        return "".join(lines)
    if info.get("error") and not info.get("pending"):
        err = str(info["error"]).replace("\n", " ")[:160]
        return (
            f"  Share:     failed — {err}\n  Share tip:  needs working `grok share` in container\n"
        )
    return (
        "  Share:     pending (container `grok share <session-id>` → groket-share.json)\n"
        "  Share tip:  F5 refresh; requires image with entrypoint share loop\n"
    )


def refresh_share_from_disk(session_dir: Path | str) -> str:
    """Re-read disk and update in-memory registry; return URL or empty."""
    sd = Path(session_dir)
    try:
        key = str(sd.resolve())
    except Exception:
        key = str(sd)
    res = load_cached_share(sd)
    if res:
        return res.share_url
    with _LOCK:
        _REGISTRY.pop(key, None)
    return ""


__all__ = [
    "LIVE_SHARES_INDEX",
    "SHARE_FILENAME",
    "ShareResult",
    "format_share_stats_line",
    "format_share_summary_markdown",
    "get_share_display",
    "get_share_url",
    "is_share_not_ready_error",
    "load_cached_share",
    "refresh_share_from_disk",
    "share_path_for",
]
