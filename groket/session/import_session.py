"""Import native Grok Build sessions (``~/.grok/sessions``) into a work traces tree.

Native sessions already parse via :func:`~groket.parser.find_sessions` if you
point the TUI at ``~/.grok/sessions``. Import **copies** (or optionally
symlinks) a session into ``<work>/runs/traces/imported/…`` so it shows up next
to eval runs without changing the active traces root.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from ..parser import find_sessions, load_session_meta
from ..paths import default_traces_root

logger = logging.getLogger(__name__)

IMPORT_KIND = "host-grok-session"
IMPORT_META_NAME = "groket-import.json"
IMPORTED_DIRNAME = "imported"
# summary.json is small; cap read so a corrupt huge file cannot stall the picker.
_SUMMARY_READ_MAX = 64 * 1024
_ACTIVITY_FILES = ("summary.json", "updates.jsonl", "events.jsonl")


def host_grok_sessions_root() -> Path:
    """Default Grok Build sessions root: ``~/.grok/sessions``."""
    return Path.home() / ".grok" / "sessions"


def is_session_directory(path: Path) -> bool:
    """True when *path* looks like a Grok session dir (same markers as find_sessions)."""
    path = Path(path).expanduser()
    if not path.is_dir():
        return False
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    if names & {"updates.jsonl", "summary.json"}:
        return True
    if "events.jsonl" in names:
        try:
            return (path / "events.jsonl").stat().st_size > 0
        except OSError:
            return False
    if "chat_history.jsonl" in names:
        try:
            return (path / "chat_history.jsonl").stat().st_size > 0
        except OSError:
            return False
    return False


@dataclass(frozen=True)
class HostSessionRow:
    """One host Grok session for pickers (light metadata only)."""

    path: Path
    session_id: str
    title: str
    cwd_label: str
    mtime: float

    def search_fields(self) -> list[str]:
        """Fields and path fragments used for import-picker filtering.

        Includes title, decoded project path, session id, full path, and path
        segments (so operators can type a repo basename or any path fragment).
        """
        fields: list[str] = [
            self.title or "",
            self.cwd_label or "",
            self.session_id or "",
            str(self.path),
            self.path.name,
        ]
        # Decoded cwd segments: /home/ali/proj → home, ali, proj
        for part in Path(self.cwd_label or "").parts:
            if part and part != "/":
                fields.append(part)
        # Encoded parent token + unquoted form (Grok sessions layout).
        parent = self.path.parent.name
        if parent:
            fields.append(parent)
            try:
                fields.append(unquote(parent))
            except Exception:
                pass
            for part in unquote(parent).replace("\\", "/").split("/"):
                if part:
                    fields.append(part)
        # Dedupe while preserving order (cheap for small lists).
        seen: set[str] = set()
        out: list[str] = []
        for f in fields:
            s = (f or "").strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    def search_text(self) -> str:
        """Joined haystack for display/debug."""
        return " ".join(self.search_fields())


def match_host_session(query: str, row: HostSessionRow) -> float:
    """Score *row* against a picker query; ``0`` means no match.

    Whitespace-separated tokens are AND'd. Each token must appear as a
    case-insensitive substring of title, project path, session id, or a path
    fragment. Higher scores prefer title hits, then path, then id.

    ``~`` / ``~/…`` expands to the user home path so project filters work either
    way.
    """
    raw = (query or "").strip()
    if not raw:
        return 1.0
    q = raw
    if q == "~":
        q = str(Path.home())
    elif q.startswith("~/"):
        q = str(Path.home() / q[2:])
    tokens = [t for t in q.lower().split() if t]
    if not tokens:
        return 1.0

    fields_l = [f.lower() for f in row.search_fields()]
    # Field weight: title first, then cwd, then the rest.
    weights: list[float] = []
    for i, _f in enumerate(fields_l):
        if i == 0:
            weights.append(100.0)
        elif i == 1:
            weights.append(80.0)
        else:
            weights.append(40.0)

    total = 0.0
    for tok in tokens:
        best = 0.0
        for field, weight in zip(fields_l, weights, strict=False):
            if tok in field:
                # Longer match / earlier field ranks higher.
                best = max(best, weight + min(len(tok), 48))
        if best <= 0:
            return 0.0
        total += best
    return total


def _cwd_label(session_dir: Path) -> str:
    parent = session_dir.parent.name
    if parent.startswith("%"):
        try:
            return unquote(parent)
        except Exception:
            return parent
    return parent


def _activity_mtime(session_dir: Path) -> float:
    """Newest mtime among the session dir and small activity markers (no full reads)."""
    mtime = 0.0
    try:
        mtime = float(session_dir.stat().st_mtime)
    except OSError:
        pass
    for name in _ACTIVITY_FILES:
        p = session_dir / name
        try:
            if p.is_file():
                mtime = max(mtime, float(p.stat().st_mtime))
        except OSError:
            continue
    return mtime


def _summary_id_and_title(session_dir: Path) -> tuple[str, str]:
    """session_id and title from ``summary.json`` only — not full :func:`load_session_meta`."""
    sid = session_dir.name
    title = ""
    fp = session_dir / "summary.json"
    if not fp.is_file():
        return sid, title
    try:
        raw = fp.read_bytes()
        if len(raw) > _SUMMARY_READ_MAX:
            raw = raw[:_SUMMARY_READ_MAX]
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return sid, title
    if not isinstance(data, dict):
        return sid, title
    sid = str(data.get("session_id") or sid).strip() or sid
    title = str(data.get("generated_title") or data.get("session_summary") or "").strip()
    if len(title) > 200:
        title = title[:200]
    return sid, title


def list_host_grok_sessions(
    root: Path | None = None,
    *,
    limit: int = 0,
) -> list[HostSessionRow]:
    """List host Grok sessions for pickers (newest first).

    Cheap pass: walk via :func:`~groket.parser.find_sessions`, then per session
    only ``stat`` activity markers and a capped read of ``summary.json``. Does
    **not** call :func:`~groket.parser.load_session_meta` (that path scans
    signals, events, gates, run meta and is far too slow for thousands of dirs).

    :param root: Sessions root (default ``~/.grok/sessions``).
    :param limit: Max rows after sort; ``0`` means no cap.
    """
    base = Path(root).expanduser() if root is not None else host_grok_sessions_root()
    if not base.is_dir():
        return []
    rows: list[HostSessionRow] = []
    for sd in find_sessions(base):
        sid, title = _summary_id_and_title(sd)
        rows.append(
            HostSessionRow(
                path=sd,
                session_id=sid,
                title=title,
                cwd_label=_cwd_label(sd),
                mtime=_activity_mtime(sd),
            )
        )
    rows.sort(key=lambda r: r.mtime, reverse=True)
    if limit > 0:
        return rows[:limit]
    return rows


def _import_dest(
    source: Path,
    traces_root: Path,
    *,
    session_id: str,
) -> Path:
    """Destination path under *traces_root*/imported/<cwd-token>/<session_id>."""
    parent_token = source.parent.name or "unknown-cwd"
    # Keep URL-encoded cwd tokens stable; slug only if path-like junk
    safe_parent = (
        "".join(c if c.isalnum() or c in "._%-" else "_" for c in parent_token)[:120] or "cwd"
    )
    safe_sid = (
        "".join(c if c.isalnum() or c in "._-" else "_" for c in (session_id or source.name))[:80]
        or "session"
    )
    return Path(traces_root).expanduser() / IMPORTED_DIRNAME / safe_parent / safe_sid


@dataclass(frozen=True)
class ImportSessionResult:
    """Outcome of :func:`import_session`."""

    source: Path
    dest: Path
    session_id: str
    linked: bool


def import_session(
    source: Path,
    *,
    traces_root: Path | None = None,
    link: bool = False,
    force: bool = False,
) -> ImportSessionResult:
    """Copy (or symlink) a native Grok session into the work traces tree.

    :param source: Path to a session directory (…/<session_id>/ with summary/events).
    :param traces_root: Destination traces root (default ``~/.groket/work/runs/traces``).
    :param link: When True, create a symlink instead of copying.
    :param force: Replace an existing destination.
    :returns: Paths and session id.
    :raises FileNotFoundError: Source missing.
    :raises ValueError: Source not a session, or dest exists without *force*.
    :raises OSError: Copy/link failure.
    """
    src = Path(source).expanduser().resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"session directory not found: {src}")
    if not is_session_directory(src):
        raise ValueError(
            f"not a Grok session directory (need summary.json / updates.jsonl / events): {src}"
        )
    root = Path(traces_root).expanduser() if traces_root is not None else default_traces_root()
    try:
        root = root.resolve()
    except OSError:
        pass

    sid = src.name
    try:
        meta = load_session_meta(src, include_timeline_count=False)
        if meta.session_id:
            sid = meta.session_id
    except Exception:
        logger.debug("could not load meta for import id", exc_info=True)

    dest = _import_dest(src, root, session_id=sid)
    if dest.exists() or dest.is_symlink():
        if not force:
            raise ValueError(f"import destination already exists (use force to replace): {dest}")
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    if link:
        dest.symlink_to(src, target_is_directory=True)
        linked = True
    else:
        shutil.copytree(src, dest, symlinks=True)
        linked = False

    try:
        if linked:
            # Do not write into the live host session; meta next to the link.
            meta_file = dest.parent / f"{dest.name}.{IMPORT_META_NAME}"
        else:
            meta_file = dest / IMPORT_META_NAME
        payload = {
            "kind": IMPORT_KIND,
            "source": str(src),
            "dest": str(dest.resolve()),
            "session_id": sid,
            "linked": linked,
            "imported_at": datetime.now(UTC).isoformat(),
        }
        meta_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.debug("could not write import meta for %s", dest, exc_info=True)

    logger.info("Imported Grok session %s → %s (link=%s)", sid, dest, linked)
    return ImportSessionResult(
        source=src,
        dest=dest.resolve() if not linked else dest,
        session_id=sid,
        linked=linked,
    )


__all__ = [
    "IMPORT_KIND",
    "IMPORT_META_NAME",
    "IMPORTED_DIRNAME",
    "HostSessionRow",
    "ImportSessionResult",
    "host_grok_sessions_root",
    "import_session",
    "is_session_directory",
    "list_host_grok_sessions",
    "match_host_session",
]
