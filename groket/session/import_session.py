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
    """One host Grok session for pickers."""

    path: Path
    session_id: str
    title: str
    cwd_label: str
    mtime: float


def list_host_grok_sessions(
    root: Path | None = None,
    *,
    limit: int = 80,
) -> list[HostSessionRow]:
    """List recent operator sessions under the host Grok sessions root.

    Sorted by newest activity first. Caps at *limit* for UI pickers.
    """
    base = Path(root).expanduser() if root is not None else host_grok_sessions_root()
    if not base.is_dir():
        return []
    rows: list[HostSessionRow] = []
    for sd in find_sessions(base):
        try:
            st = sd.stat()
            mtime = st.st_mtime
            for name in ("summary.json", "events.jsonl", "chat_history.jsonl", "updates.jsonl"):
                p = sd / name
                if p.is_file():
                    mtime = max(mtime, p.stat().st_mtime)
        except OSError:
            mtime = 0.0
        sid = sd.name
        title = ""
        try:
            meta = load_session_meta(sd, include_timeline_count=False)
            sid = meta.session_id or sid
            title = (meta.title or "").strip()
        except Exception:
            logger.debug("meta load failed for %s", sd, exc_info=True)
        parent = sd.parent.name
        try:
            cwd_label = unquote(parent) if parent.startswith("%") else parent
        except Exception:
            cwd_label = parent
        rows.append(
            HostSessionRow(
                path=sd,
                session_id=sid,
                title=title,
                cwd_label=cwd_label,
                mtime=mtime,
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
]
