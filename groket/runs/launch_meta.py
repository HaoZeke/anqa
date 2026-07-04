"""Per-container launch record written into the traces volume at start.

``groket-launch.json`` stores the operator-selected model id and reasoning
effort so session list / summary read the real launch values instead of
inferring them from directory names or Grok ``summary.json``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import is_run_dir_name
from .batch import REASONING_EFFORTS, join_model_effort, split_model_effort

if TYPE_CHECKING:
    from ..docker.orchestrator import ContainerConfig
    from ..models import SessionMeta

logger = logging.getLogger(__name__)

LAUNCH_META_FILENAME = "groket-launch.json"
LAUNCH_META_VERSION = 1


@dataclass(frozen=True)
class LaunchMeta:
    """Authoritative launch fields for one eval container."""

    model: str
    reasoning_effort: str = ""
    model_token: str = ""
    container_name: str = ""
    run_id: str = ""
    task_id: str = ""
    created_at: str = ""

    @property
    def display_token(self) -> str:
        """``model:effort`` when effort is set, else bare model id."""
        token = (self.model_token or "").strip()
        if token:
            return token
        return join_model_effort(self.model, self.reasoning_effort)


def build_launch_meta(
    *,
    model: str,
    reasoning_effort: str = "",
    container_name: str = "",
    run_id: str = "",
    task_id: str = "",
) -> LaunchMeta:
    """Normalize launch fields from a container config or explicit values."""
    mid, eff_tok = split_model_effort(model)
    model_id = (mid or model or "").strip()
    effort = (reasoning_effort or eff_tok or "").strip().lower()
    if effort not in REASONING_EFFORTS:
        effort = ""
    token = join_model_effort(model_id, effort)
    return LaunchMeta(
        model=model_id or (model or "").strip(),
        reasoning_effort=effort,
        model_token=token,
        container_name=(container_name or "").strip(),
        run_id=(run_id or "").strip(),
        task_id=(task_id or "").strip(),
        created_at=datetime.now(UTC).isoformat(),
    )


def launch_meta_from_config(config: ContainerConfig, *, task_id: str = "") -> LaunchMeta:
    """Build :class:`LaunchMeta` from a :class:`~groket.docker.orchestrator.ContainerConfig`."""
    return build_launch_meta(
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        container_name=config.container_name,
        run_id=config.run_id,
        task_id=task_id,
    )


def write_launch_meta(traces_vol: Path, meta: LaunchMeta) -> Path:
    """Write ``groket-launch.json`` under *traces_vol* (container sessions mount)."""
    traces_vol.mkdir(parents=True, exist_ok=True)
    path = traces_vol / LAUNCH_META_FILENAME
    payload = {
        "version": LAUNCH_META_VERSION,
        "model": meta.model,
        "reasoning_effort": meta.reasoning_effort,
        "model_token": meta.model_token or meta.display_token,
        "container_name": meta.container_name,
        "run_id": meta.run_id,
        "task_id": meta.task_id,
        "created_at": meta.created_at,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def write_launch_meta_for_config(
    traces_vol: Path, config: ContainerConfig, *, task_id: str = ""
) -> Path:
    """Serialize *config* launch fields into *traces_vol*."""
    return write_launch_meta(traces_vol, launch_meta_from_config(config, task_id=task_id))


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
        container_name=str(data.get("container_name") or "").strip(),
        run_id=str(data.get("run_id") or "").strip(),
        task_id=str(data.get("task_id") or "").strip(),
        created_at=str(data.get("created_at") or "").strip(),
    )


def find_launch_meta_file(session_dir: Path) -> Path | None:
    """Locate ``groket-launch.json`` on the session dir or its traces volume."""
    for anc in [session_dir, *session_dir.parents]:
        candidate = anc / LAUNCH_META_FILENAME
        if candidate.is_file():
            return candidate
        if is_run_dir_name(anc.name) or anc.name == "traces":
            break
    return None


def read_launch_meta(session_dir: Path) -> LaunchMeta | None:
    """Load launch meta for *session_dir*, or ``None`` when absent/invalid."""
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
    """Copy authoritative launch fields onto *meta* (overrides summary guesses)."""
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
