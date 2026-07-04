"""Process-wide per-session analysis inflight tracking.

Prevents browser auto-analyze and home-list analyze from enqueueing a second
job for the same ``session_dir`` while one is already queued or running.
"""

from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.Lock()
_inflight: set[str] = set()


def analysis_session_key(session_dir: Path | str) -> str:
    """Stable identity for a session directory."""
    p = Path(session_dir)
    try:
        return str(p.expanduser().resolve())
    except OSError:
        return str(p.expanduser())


def try_begin_session_analysis(session_dir: Path | str) -> bool:
    """Mark *session_dir* inflight. Return False if already in the pipeline."""
    key = analysis_session_key(session_dir)
    with _lock:
        if key in _inflight:
            return False
        _inflight.add(key)
        return True


def end_session_analysis(session_dir: Path | str) -> None:
    """Clear inflight for *session_dir* (idempotent)."""
    key = analysis_session_key(session_dir)
    with _lock:
        _inflight.discard(key)


def session_analysis_inflight(session_dir: Path | str) -> bool:
    """True when analysis is queued or running for *session_dir*."""
    key = analysis_session_key(session_dir)
    with _lock:
        return key in _inflight


def clear_session_analysis_inflight() -> None:
    """Drop all inflight keys (tests / process teardown)."""
    with _lock:
        _inflight.clear()


def session_analysis_inflight_count() -> int:
    with _lock:
        return len(_inflight)
