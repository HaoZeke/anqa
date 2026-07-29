"""Session catalog roots: eval (Docker) traces and optional host Grok sessions.

Eval catalog (always): ``<work>/runs/traces`` — sessions launched by groket.
Host catalog (optional): ``~/.grok/sessions`` — native Grok Build sessions.

On-disk origin codes remain ``work`` / ``host``; the TUI labels them Eval / Host.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..parser import find_sessions

ORIGIN_WORK = "work"
ORIGIN_HOST = "host"

type SessionOrigin = str


def host_grok_sessions_root() -> Path:
    """Default Grok Build sessions root: ~/.grok/sessions."""
    return Path.home() / ".grok" / "sessions"


def work_traces_root(work_dir: Path) -> Path:
    """Docker/eval traces under the work root."""
    return Path(work_dir).expanduser() / "runs" / "traces"


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
    work_traces: Path,
    host_root: Path | None = None,
) -> SessionOrigin:
    """Return work or host for *session_dir*."""
    sd = _resolved(session_dir)
    host = _resolved(host_root) if host_root is not None else _resolved(host_grok_sessions_root())
    if sd == host or host in sd.parents:
        return ORIGIN_HOST
    wt = _resolved(work_traces)
    if sd == wt or wt in sd.parents:
        return ORIGIN_WORK
    if is_under_host_grok_sessions(session_dir):
        return ORIGIN_HOST
    return ORIGIN_WORK


@dataclass(frozen=True)
class SessionScanRoot:
    """One directory to scan for operator-facing sessions."""

    origin: SessionOrigin
    path: Path


def session_scan_roots(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool = False,
    host_root: Path | None = None,
) -> list[SessionScanRoot]:
    """Roots for the sessions home list.

    Always includes the work traces tree. When *include_host* is true, also
    includes the host Grok sessions root. An explicit *traces_path* that is
    not already covered is added with a classified origin.
    """
    work = work_traces_root(work_dir)
    host = Path(host_root).expanduser() if host_root is not None else host_grok_sessions_root()
    out: list[SessionScanRoot] = []
    seen: set[str] = set()

    def add(origin: SessionOrigin, path: Path) -> None:
        key = str(_resolved(path))
        if key in seen:
            return
        seen.add(key)
        out.append(SessionScanRoot(origin=origin, path=Path(path).expanduser()))

    add(ORIGIN_WORK, work)
    if include_host:
        add(ORIGIN_HOST, host)
    if traces_path is not None:
        tp = Path(traces_path).expanduser()
        origin = classify_session_origin(tp, work_traces=work, host_root=host)
        add(origin, tp)
    return out


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
        session_dirs = find_sessions(path)
        if not session_dirs and path.is_dir():
            try:
                children = sorted(path.iterdir())
            except OSError:
                children = []
            for sub in children:
                if sub.is_dir():
                    session_dirs.extend(find_sessions(sub))
        for sd in session_dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append((sd, root.origin))
    return found


__all__ = [
    "ORIGIN_HOST",
    "ORIGIN_WORK",
    "SessionOrigin",
    "SessionScanRoot",
    "classify_session_origin",
    "collect_session_dirs",
    "host_grok_sessions_root",
    "is_host_grok_sessions_root",
    "is_under_host_grok_sessions",
    "session_scan_roots",
    "work_traces_root",
]
