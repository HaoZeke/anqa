"""Modal: fuzzy-pick a native ~/.grok session and import it into the work tree."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Static

from ..session.import_session import HostSessionRow, list_host_grok_sessions, match_host_session
from .data_table import cursor_row_key, preserving_cursor, style_data_table
from .i18n import t
from .quit_actions import QuitActions
from .threads import call_ui

_TITLE_COL = 48
_CWD_COL = 36
_ID_COL = 12


def _short_when(mtime: float) -> str:
    if mtime <= 0:
        return t("import-session-when-unknown")
    try:
        return datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return t("import-session-when-unknown")


def _clip(text: str, n: int) -> str:
    s = (text or "").replace("\n", " ").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


class ImportSessionModal(QuitActions, ModalScreen[tuple[str, bool] | None]):
    """Filterable list of host sessions. Result: ``(path, link)`` or None."""

    BINDINGS = [
        Binding("escape", "cancel", t("ui-cancel"), show=True),
        Binding("ctrl+s", "submit", t("ui-save"), show=True),
        # Enter on the table imports; leave Enter free in the search Input.
        Binding("enter", "submit", t("ui-save"), show=False, priority=False),
    ]

    def __init__(self, *, sessions_root: Path | None = None) -> None:
        super().__init__()
        self._sessions_root = sessions_root
        self._rows: list[HostSessionRow] = []
        self._query_text = ""
        self._loaded = False

    def compose(self) -> ComposeResult:
        with Vertical(id="import-session-modal"):
            yield Static(t("import-session-title"), id="import-session-title")
            yield Static(t("import-session-hint"), id="import-session-hint")
            yield Input(
                placeholder=t("import-session-search-placeholder"),
                id="import-session-search",
            )
            yield Static(t("import-session-loading"), id="import-session-status")
            yield DataTable(id="import-session-table", cursor_type="row")
            yield Checkbox(
                t("import-session-link-label"),
                value=False,
                id="import-session-link",
            )
            with Horizontal(id="import-session-actions", classes="modal-footer"):
                yield Button(t("import-session-import"), variant="primary", id="import-session-ok")
                yield Button(t("ui-cancel"), id="import-session-cancel")

    def on_mount(self) -> None:
        table = self.query_one("#import-session-table", DataTable)
        style_data_table(table)
        table.add_columns(
            t("import-session-col-title"),
            t("import-session-col-cwd"),
            t("import-session-col-when"),
            t("import-session-col-id"),
        )
        with suppress(Exception):
            self.query_one("#import-session-search", Input).focus()
        self._load_rows()

    @work(thread=True, exclusive=True, group="import-session-list")
    def _load_rows(self) -> None:
        try:
            rows = list_host_grok_sessions(self._sessions_root, limit=0)
        except Exception as exc:
            call_ui(self.app, self._on_load_failed, str(exc))
            return
        call_ui(self.app, self._on_rows_loaded, rows)

    def _on_load_failed(self, msg: str) -> None:
        self._loaded = True
        self._rows = []
        with suppress(Exception):
            self.query_one("#import-session-status", Static).update(
                t("import-session-load-failed", msg=msg)
            )
        self._apply_filter()

    def _on_rows_loaded(self, rows: list[HostSessionRow]) -> None:
        self._loaded = True
        self._rows = list(rows)
        # Keep any query typed while the worker ran.
        with suppress(Exception):
            self._query_text = (self.query_one("#import-session-search", Input).value or "").strip()
        self._apply_filter()

    def _filtered_rows(self) -> list[tuple[float, HostSessionRow]]:
        q = self._query_text
        if not q:
            return [(float(r.mtime), r) for r in self._rows]
        scored: list[tuple[float, HostSessionRow]] = []
        for row in self._rows:
            score = match_host_session(q, row)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].mtime))
        return scored

    def _apply_filter(self) -> None:
        table = self.query_one("#import-session-table", DataTable)
        filtered = self._filtered_rows() if self._loaded else []
        with preserving_cursor(table):
            table.clear()
            for _score, row in filtered:
                title = _clip(row.title or row.session_id, _TITLE_COL)
                cwd = _clip(row.cwd_label, _CWD_COL)
                sid = _clip(row.session_id, _ID_COL)
                table.add_row(
                    title,
                    cwd,
                    _short_when(row.mtime),
                    sid,
                    key=str(row.path),
                )
        if not self._loaded:
            status = t("import-session-loading")
        elif not self._rows:
            status = t("import-session-empty")
        elif not filtered:
            status = t("import-session-no-match")
        else:
            status = t(
                "import-session-status-count",
                shown=len(filtered),
                total=len(self._rows),
            )
        with suppress(Exception):
            self.query_one("#import-session-status", Static).update(status)

    @on(Input.Changed, "#import-session-search")
    def _search_changed(self, event: Input.Changed) -> None:
        self._query_text = (event.value or "").strip()
        self._apply_filter()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self._ok()

    @on(Button.Pressed, "#import-session-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#import-session-ok")
    def _ok_btn(self) -> None:
        self._ok()

    @on(DataTable.RowSelected, "#import-session-table")
    def _row_selected(self, _event: DataTable.RowSelected) -> None:
        self._ok()

    def _selected_path(self) -> str | None:
        table = self.query_one("#import-session-table", DataTable)
        key = cursor_row_key(table)
        return key if key else None

    def _ok(self) -> None:
        path = self._selected_path()
        if not path:
            self.notify(t("import-session-no-path"), severity="error")
            return
        link = bool(self.query_one("#import-session-link", Checkbox).value)
        self.dismiss((path, link))
