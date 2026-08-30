"""Read object rows from a json-lines file."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .models import JsonObject, as_json_object


def json_lines(path: Path | str) -> Iterator[JsonObject]:
    """Yield object rows. Missing files and junk lines are skipped."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            yield as_json_object(val)
