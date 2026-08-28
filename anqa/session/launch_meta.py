"""Read existing ``anqa-launch.json`` on a session (viewing only)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..models import SessionMeta
from ..paths import is_run_dir_name
from .models_catalog import REASONING_EFFORTS, join_model_effort, split_model_effort

logger = logging.getLogger(__name__)

LAUNCH_META_FILENAME = "anqa-launch.json"


@dataclass(frozen=True)
class LaunchMeta:
    """Launch fields recorded on an existing traces volume."""

    model: str
    reasoning_effort: str = ""
    model_token: str = ""
    run_id: str = ""
    task_id: str = ""
    resume_parent_session_id: str = ""
    resume_fork_session_id: str = ""

    @property
    def is_fork_resume(self) -> bool:
        """True when this record seeds a parent and forks a new session id."""
        return bool(
            (self.resume_parent_session_id or "").strip()
            and (self.resume_fork_session_id or "").strip()
        )


def find_launch_meta_file(session_dir: Path) -> Path | None:
    """Locate ``anqa-launch.json`` on the session dir or its traces volume."""
    for anc in [session_dir, *session_dir.parents]:
        candidate = anc / LAUNCH_META_FILENAME
        if candidate.is_file():
            return candidate
        if is_run_dir_name(anc.name) or anc.name == "traces":
            break
    return None


def _parse_launch_payload(data: object) -> LaunchMeta | None:
    if not isinstance(data, dict):
        return None
    model = str(data.get("model") or "").strip()
    token = str(data.get("model_token") or "").strip()
    effort = str(data.get("reasoning_effort") or "").strip().lower()
    if token and not model:
        model, eff_tok = split_model_effort(token)
        if not effort and eff_tok in REASONING_EFFORTS:
            effort = eff_tok
    if not model and token:
        model = split_model_effort(token)[0] or token
    if not model:
        return None
    if effort not in REASONING_EFFORTS:
        _mid, eff_tok = split_model_effort(token or model)
        effort = eff_tok if eff_tok in REASONING_EFFORTS else ""
    if not token:
        token = join_model_effort(model, effort)
    return LaunchMeta(
        model=model,
        reasoning_effort=effort,
        model_token=token,
        run_id=str(data.get("run_id") or "").strip(),
        task_id=str(data.get("task_id") or "").strip(),
        resume_parent_session_id=str(data.get("resume_parent_session_id") or "").strip(),
        resume_fork_session_id=str(data.get("resume_fork_session_id") or "").strip(),
    )


def read_launch_meta(session_dir: Path) -> LaunchMeta | None:
    """Load launch meta for *session_dir*, or ``None`` when absent or invalid."""
    path = find_launch_meta_file(session_dir)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("Failed to read launch meta %s", path, exc_info=True)
        return None
    return _parse_launch_payload(data)


def apply_launch_meta(meta: SessionMeta, launch: LaunchMeta) -> None:
    """Copy recorded launch fields onto *meta* (overrides summary guesses)."""
    if launch.model:
        meta.model_id = launch.model
    if launch.reasoning_effort:
        meta.reasoning_effort = launch.reasoning_effort
    elif launch.model_token:
        _mid, eff = split_model_effort(launch.model_token)
        if eff:
            meta.reasoning_effort = eff
    if launch.run_id and not meta.run_id:
        meta.run_id = launch.run_id
    if launch.task_id and not meta.task_id:
        meta.task_id = launch.task_id
