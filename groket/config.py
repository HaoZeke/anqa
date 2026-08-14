"""App-global ``~/.groket/config.json``.

One file, one shape. Sections are objects (``analysis``, ``hud``, ``export``).
Top-level keys are the prefs both the terminal app and the desktop HUD share.
Load folds leftover flat keys (``hud_global_shortcut``) into ``hud`` and
writes the canonical object the next time anything saves.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import paths
from .models import JsonObject, as_json_object

logger = logging.getLogger(__name__)

_AUTO_ANALYZE_WHEN = frozenset({"session_complete", "never"})

_CACHE: AppConfig | None = None
_CACHE_PATH: Path | None = None


class AnalysisPrefs(BaseModel):
    """``analysis`` section — plugins and worker pools."""

    model_config = ConfigDict(extra="ignore")

    plugins: list[str] = Field(default_factory=list)
    auto_analyze_when: str = "session_complete"
    analysis_workers: int = 1
    live_refresh_workers: int = 1

    @field_validator("plugins", mode="before")
    @classmethod
    def _plugins(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(p).strip() for p in value if isinstance(p, str) and p.strip()]

    @field_validator("auto_analyze_when", mode="before")
    @classmethod
    def _when(cls, value: object) -> str:
        raw = str(value or "session_complete").strip().lower()
        return raw if raw in _AUTO_ANALYZE_WHEN else "session_complete"

    @field_validator("analysis_workers", "live_refresh_workers", mode="before")
    @classmethod
    def _workers(cls, value: object) -> int:
        if isinstance(value, bool):
            return 1
        if isinstance(value, int):
            return max(1, value)
        if isinstance(value, str):
            try:
                return max(1, int(value.strip()))
            except ValueError:
                return 1
        return 1


class HudPrefs(BaseModel):
    """``hud`` section — desktop palette only."""

    model_config = ConfigDict(extra="ignore")

    window_mode: bool = False
    global_shortcut: str = ""
    desktop_notifications: bool = True

    @field_validator("global_shortcut", mode="before")
    @classmethod
    def _shortcut(cls, value: object) -> str:
        return str(value).strip() if isinstance(value, str) else ""


class ExportPrefs(BaseModel):
    """``export`` section — default session-export profile id."""

    model_config = ConfigDict(extra="ignore")

    default_profile: str = ""

    @field_validator("default_profile", mode="before")
    @classmethod
    def _profile(cls, value: object) -> str:
        return str(value).strip() if isinstance(value, str) else ""


class AppConfig(BaseModel):
    """Canonical ``config.json`` body."""

    model_config = ConfigDict(extra="ignore")

    theme: str = "groket"
    follow_os: bool = False
    show_host_sessions: bool = False
    show_tips: bool = True
    auto_serve: bool = True
    analysis: AnalysisPrefs = Field(default_factory=AnalysisPrefs)
    hud: HudPrefs = Field(default_factory=HudPrefs)
    export: ExportPrefs = Field(default_factory=ExportPrefs)

    @field_validator("theme", mode="before")
    @classmethod
    def _theme(cls, value: object) -> str:
        raw = str(value).strip() if isinstance(value, str) else ""
        return raw or "groket"


def _read_raw(path: Path) -> JsonObject:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        logger.debug("config: could not read %s", path, exc_info=True)
        return {}
    return as_json_object(data) if isinstance(data, dict) else {}


def _hud_from_raw(raw: JsonObject) -> JsonObject:
    section = raw.get("hud")
    hud: JsonObject = as_json_object(section) if isinstance(section, dict) else {}
    if not str(hud.get("global_shortcut") or "").strip():
        flat = raw.get("hud_global_shortcut")
        if isinstance(flat, str) and flat.strip():
            hud = {**hud, "global_shortcut": flat.strip()}
    return hud


def parse_app_config(raw: JsonObject) -> AppConfig:
    """Build :class:`AppConfig` from a JSON object, folding leftover flat keys.

    :param raw: Decoded ``config.json`` object (may be empty).
    :returns: Canonical config (unknown keys dropped).
    """
    payload: JsonObject = {
        "analysis": raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {},
        "hud": _hud_from_raw(raw),
        "export": raw.get("export") if isinstance(raw.get("export"), dict) else {},
    }
    for key in ("theme", "follow_os", "show_host_sessions", "show_tips", "auto_serve"):
        if key in raw:
            payload[key] = raw[key]
    return AppConfig.model_validate(payload)


def config_dump(cfg: AppConfig) -> JsonObject:
    """Canonical JSON object for ``config.json``."""
    return as_json_object(cfg.model_dump(mode="json"))


def invalidate_config_cache() -> None:
    """Drop the in-process config cache."""
    global _CACHE, _CACHE_PATH
    _CACHE = None
    _CACHE_PATH = None


def load_app_config(path: Path | None = None) -> AppConfig:
    """Load ``~/.groket/config.json`` (or *path*). Missing or invalid → defaults.

    :param path: Explicit file; default :func:`app_config_path`.
    :returns: Canonical config.
    """
    global _CACHE, _CACHE_PATH
    fp = Path(path).expanduser() if path is not None else paths.app_config_path()
    if _CACHE is not None and _CACHE_PATH == fp:
        return _CACHE
    cfg = parse_app_config(_read_raw(fp))
    _CACHE = cfg
    _CACHE_PATH = fp
    return cfg


def save_app_config(cfg: AppConfig, path: Path | None = None) -> None:
    """Write the canonical object (no leftover flat keys).

    :param cfg: Config to persist.
    :param path: Explicit file; default :func:`app_config_path`.
    """
    global _CACHE, _CACHE_PATH
    fp = Path(path).expanduser() if path is not None else paths.app_config_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(config_dump(cfg), indent=2) + "\n"
    fp.write_text(text, encoding="utf-8")
    _CACHE = cfg
    _CACHE_PATH = fp


def update_app_config(path: Path | None = None, **changes: object) -> AppConfig:
    """Load, apply top-level field *changes*, save, return the new config.

    Nested sections pass a full replacement object (``analysis=…``, ``hud=…``).

    :param path: Explicit file; default :func:`app_config_path`.
    :param changes: Field names on :class:`AppConfig`.
    :returns: Saved config.
    """
    cfg = load_app_config(path)
    dumped = cfg.model_dump()
    for key, value in changes.items():
        dumped[key] = value.model_dump() if isinstance(value, BaseModel) else value
    next_cfg = AppConfig.model_validate(dumped)
    save_app_config(next_cfg, path)
    return next_cfg
