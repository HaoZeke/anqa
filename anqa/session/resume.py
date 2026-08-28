"""Skip resume substrate and locate a fork's parent session."""

from __future__ import annotations

from pathlib import Path

# Leftover work trees use cwd=/workspace → sessions under this token.
_DEFAULT_CWD_TOKEN = "%2Fworkspace"
# Staging tree under a leftover traces volume. The session list skips this
# name (same class of convention as ``*.stage`` / turn-gate dirs).
RESUME_SEED_DIRNAME = ".anqa-resume-seed"


def can_resume_session(session_dir: Path) -> bool:
    """True when *session_dir* has artifacts a fork can resume from."""
    p = Path(session_dir)
    if not p.is_dir():
        return False
    for name in ("chat_history.jsonl", "summary.json", "events.jsonl"):
        if (p / name).is_file():
            return True
    return False


def is_resume_seed_path(path: Path) -> bool:
    """True when *path* is resume history substrate, not an operator session.

    Substrate lives under :data:`RESUME_SEED_DIRNAME`, or is a live path that
    resolves into that tree (symlink placed for a fork resume).
    """
    p = Path(path)
    if RESUME_SEED_DIRNAME in p.parts:
        return True
    try:
        if RESUME_SEED_DIRNAME in p.resolve().parts:
            return True
    except OSError:
        return False
    return False


def fork_parent_session_dir(session_dir: Path) -> Path | None:
    """Return the seeded parent session for a forked child, or ``None``.

    Existing fork-resume records write ``resume_parent_session_id`` /
    ``resume_fork_session_id`` into ``anqa-launch.json``. The host keeps the
    parent under ``.anqa-resume-seed/<cwd-token>/<parent_id>/``.

    :param session_dir: Candidate forked child session directory.
    :returns: Parent seed path when *session_dir* is the forked child and the
        seed exists; otherwise ``None``.
    """
    from .launch_meta import find_launch_meta_file, read_launch_meta

    sd = Path(session_dir)
    launch = read_launch_meta(sd)
    if launch is None or not launch.is_fork_resume:
        return None
    parent_id = (launch.resume_parent_session_id or "").strip()
    child_id = (launch.resume_fork_session_id or "").strip()
    if not parent_id or not child_id or sd.name != child_id:
        return None

    meta_file = find_launch_meta_file(sd)
    if meta_file is None:
        return None
    traces_vol = meta_file.parent
    token = sd.parent.name
    if not token:
        token = _DEFAULT_CWD_TOKEN

    candidates = (
        traces_vol / RESUME_SEED_DIRNAME / token / parent_id,
        traces_vol / token / parent_id,
    )
    for cand in candidates:
        if cand.is_dir() and can_resume_session(cand):
            try:
                return cand.resolve() if cand.is_symlink() else cand
            except OSError:
                return cand
    return None
