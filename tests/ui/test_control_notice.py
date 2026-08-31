"""Operator copy for control-owner errors."""

from __future__ import annotations

from anqa.control.server import ControlError
from anqa.ui.control_notice import control_operator_text
from anqa.ui.i18n import t


def test_unknown_method_uses_restart_copy() -> None:
    text = control_operator_text(
        ControlError(-32601, "method not found"),
        fallback_id="notify-control-session-failed",
    )
    assert text == t("ui-control-owner-stale")
    assert "method not found" not in text
    assert "anqad restart" in text


def test_other_errors_keep_fallback() -> None:
    exc = ControlError(404, "session not found")
    text = control_operator_text(exc, fallback_id="notify-control-session-failed")
    assert "session not found" in text
    assert text != t("ui-control-owner-stale")
