"""Shared session-export notify path for sessions home and browser."""

from __future__ import annotations

from pathlib import Path

from .i18n import t
from .threads import call_ui, resolve_ui_app


def export_session_with_notify(owner: object, session_dir: Path) -> None:
    """Build a session export tarball and notify the operator.

    Call from a worker thread (``@work``). Uses :func:`call_ui` for notifications.

    :param owner: Screen or app that owns ``notify`` (and ``app`` when a screen).
    :param session_dir: Grok session directory to export.
    """
    from ..session.export_bundle import export_session_bundle

    app = resolve_ui_app(owner)
    notify = getattr(owner, "notify", None)
    if not callable(notify):
        export_session_bundle(session_dir)
        return

    call_ui(app, notify, t("export-bundle-working"), severity="information")
    try:
        result = export_session_bundle(session_dir)
    except Exception as exc:
        call_ui(
            app,
            notify,
            t("export-bundle-failed", exc=str(exc)),
            severity="error",
            timeout=12,
        )
        return
    call_ui(
        app,
        notify,
        t("export-bundle-saved", path=str(result.path)),
        severity="information",
        timeout=12,
    )
