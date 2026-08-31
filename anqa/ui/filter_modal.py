"""Pick, save, and fill holes for saved search filters."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from ..filters import (
    FilterHole,
    SavedFilter,
    expand,
    filters_for_scope,
    holes,
    remove_filter,
    upsert_filter,
)
from .bindings import FORM_SAVE
from .forms import select_is_blank, select_null
from .i18n import t
from .quit_actions import QuitActions


class FilterPickModal(QuitActions, ModalScreen[str | None]):
    """Choose a saved filter name."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, rows: list[SavedFilter]) -> None:
        super().__init__()
        self._rows = list(rows)

    def compose(self) -> ComposeResult:
        options = [(f"{row.name}  ·  {row.query}", row.name) for row in self._rows]
        default: object = options[0][1] if options else select_null()
        with Vertical(id="modal-container"):
            yield Static("[bold]" + t("filter-pick-title") + "[/bold]")
            if not options:
                yield Static(t("filter-pick-empty"), classes="dim")
            else:
                yield Select(
                    options,
                    value=default,
                    id="filter-pick-select",
                    classes="field-select",
                    allow_blank=False,
                )
            with Horizontal(classes="modal-footer"):
                yield Button(t("ui-ok"), variant="primary", id="filter-pick-ok")
                yield Button(t("ui-cancel"), id="filter-pick-cancel")

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._commit()

    def _commit(self) -> None:
        if not self._rows:
            self.dismiss(None)
            return
        raw = self.query_one("#filter-pick-select", Select).value
        if select_is_blank(raw):
            self.dismiss(None)
            return
        self.dismiss(str(raw))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "filter-pick-ok":
            self._commit()
        elif event.button.id == "filter-pick-cancel":
            self.dismiss(None)


class FilterSaveModal(QuitActions, ModalScreen[str | None]):
    """Name the current search as a saved filter."""

    BINDINGS = list(FORM_SAVE)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static("[bold]" + t("filter-save-title") + "[/bold]")
            yield Input(placeholder=t("filter-save-name"), id="filter-save-input")
            with Horizontal(classes="modal-footer"):
                yield Button(t("ui-save"), variant="primary", id="filter-save-ok")
                yield Button(t("ui-cancel"), id="filter-save-cancel")

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self.action_commit()

    def action_commit(self) -> None:
        name = self.query_one("#filter-save-input", Input).value.strip()
        self.dismiss(name or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "filter-save-ok":
            self.action_commit()
        elif event.button.id == "filter-save-cancel":
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.action_commit()


class FilterHolesModal(QuitActions, ModalScreen[dict[str, str] | None]):
    """Collect answers for ``field:{a,b}`` and ``field:?`` holes."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, found: list[FilterHole]) -> None:
        super().__init__()
        self._holes = list(found)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Static("[bold]" + t("filter-holes-title") + "[/bold]")
            for hole in self._holes:
                yield Static(hole.field)
                hid = f"filter-hole-{hole.field}"
                if hole.kind == "choice":
                    opts = [(c, c) for c in hole.choices]
                    yield Select(
                        opts,
                        value=opts[0][1],
                        id=hid,
                        classes="field-select",
                        allow_blank=False,
                    )
                else:
                    yield Input(placeholder=hole.field, id=hid)
            with Horizontal(classes="modal-footer"):
                yield Button(t("ui-ok"), variant="primary", id="filter-holes-ok")
                yield Button(t("ui-cancel"), id="filter-holes-cancel")

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._commit()

    def _commit(self) -> None:
        answers: dict[str, str] = {}
        for hole in self._holes:
            hid = f"filter-hole-{hole.field}"
            if hole.kind == "choice":
                raw = self.query_one(f"#{hid}", Select).value
                if select_is_blank(raw):
                    self.dismiss(None)
                    return
                answers[hole.field] = str(raw)
            else:
                answers[hole.field] = self.query_one(f"#{hid}", Input).value.strip()
        if any(not v for v in answers.values()):
            self.dismiss(None)
            return
        self.dismiss(answers)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "filter-holes-ok":
            self._commit()
        elif event.button.id == "filter-holes-cancel":
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._commit()


def apply_named_filter(
    app: App[object],
    scope: str,
    apply_query: Callable[[str], None],
) -> None:
    """Pick a saved filter for *scope* and write the expanded query."""
    rows = filters_for_scope(scope)
    if not rows:
        app.notify(t("filter-none"), severity="warning")
        return

    def _picked(name: str | None) -> None:
        if not name:
            return
        row = next((item for item in rows if item.name == name), None)
        if row is None:
            return
        found = holes(row.query)
        if not found:
            apply_query(row.query)
            app.notify(t("filter-applied", name=row.name))
            return

        def _filled(answers: dict[str, str] | None) -> None:
            if not answers:
                return
            apply_query(expand(row.query, answers))
            app.notify(t("filter-applied", name=row.name))

        app.push_screen(FilterHolesModal(found), _filled)

    app.push_screen(FilterPickModal(rows), _picked)


def save_named_filter(app: App[object], scope: str, query: str) -> None:
    """Name the current search box and write it to ``filters.toml``."""
    text = (query or "").strip()
    if not text:
        app.notify(t("filter-empty-query"), severity="warning")
        return

    def _named(name: str | None) -> None:
        if not name:
            return
        try:
            upsert_filter(SavedFilter(name, scope, text))
        except ValueError:
            app.notify(t("filter-save-failed"), severity="error")
            return
        app.notify(t("filter-saved", name=name))

    app.push_screen(FilterSaveModal(), _named)


def delete_named_filter(app: App[object], scope: str) -> None:
    """Pick a saved filter for *scope* and remove it."""
    rows = filters_for_scope(scope)
    if not rows:
        app.notify(t("filter-none"), severity="warning")
        return

    def _picked(name: str | None) -> None:
        if not name:
            return
        if remove_filter(name, scope):
            app.notify(t("filter-deleted", name=name))

    app.push_screen(FilterPickModal(rows), _picked)
