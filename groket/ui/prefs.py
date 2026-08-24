"""App-global UI preferences (``~/.groket/config.toml``).

Thin accessors over :mod:`groket.config`. Screens and ``panel_render``
read these — do not scatter ad-hoc config.toml I/O.
"""

from __future__ import annotations

import logging

from ..config import invalidate_config_cache, load_app_config, update_app_config
from ..paths import app_config_path
from .i18n import t

logger = logging.getLogger(__name__)


def invalidate_prefs_cache() -> None:
    """Drop the shared app-config cache."""
    invalidate_config_cache()


def auto_serve_enabled() -> bool:
    """Whether the TUI should detach-start a control owner when the socket is free."""
    return load_app_config().auto_serve


def set_auto_serve(enabled: bool) -> None:
    try:
        update_app_config(auto_serve=bool(enabled))
    except OSError:
        logger.warning(t("ui-failed-to-write-prefs-to-s"), app_config_path(), exc_info=True)


def hud_global_shortcut() -> str:
    """HUD summon chord from ``hud.global_shortcut`` (empty = binary default).

    Format is ``+``-separated modifiers and one key (see HUD README).
    Env ``GROKET_HUD_SHORTCUT`` wins when the launcher sets it from this value.
    """
    return load_app_config().hud.global_shortcut
