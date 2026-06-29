"""User flags (annotations) on sessions — core concern, not analysis."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import Flag

logger = logging.getLogger(__name__)


def load_flags(session_dir: Path) -> list[Flag]:
    """Load user flags from a session directory or fallback location."""
    candidates = [
        session_dir / "flags.json",
        Path.home() / "groket" / "flags" / session_dir.name / "flags.json",
    ]
    for flags_file in candidates:
        if not flags_file.exists():
            continue
        try:
            with open(flags_file) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return [Flag.model_validate({**v, "event_index": int(k)}) for k, v in data.items()]
            elif isinstance(data, list):
                return [Flag.model_validate(d) for d in data]
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return []


def save_flags(session_dir: Path, flags: list[Flag]) -> None:
    """Save user flags. Falls back to work_dir if session_dir is read-only."""
    flags_file = session_dir / "flags.json"
    try:
        with open(flags_file, "w") as f:
            json.dump([fl.model_dump() for fl in flags], f, indent=2)
    except PermissionError:
        # Fall back to a writable location
        fallback_dir = Path.home() / "groket" / "flags" / session_dir.name
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback_file = fallback_dir / "flags.json"
        with open(fallback_file, "w") as f:
            json.dump([fl.model_dump() for fl in flags], f, indent=2)
