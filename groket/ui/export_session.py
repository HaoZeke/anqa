"""Shared session-export notify path for sessions home and browser."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.screen import Screen

from .i18n import t
from .threads import call_ui, resolve_ui_app


def export_session_with_notify(
    owner: object,
    session_dir: Path,
    *,
    profile: str | None = None,
) -> None:
    """Build a session export and notify the operator.

    Call from a worker thread (``@work``). Uses :func:`call_ui` for notifications.

    :param owner: Screen or app that owns ``notify`` (and ``app`` when a screen).
    :param session_dir: Grok session directory to export.
    :param profile: Export profile id; default profile when omitted.
    """
    from ..session.export_bundle import export_session_bundle

    app = resolve_ui_app(owner)
    notify = getattr(owner, "notify", None)
    if not callable(notify):
        export_session_bundle(session_dir, profile=profile)
        return

    call_ui(app, notify, t("export-bundle-working"), severity="information")
    try:
        result = export_session_bundle(session_dir, profile=profile)
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
        t(
            "export-bundle-saved",
            path=str(result.path),
            profile=result.profile_id,
        ),
        severity="information",
        timeout=12,
    )


def start_export_with_profile_picker(owner: Screen | App, session_dir: Path) -> None:
    """Push the profile picker; on confirm, export *session_dir* on a worker."""
    from .export_profile_modal import ExportProfileModal

    app = owner if isinstance(owner, App) else resolve_ui_app(owner)
    if app is None:
        return

    def _done(profile_id: str | None) -> None:
        if not profile_id:
            return

        def _run() -> None:
            export_session_with_notify(owner, session_dir, profile=profile_id)

        # Prefer the same thread pattern as other exports.
        worker = getattr(app, "run_worker", None)
        if callable(worker):
            worker(_run, thread=True, exclusive=False, name="export-session-profile")
        else:
            _run()

    app.push_screen(ExportProfileModal(), _done)
