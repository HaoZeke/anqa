"""Read existing ``run.json`` on a session (viewing only)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import JsonObject, as_json_object
from ..paths import is_run_dir_name

logger = logging.getLogger(__name__)

RUN_RECIPE_FILENAME = "run.json"


def _try_load(path: Path) -> JsonObject | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("Failed to read run recipe %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    return as_json_object(data)


def find_run_recipe_path(session_dir: Path) -> Path | None:
    """Locate ``run.json`` for a session (session dir, run volume, fork parent)."""
    sd = Path(session_dir)
    direct = sd / RUN_RECIPE_FILENAME
    if direct.is_file():
        return direct
    for anc in sd.parents:
        candidate = anc / RUN_RECIPE_FILENAME
        if candidate.is_file():
            return candidate
        if is_run_dir_name(anc.name) or anc.name == "traces":
            break
    try:
        from .resume import fork_parent_session_dir

        parent = fork_parent_session_dir(sd)
    except Exception:
        logger.debug("fork parent lookup failed for %s", sd, exc_info=True)
        parent = None
    if parent is not None:
        parent_recipe = parent / RUN_RECIPE_FILENAME
        if parent_recipe.is_file():
            return parent_recipe
        for anc in parent.parents:
            candidate = anc / RUN_RECIPE_FILENAME
            if candidate.is_file():
                return candidate
            if is_run_dir_name(anc.name) or anc.name == "traces":
                break
    return None


def load_run_recipe(session_dir: Path) -> JsonObject:
    """Load run recipe for *session_dir*, or ``{}`` when absent or invalid."""
    path = find_run_recipe_path(session_dir)
    if path is None:
        return {}
    data = _try_load(path)
    return data if data is not None else {}
