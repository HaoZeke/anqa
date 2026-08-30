"""Store timestamps: epoch seconds, UTC ISO, and on-disk file stamps."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .models import JsonValue

_ISO_FACE = "%Y-%m-%dT%H:%M:%SZ"
_MS_FLOOR = 1e12


class Stamp:
    """Unix seconds, UTC ISO, and ``(mtime, size, 0, 0)`` for a path."""

    @staticmethod
    def epoch(raw: JsonValue) -> int | None:
        """Unix seconds. Milliseconds (``> 1e12``) and ISO strings are accepted."""
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, (int, float)):
            if raw <= 0:
                return None
            val = float(raw)
            return int(val / 1000.0) if val > _MS_FLOOR else int(val)
        if isinstance(raw, str) and raw.strip():
            text = raw.strip().strip('"').strip("'")
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp())
        return None

    @staticmethod
    def iso(raw: JsonValue) -> str:
        """UTC ``YYYY-MM-DDTHH:MM:SSZ``. Already-offset strings stay as given."""
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            if text.endswith("Z") or text.endswith("z") or "+" in text[10:]:
                return text.replace("z", "Z")
            sec = Stamp.epoch(text)
            if sec is None:
                return text
            return datetime.fromtimestamp(sec, tz=UTC).strftime(_ISO_FACE)
        sec = Stamp.epoch(raw)
        if sec is None:
            return ""
        try:
            return datetime.fromtimestamp(sec, tz=UTC).strftime(_ISO_FACE)
        except (OSError, OverflowError, ValueError):
            return ""

    @staticmethod
    def file(path: Path | str) -> tuple[float, int, int, int]:
        """``(mtime, size, 0, 0)`` for *path*, or zeros when the file is missing."""
        try:
            st = Path(path).expanduser().stat()
        except OSError:
            return (0.0, 0, 0, 0)
        return (float(st.st_mtime), int(st.st_size), 0, 0)
