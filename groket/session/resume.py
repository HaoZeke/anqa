"""Seed an ended Grok session into a new eval traces volume for ``--resume``."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Eval containers use cwd=/workspace → sessions under this token.
_DEFAULT_CWD_TOKEN = "%2Fworkspace"


def resume_session_id(session_dir: Path) -> str:
    """Grok session id (directory name) for *session_dir*."""
    return Path(session_dir).name.strip()


def resume_cwd_token(session_dir: Path) -> str:
    """Cwd-encoded parent segment under the container sessions mount.

    Typical layout: ``traces/<container>/%2Fworkspace/<session_id>/``.
    Falls back to ``%2Fworkspace`` when the parent is not an encoded path.
    """
    parent = Path(session_dir).resolve().parent
    name = parent.name
    if name.startswith("%") or "%2F" in name or name == "workspace":
        return name
    return _DEFAULT_CWD_TOKEN


def can_resume_session(session_dir: Path) -> bool:
    """True when *session_dir* has artifacts Grok can resume from."""
    p = Path(session_dir)
    if not p.is_dir():
        return False
    for name in ("chat_history.jsonl", "summary.json", "events.jsonl"):
        if (p / name).is_file():
            return True
    return False


def seed_resume_into_traces_vol(traces_vol: Path, source_session_dir: Path) -> str:
    """Copy *source_session_dir* into *traces_vol* for a new container.

    Preserves the cwd-token / session-id layout so ``grok --resume <id>`` finds
    the session under ``/root/.grok/sessions``. Returns the session id.

    :param traces_vol: Host path bound to ``/root/.grok/sessions`` for the new run.
    :param source_session_dir: Ended session directory on the host.
    :returns: Session id string for ``RESUME_SESSION_ID``.
    :raises FileNotFoundError: When the source directory is missing.
    :raises ValueError: When the source has no resumeable artifacts.
    """
    source = Path(source_session_dir).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"resume source not found: {source}")
    if not can_resume_session(source):
        raise ValueError(f"session has no chat/events/summary to resume: {source}")

    sid = resume_session_id(source)
    if not sid:
        raise ValueError(f"empty session id for {source}")

    token = resume_cwd_token(source)
    dest = Path(traces_vol) / token / sid
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)

    # Drop lock files that could block the resumed process.
    for lock in dest.glob("*.lock"):
        try:
            lock.unlink()
        except OSError:
            logger.debug("could not remove lock %s", lock, exc_info=True)

    # Optional cwd-level history Grok sometimes writes beside the session dir.
    hist = source.parent / "prompt_history.jsonl"
    if hist.is_file():
        try:
            shutil.copy2(hist, dest.parent / "prompt_history.jsonl")
        except OSError:
            logger.debug("could not copy prompt_history.jsonl", exc_info=True)

    logger.info("Seeded resume session %s → %s", sid, dest)
    return sid
