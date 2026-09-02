"""Grok session discovery and cheap disk stamps.

Timeline and catalog list live in ``anqa.core``. This module finds
session directories and reports mtime / size for live refresh.
"""

from __future__ import annotations

from pathlib import Path

from ..scan import find_sessions as walk_sessions
from ..scan import skip_dir_name


def _newest_mtime(session_dir: Path, names: tuple[str, ...]) -> float:
    """Newest mtime among named files under *session_dir* (0 if none)."""
    newest = 0.0
    for name in names:
        fp = session_dir / name
        try:
            if fp.is_file():
                newest = max(newest, fp.stat().st_mtime)
        except OSError:
            continue
    return newest


def session_trace_mtime(session_dir: Path) -> float:
    """Newest mtime among trace artifacts (0 if none)."""
    newest = _newest_mtime(
        session_dir,
        (
            "events.jsonl",
            "chat_history.jsonl",
            "updates.jsonl",
            "summary.json",
            "signals.json",
        ),
    )
    if newest <= 0:
        try:
            newest = session_dir.stat().st_mtime
        except OSError:
            pass
    return newest


def updates_jsonl_size(session_dir: Path) -> int:
    """Byte size of ``updates.jsonl`` (0 if missing)."""
    fp = Path(session_dir) / "updates.jsonl"
    try:
        return int(fp.stat().st_size) if fp.is_file() else 0
    except OSError:
        return 0


def _scan_hit_is_listed(root: Path, path: Path) -> bool:
    """Apply the walk policy to one compiled ``find_sessions`` hit."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = path
    if any(skip_dir_name(part) for part in rel.parts):
        return False
    if _is_subagent_session_dir(path):
        return False
    try:
        if ".anqa-resume-seed" in path.resolve().parts:
            return False
    except OSError:
        pass
    return True


def _is_subagent_session_dir(path: Path) -> bool:
    """True when *path* is under a ``subagents`` segment."""
    return "subagents" in path.parts


def _drop_subagent_mirror_sessions(sessions: list[Path]) -> list[Path]:
    """Keep primary sessions only."""
    from ..session.subagents import drop_subagent_sessions

    return drop_subagent_sessions(sessions)


def _looks_like_session_dir(path: Path, filenames: set[str]) -> bool:
    """Whether *path* has session artifacts worth listing."""
    if filenames & {"updates.jsonl", "summary.json"}:
        return True
    if "events.jsonl" in filenames:
        try:
            return (path / "events.jsonl").stat().st_size > 0
        except OSError:
            return False
    return False


def find_sessions(root: Path) -> list[Path]:
    """Recursively find operator-facing session directories.

    A session directory has ``updates.jsonl`` / ``summary.json``, or a
    non-empty ``events.jsonl``. Staging trees and Grok subagent sessions
    are omitted. Once a session dir is recognized, the walk does not
    descend into it.
    """
    if not root.exists():
        return []
    sessions = [path for path in walk_sessions(root) if _scan_hit_is_listed(root, path)]
    return _drop_subagent_mirror_sessions(sessions)


__all__ = [
    "find_sessions",
    "session_trace_mtime",
    "updates_jsonl_size",
]
