"""Desktop HUD: launches the Tauri session palette against the control plane."""

from __future__ import annotations

from .app import run_hud
from .launch import find_hud_binary

__all__ = ["find_hud_binary", "run_hud"]
