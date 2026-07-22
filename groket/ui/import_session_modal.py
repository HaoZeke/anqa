"""Modal: import a native ~/.grok session into the work traces tree."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from .forms import select_value_str
from .i18n import t
from .quit_actions import QuitActions

_NONE = "__none__"


class ImportSessionModal(QuitActions, ModalScreen[tuple[str, bool] | None]):
    """Pick a host session path (recent list or free text). Result: (path, link)."""

    BINDINGS = [
        Binding("escape", "cancel", t("ui-cancel"), show=True),
        Binding("ctrl+s", "submit", t("ui-save"), show=True),
        Binding("enter", "submit", t("ui-save"), show=False),
    ]

    def __init__(
        self,
        *,
        recent: list[tuple[str, str]],
        initial_path: str = "",
    ) -> None:
        """
        :param recent: ``(label, path_str)`` options for a Select (newest first).
        :param initial_path: Prefill path input.
        """
        super().__init__()
        self._recent = recent
        self._initial = initial_path

    def compose(self) -> ComposeResult:
        opts: list[tuple[str, str]] = [(t("ui-none-run-defaults-only"), _NONE)]
        opts.extend(self._recent)
        with Vertical(id="import-session-modal"):
            yield Static(t("import-session-title"), id="import-session-title")
            yield Static(t("import-session-hint"), id="import-session-hint")
            yield Label(t("import-session-recent-label"))
            yield Select(opts, value=_NONE, id="import-session-recent", allow_blank=False)
            yield Input(
                value=self._initial,
                placeholder=t("import-session-placeholder"),
                id="import-session-path",
            )
            yield Checkbox(t("import-session-link-label"), value=False, id="import-session-link")
            with Horizontal(id="import-session-actions", classes="modal-footer"):
                yield Button(t("ui-save"), variant="primary", id="import-session-ok")
                yield Button(t("ui-cancel"), id="import-session-cancel")

    def on_mount(self) -> None:
        self.query_one("#import-session-path", Input).focus()

    @on(Select.Changed, "#import-session-recent")
    def _recent_changed(self, event: Select.Changed) -> None:
        val = select_value_str(event.value, default=_NONE)
        if val and val != _NONE:
            self.query_one("#import-session-path", Input).value = val

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

    def _ok(self) -> None:
        path = self.query_one("#import-session-path", Input).value.strip()
        if not path:
            self.notify(t("import-session-no-path"), severity="error")
            return
        link = bool(self.query_one("#import-session-link", Checkbox).value)
        self.dismiss((path, link))
