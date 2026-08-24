#!/usr/bin/env python3
"""Write desktop/assets/textual-themes.json from the shared catalog."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from groket.ui.theme import community_themes, theme_family_pairs
from textual.theme import BUILTIN_THEMES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "desktop" / "assets" / "textual-themes.json"
PAIRS_OUT = ROOT / "desktop" / "assets" / "theme-pairs.json"


def _record(theme: object) -> dict[str, object] | None:
    if getattr(theme, "ansi", False):
        return None
    colors = theme.to_color_system().generate()
    hex_colors = {key: val for key, val in colors.items() if isinstance(val, str)}
    return {"dark": bool(theme.dark), "colors": hex_colors}


def theme_payload() -> dict[str, dict[str, object]]:
    """Map theme name → {dark, colors} using Textual ColorSystem hex tokens."""
    out: dict[str, dict[str, object]] = {}
    for name, theme in sorted(BUILTIN_THEMES.items()):
        rec = _record(theme)
        if rec is not None:
            out[name] = rec
    for theme in community_themes():
        rec = _record(theme)
        if rec is not None:
            out[theme.name] = rec
    return out


def pairs_payload() -> dict[str, list[str]]:
    """Family id → ``[light, dark]`` members (same table as ``THEME_FAMILIES``)."""
    return {name: [light, dark] for name, (light, dark) in sorted(theme_family_pairs().items())}


def emit(path: Path | None = None) -> Path:
    """Write pretty JSON (stable key order) to *path* or the committed HUD file."""
    dest = path or OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = theme_payload()
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def emit_pairs(path: Path | None = None) -> Path:
    """Write the shared light/dark pair table next to the theme map."""
    dest = path or PAIRS_OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(pairs_payload(), indent=2) + "\n", encoding="utf-8")
    return dest


def main() -> int:
    theme_dest = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    pairs_dest = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    dest = emit(theme_dest)
    pairs = emit_pairs(pairs_dest)
    print(
        f"wrote {dest} ({len(theme_payload())} themes) and {pairs}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
