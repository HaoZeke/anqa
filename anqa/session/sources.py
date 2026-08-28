"""Session catalog roots: adapter host stores.

Native stores (``~/.grok/sessions`` and ``[catalog.roots]``) are the catalog.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from ..parser import find_sessions
from ..paths import default_host_sessions_root
from .subagents import drop_subagent_sessions

_HOST_SKIP_DIR_NAMES = frozenset(
    {
        "anqa-plugins",
        "anqa-skills",
        "subagents",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
        ".cache",
        ".tox",
        ".anqa-resume-seed",
        ".anqa-workspace-seed",
        "workspace",
        "runs",
    }
)

ORIGIN_WORK = "work"
ORIGIN_HOST = "host"

type SessionOrigin = str


def host_grok_sessions_root() -> Path:
    """Default Grok session store: ~/.grok/sessions."""
    return default_host_sessions_root()


def _resolved(path: Path) -> Path:
    p = Path(path).expanduser()
    try:
        return p.resolve()
    except OSError:
        return p


def is_host_grok_sessions_root(path: Path) -> bool:
    """True when *path* is the host Grok sessions tree (~/.grok/sessions)."""
    try:
        return _resolved(path) == _resolved(host_grok_sessions_root())
    except OSError:
        return False


def is_under_host_grok_sessions(session_dir: Path) -> bool:
    """True when *session_dir* lives under the host Grok sessions tree."""
    try:
        host = _resolved(host_grok_sessions_root())
        p = _resolved(session_dir)
    except OSError:
        return False
    if p == host:
        return True
    return host in p.parents


def classify_session_origin(
    session_dir: Path,
    *,
    host_root: Path | None = None,
) -> SessionOrigin:
    """Return host when *session_dir* is under the adapter store."""
    sd = _resolved(session_dir)
    host = _resolved(host_root) if host_root is not None else _resolved(host_grok_sessions_root())
    if sd == host or host in sd.parents:
        return ORIGIN_HOST
    if is_under_host_grok_sessions(session_dir):
        return ORIGIN_HOST
    return ORIGIN_HOST


@dataclass(frozen=True)
class SessionScanRoot:
    """One directory to scan for operator-facing sessions."""

    origin: SessionOrigin
    path: Path


def session_scan_roots(
    *,
    traces_path: Path | None = None,
    include_host: bool = True,
    host_root: Path | None = None,
) -> list[SessionScanRoot]:
    """Roots for the sessions home list.

    The host adapter store. An explicit *traces_path* that is not already
    that store is added as another host root.
    """
    host = Path(host_root).expanduser() if host_root is not None else host_grok_sessions_root()
    out: list[SessionScanRoot] = []
    seen: set[str] = set()

    def add(origin: SessionOrigin, path: Path) -> None:
        key = str(_resolved(path))
        if key in seen:
            return
        seen.add(key)
        out.append(SessionScanRoot(origin=origin, path=Path(path).expanduser()))

    if include_host:
        add(ORIGIN_HOST, host)
    if traces_path is not None:
        add(ORIGIN_HOST, Path(traces_path).expanduser())
    return out


def session_dir_for_watch_path(path: Path, root: Path) -> Path | None:
    """Nearest session directory on *path* that lives under *root*.

    Host trees nest sessions under a percent-encoded cwd bucket. The first
    component under the watch root is that bucket, not the session.
    """
    try:
        cur = Path(path).expanduser().resolve()
        root_r = Path(root).expanduser().resolve()
    except OSError:
        cur = Path(path).expanduser()
        root_r = Path(root).expanduser()
    try:
        cur.relative_to(root_r)
    except ValueError:
        return None
    if cur.is_file() or not cur.exists():
        cur = cur.parent
    while True:
        try:
            if cur == root_r:
                return None
            cur.relative_to(root_r)
        except ValueError:
            return None
        if _dir_is_session(cur):
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def is_encoded_cwd_name(name: str) -> bool:
    """True for URL-encoded absolute paths used as Grok host session buckets."""
    n = (name or "").casefold()
    return n.startswith("%2f") or "%2f" in n


def session_run_dir(session_dir: Path) -> str:
    """Host directory the session was run in.

    Host trees nest under a percent-encoded cwd bucket
    (``~/.grok/sessions/%2Fhome%2F…/<id>``). Container ``/workspace`` is
    skipped.
    """
    parent = Path(session_dir).parent.name
    if is_encoded_cwd_name(parent):
        decoded = unquote(parent)
        if decoded and decoded not in {"/workspace", "workspace"}:
            return decoded
    return ""


def is_host_skip_dir_name(name: str) -> bool:
    """Host-tree names that must not be descended (workspace / staging junk)."""
    low = (name or "").casefold()
    if not low:
        return True
    if low in _HOST_SKIP_DIR_NAMES or low.endswith(".stage"):
        return True
    return False


def _dir_is_session(path: Path) -> bool:
    """True when *path* itself is a session directory (no recursion)."""
    names: set[str] = set()
    try:
        with os.scandir(path) as it:
            for ent in it:
                if not ent.is_file(follow_symlinks=False):
                    continue
                names.add(ent.name)
                if names & {"summary.json", "updates.jsonl"}:
                    return True
    except OSError:
        return False
    if "events.jsonl" in names:
        try:
            return (path / "events.jsonl").stat().st_size > 0
        except OSError:
            return False
    return False


def _immediate_session_children(path: Path) -> list[Path]:
    """Session dirs that are direct children of *path* (no deeper walk)."""
    out: list[Path] = []
    try:
        with os.scandir(path) as it:
            children = list(it)
    except OSError:
        return out
    for ent in children:
        if not ent.is_dir(follow_symlinks=False):
            continue
        if is_host_skip_dir_name(ent.name):
            continue
        child = Path(ent.path)
        if _dir_is_session(child):
            out.append(child)
    return out


def list_host_session_dirs(root: Path) -> list[Path]:
    """Host session dirs by tree shape: children or one encoded-cwd level.

    Uses directory entries only (no ``summary.json`` read). Does not walk
    ``workspace`` / staging junk or recurse into a session.
    """
    path = Path(root).expanduser()
    if not path.is_dir():
        return []
    found: list[Path] = []
    try:
        with os.scandir(path) as it:
            tops = list(it)
    except OSError:
        return found
    for ent in tops:
        if not ent.is_dir(follow_symlinks=False):
            continue
        name = ent.name
        if is_host_skip_dir_name(name):
            continue
        child = Path(ent.path)
        if _dir_is_session(child):
            found.append(child)
            continue
        if is_encoded_cwd_name(name):
            found.extend(_immediate_session_children(child))
    return found


def collect_host_session_dirs(root: Path) -> list[Path]:
    """Host sessions for the operator catalog (tree shape, then drop children)."""
    return drop_subagent_sessions(list_host_session_dirs(root))


def collect_session_dirs(
    roots: list[SessionScanRoot],
) -> list[tuple[Path, SessionOrigin]]:
    """Find unique session directories across *roots* (first origin wins)."""
    found: list[tuple[Path, SessionOrigin]] = []
    seen: set[str] = set()
    for root in roots:
        path = root.path
        if not path.exists():
            continue
        if root.origin == ORIGIN_HOST:
            session_dirs = collect_host_session_dirs(path)
        else:
            session_dirs = find_sessions(path)
        for sd in session_dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append((sd, root.origin))
    kept = {str(p) for p in drop_subagent_sessions([sd for sd, _ in found])}
    return [(sd, origin) for sd, origin in found if str(sd) in kept]


__all__ = [
    "ORIGIN_HOST",
    "ORIGIN_WORK",
    "SessionOrigin",
    "SessionScanRoot",
    "classify_session_origin",
    "collect_host_session_dirs",
    "collect_session_dirs",
    "list_host_session_dirs",
    "host_grok_sessions_root",
    "is_encoded_cwd_name",
    "session_dir_for_watch_path",
    "session_run_dir",
    "is_host_skip_dir_name",
    "is_host_grok_sessions_root",
    "is_under_host_grok_sessions",
    "session_scan_roots",
]
