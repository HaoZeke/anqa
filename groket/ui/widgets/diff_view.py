"""Diff pane: rewind-point picker, file list, one extractable body."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Select, Static

from ...constants import DIFF_TRUNCATE_THRESHOLD
from ...session.workspace_diff import DiffHunk, DiffPoint, WorkspaceDiff
from ..data_table import preserving_cursor, restore_cursor, style_data_table
from ..fuzzy import filter_diff_hunks, mark_unified_hit
from ..i18n import t
from ..panel_render import dim_rule, kv_line, panel_group, section_header
from ..render_detail import set_static_renderable
from ..selectable_static import SelectableStatic
from .controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS


def _point_label(point: DiffPoint, index: int) -> str:
    if point.source == "search_replace":
        return t("diff-point-edits")
    if point.prompt_index is not None:
        return t("diff-point-prompt", n=point.prompt_index)
    return t("diff-point-rewind", n=index + 1)


def _clip_context(text: str, limit: int = 400) -> str:
    one = " ".join((text or "").split())
    if len(one) <= limit:
        return one
    return one[: limit - 1] + "…"


def _source_label(source: str | None) -> str:
    if source == "rewind_points":
        return t("diff-source-rewind")
    if source == "search_replace":
        return t("diff-source-edits")
    return t("diff-source-none")


class DiffView(Vertical):
    """Rewind snapshots or approximate ``search_replace`` edits, one file at a time."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._doc = WorkspaceDiff(())
        self._point_key: str | None = None
        self._file_key: str | None = None
        self._query: str = ""
        self._hit_line: int | None = None
        self._syncing = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="diff-filter-bar", classes=FILTER_BAR_CLASS):
            yield Static(t("diff-filter"), id="diff-filter-label", classes=FILTER_LABEL_CLASS)
            yield Select(
                [(t("diff-point-edits"), "edits")],
                value="edits",
                id="diff-point-select",
                allow_blank=False,
                classes="field-select",
            )
            yield Input(placeholder=t("diff-search-placeholder"), id="diff-search")
        with Horizontal(id="diff-layout"):
            with Vertical(id="diff-files"):
                yield DataTable(id="diff-file-list")
            with Vertical(id="diff-body-column"):
                with VerticalScroll(id="diff-scroll"):
                    yield SelectableStatic(id="diff-content")

    def on_mount(self) -> None:
        table = self.query_one("#diff-file-list", DataTable)
        style_data_table(table)
        table.add_columns(t("col-file"), t("col-added"), t("col-removed"))
        self._paint()

    def set_doc(self, doc: WorkspaceDiff) -> None:
        """Replace the loaded snapshots and keep the current file when it still exists."""
        self._doc = doc
        if self.is_mounted:
            self._paint()

    def selected_plain(self) -> str:
        """Unified diff of the highlighted file, or empty."""
        hunk = self._hunk()
        return hunk.unified if hunk is not None else ""

    def hit_line(self) -> int | None:
        """0-based unified line for the last body search hit, if any."""
        return self._hit_line

    def can_step_point(self) -> bool:
        """True when h/l should step rewind snapshots."""
        return len(self._doc.points) > 1

    def focus_search(self) -> None:
        """Put the keyboard in the Diff search field."""
        try:
            self.query_one("#diff-search", Input).focus()
        except Exception:
            return

    def step_point(self, delta: int) -> None:
        """Move to the next or previous rewind snapshot."""
        keys = [p.key for p in self._doc.points]
        if len(keys) < 2 or delta == 0:
            return
        cur = self._point_key if self._point_key in keys else keys[-1]
        i = keys.index(cur)
        nxt = max(0, min(len(keys) - 1, i + delta))
        if keys[nxt] == cur:
            return
        self._point_key = keys[nxt]
        self._file_key = None
        self._hit_line = None
        if self.is_mounted:
            self._paint()

    def _current_point(self) -> DiffPoint | None:
        key = self._point_key
        if key is not None:
            found = self._doc.point(key)
            if found is not None:
                return found
        last = self._doc.last()
        if last is not None:
            self._point_key = last.key
        return last

    def _paint(self) -> None:
        self._syncing = True
        try:
            self._sync_point_select()
            self._fill_files()
            self._paint_body()
        finally:
            self._syncing = False

    def _sync_point_select(self) -> None:
        sel = self.query_one("#diff-point-select", Select)
        points = self._doc.points
        sel.display = len(points) > 1
        if not points:
            sel.set_options([(t("diff-empty-session"), "empty")])
            sel.value = "empty"
            return
        options = [(_point_label(p, i), p.key) for i, p in enumerate(points)]
        sel.set_options(options)
        point = self._current_point()
        if point is not None:
            sel.value = point.key

    def _visible_files(self) -> tuple[DiffHunk, ...]:
        point = self._current_point()
        if point is None:
            return ()
        files = point.files
        q = self._query.strip()
        if not q:
            self._hit_line = None
            return files
        pairs = [(h.path, h.unified) for h in files]
        hits = filter_diff_hunks(q, pairs)
        by_path = {h.path: h for h in files}
        ordered: list[DiffHunk] = []
        hit_line: int | None = None
        for path, _score, line, _where in hits:
            hunk = by_path.get(path)
            if hunk is None:
                continue
            ordered.append(hunk)
            if path == (self._file_key or path) and hit_line is None:
                hit_line = line
        if ordered and (self._file_key is None or self._file_key not in {h.path for h in ordered}):
            hit_line = hits[0][2]
        self._hit_line = hit_line
        return tuple(ordered)

    def _fill_files(self) -> None:
        table = self.query_one("#diff-file-list", DataTable)
        files = self._visible_files()
        with preserving_cursor(table):
            table.clear()
            for hunk in files:
                table.add_row(hunk.path, f"+{hunk.added}", f"-{hunk.removed}", key=hunk.path)
        if files:
            keep = self._file_key if any(h.path == self._file_key for h in files) else files[0].path
            self._file_key = keep
            restore_cursor(table, keep, scroll=True)
        else:
            self._file_key = None

    def _hunk(self) -> DiffHunk | None:
        point = self._current_point()
        if point is None or self._file_key is None:
            return None
        for hunk in point.files:
            if hunk.path == self._file_key:
                return hunk
        return None

    def _paint_body(self) -> None:
        widget = self.query_one("#diff-content", SelectableStatic)
        point = self._current_point()
        hunk = self._hunk()
        from rich.text import Text

        head = Text()
        head.append(t("ui-diff-1"), style="bold")
        src = point.source if point is not None else None
        head.append_text(kv_line(t("ui-source"), _source_label(src)))
        if point is not None:
            extra = t(
                "diff-point-counts",
                files=point.files_changed,
                added=point.lines_added,
                removed=point.lines_removed,
            )
            head.append(f"  {extra}\n", style="dim")
            if point.prompt_text.strip():
                head.append_text(
                    kv_line(t("diff-context-prompt"), _clip_context(point.prompt_text))
                )
            if point.assistant_text.strip():
                head.append_text(
                    kv_line(t("diff-context-assistant"), _clip_context(point.assistant_text))
                )
        blocks: list = [head, dim_rule(), section_header(t("ui-changes"))]
        if hunk is None:
            empty = t("diff-empty-session") if point is None else t("diff-empty-files")
            blocks.append(Text(empty, style="dim"))
            set_static_renderable(widget, panel_group(*blocks))
            return
        marked = mark_unified_hit(hunk.unified, self._hit_line)
        hunk_text = Text()
        for raw in marked.splitlines():
            style = "bold reverse" if raw.startswith("> ") else None
            if raw.startswith(("> +", "  +")) and not raw.startswith(("> +++", "  +++")):
                style = "bold green reverse" if raw.startswith("> ") else "green"
            elif raw.startswith(("> -", "  -")) and not raw.startswith(("> ---", "  ---")):
                style = "bold red reverse" if raw.startswith("> ") else "red"
            hunk_text.append(raw + "\n", style=style)
        if len(hunk_text.plain) > DIFF_TRUNCATE_THRESHOLD:
            hunk_text = Text(marked[:DIFF_TRUNCATE_THRESHOLD] + "\n")
        blocks.append(hunk_text)
        set_static_renderable(widget, panel_group(*blocks))
        self._scroll_hit_into_view()

    def painted_hit_line(self) -> str | None:
        """The marked unified line currently shown (``> …``), if any."""
        hunk = self._hunk()
        if hunk is None or self._hit_line is None:
            return None
        marked = mark_unified_hit(hunk.unified, self._hit_line)
        for raw in marked.splitlines():
            if raw.startswith("> "):
                return raw
        return None

    def _scroll_hit_into_view(self) -> None:
        if self._hit_line is None:
            return
        line = int(self._hit_line)

        def _go() -> None:
            try:
                scroll = self.query_one("#diff-scroll", VerticalScroll)
            except Exception:
                return
            scroll.scroll_to(y=max(0, 12 + line), animate=False)

        self.call_after_refresh(_go)

    @on(Select.Changed, "#diff-point-select")
    def _on_point_changed(self, event: Select.Changed) -> None:
        if self._syncing or event.value is Select.BLANK or event.value is None:
            return
        key = str(event.value)
        if key == self._point_key:
            return
        self._point_key = key
        self._file_key = None
        self._fill_files()
        self._paint_body()

    @on(DataTable.RowHighlighted, "#diff-file-list")
    def _on_file_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._syncing:
            return
        if event.row_key is None or event.row_key.value is None:
            return
        key = str(event.row_key.value)
        if key == self._file_key:
            return
        self._file_key = key
        self._refresh_hit_line()
        self._paint_body()

    @on(Input.Changed, "#diff-search")
    def _on_search_changed(self, event: Input.Changed) -> None:
        self._query = event.value or ""
        if self._syncing:
            return
        self._fill_files()
        self._paint_body()

    def _refresh_hit_line(self) -> None:
        q = self._query.strip()
        hunk = self._hunk()
        if not q or hunk is None:
            self._hit_line = None
            return
        hits = filter_diff_hunks(q, [(hunk.path, hunk.unified)])
        self._hit_line = hits[0][2] if hits else None
