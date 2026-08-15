"""Operator-facing control-owner errors (TUI toasts)."""

from __future__ import annotations

from groket.integrations.control import is_unknown_method

from .i18n import t


def control_operator_text(exc: BaseException, *, fallback_id: str) -> str:
    """Friendly toast copy; callers log the raw exception separately.

    Unknown-method (-32601) means this process is talking to an older
    ``groket serve``. Other failures keep *fallback_id* with a short ``err``.
    """
    if is_unknown_method(exc):
        return t("ui-control-owner-stale")
    return t(fallback_id, err=str(exc)[:180])
