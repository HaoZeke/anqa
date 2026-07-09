"""Seed an ended Grok session into a new eval traces volume for ``--resume``."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Eval containers use cwd=/workspace → sessions under this token.
_DEFAULT_CWD_TOKEN = "%2Fworkspace"
# Staging tree under the container traces volume. Operator session listing
# never treats paths under this name as eval rows (same class of convention as
# ``*.stage`` / turn-gate dirs). Grok still sees the parent at the live
# ``<cwd-token>/<session_id>`` path via a symlink into this tree.
RESUME_SEED_DIRNAME = ".groket-resume-seed"


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


def is_resume_seed_path(path: Path) -> bool:
    """True when *path* is resume history substrate, not an operator eval session.

    Substrate lives under :data:`RESUME_SEED_DIRNAME`, or is a live path that
    resolves into that tree (symlink placed for ``grok --resume``).
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


def seed_resume_into_traces_vol(traces_vol: Path, source_session_dir: Path) -> str:
    """Copy *source_session_dir* into *traces_vol* for a new container.

    Layout (filesystem contract):

    * **Substrate** (canonical copy):
      ``<traces_vol>/.groket-resume-seed/<cwd-token>/<session_id>/``
    * **Live path for Grok** (symlink into substrate):
      ``<traces_vol>/<cwd-token>/<session_id>`` → substrate

    :func:`~groket.parser.find_sessions` does not list substrate (or live
    paths that resolve into it). The forked child session is a normal directory
    under ``<cwd-token>/`` and is listed as usual.

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
    traces = Path(traces_vol)
    seed_dest = traces / RESUME_SEED_DIRNAME / token / sid
    live_dest = traces / token / sid

    seed_dest.parent.mkdir(parents=True, exist_ok=True)
    if seed_dest.exists():
        shutil.rmtree(seed_dest)
    shutil.copytree(source, seed_dest)

    for lock in seed_dest.glob("*.lock"):
        try:
            lock.unlink()
        except OSError:
            logger.debug("could not remove lock %s", lock, exc_info=True)

    hist = source.parent / "prompt_history.jsonl"
    if hist.is_file():
        try:
            shutil.copy2(hist, seed_dest.parent / "prompt_history.jsonl")
        except OSError:
            logger.debug("could not copy prompt_history.jsonl", exc_info=True)

    # Live path Grok expects: symlink into substrate (not a second full copy).
    live_dest.parent.mkdir(parents=True, exist_ok=True)
    if live_dest.is_symlink() or live_dest.exists():
        if live_dest.is_dir() and not live_dest.is_symlink():
            shutil.rmtree(live_dest)
        else:
            live_dest.unlink()
    rel = Path(os.path.relpath(seed_dest, start=live_dest.parent))
    live_dest.symlink_to(rel, target_is_directory=True)

    logger.info("Seeded resume session %s → %s (live link %s)", sid, seed_dest, live_dest)
    return sid
