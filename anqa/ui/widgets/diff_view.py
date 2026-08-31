"""Diff pane: Turn picker, Prompt/Assistant bar, files | hunk split."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Select, Static, TabbedContent, TabPane, Tree
from textual.widgets.tree import TreeNode

from ...constants import DIFF_TRUNCATE_THRESHOLD
from ...diff_tree import tree_rows
from ...session.workspace_diff import DiffHunk, DiffPoint, WorkspaceDiff
from ..fuzzy import filter_diff_hunks, mark_unified_hit
from ..i18n import t
from ..panel_render import md_content
from ..render_detail import set_static_renderable
from ..selectable_static import SelectableStatic
from .controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS


def _tree_nodes(node: TreeNode[tuple[str, str]]) -> list[TreeNode[tuple[str, str]]]:
    out: list[TreeNode[tuple[str, str]]] = []
    for child in node.children:
        out.append(child)
        out.extend(_tree_nodes(child))
    return out


def _point_label(point: DiffPoint, index: int) -> str:
    if point.source == "search_replace":
        return t("diff-point-edits")
    if point.prompt_index is not None:
        return t("diff-point-prompt", n=point.prompt_index)
    return t("diff-point-rewind", n=index + 1)


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
        with Horizontal(id="diff-chrome"):
            with Vertical(id="diff-filter-bar", classes=FILTER_BAR_CLASS):
                yield Static(t("diff-filter"), id="diff-filter-label", classes=FILTER_LABEL_CLASS)
                yield Select(
                    [(t("diff-point-edits"), "edits")],
                    value="edits",
                    id="diff-point-select",
                    allow_blank=False,
                    classes="field-select",
                )
            with Vertical(id="diff-context"):
                with TabbedContent(id="diff-context-tabs"):
                    with TabPane(t("diff-context-prompt"), id="diff-tab-prompt"):
                        with VerticalScroll(id="diff-prompt-scroll"):
                            yield SelectableStatic(id="diff-prompt")
                    with TabPane(t("diff-context-assistant"), id="diff-tab-assistant"):
                        with VerticalScroll(id="diff-assistant-scroll"):
                            yield SelectableStatic(id="diff-assistant")
        with Horizontal(id="diff-search-bar", classes=FILTER_BAR_CLASS):
            yield Input(placeholder=t("diff-search-placeholder"), id="diff-search")
        with Horizontal(id="diff-layout"):
            with Vertical(id="diff-files"):
                tree: Tree[tuple[str, str]] = Tree("Files", id="diff-file-list")
                tree.show_root = False
                yield tree
            with VerticalScroll(id="diff-scroll"):
                yield SelectableStatic(id="diff-content")

    def on_mount(self) -> None:
        self._paint()

    def set_doc(self, doc: WorkspaceDiff) -> None:
        """Replace the loaded snapshots and keep the current file when it still exists."""
        if self._doc is doc:
            return
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
            self._paint_context()
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
        tree = self.query_one("#diff-file-list", Tree)
        files = self._visible_files()
        tree.clear()
        tree.show_root = False
        stack = [tree.root]
        select_node = None
        for row in tree_rows([h.path for h in files]):
            while len(stack) > row.depth + 1:
                stack.pop()
            parent = stack[-1]
            if row.kind == "dir":
                node = parent.add(row.label, data=("dir", row.path), expand=True)
                stack.append(node)
                continue
            node = parent.add_leaf(row.label, data=("file", row.path))
            if self._file_key == row.path:
                select_node = node
        if files:
            keep = self._file_key if any(h.path == self._file_key for h in files) else files[0].path
            self._file_key = keep
            if select_node is None or select_node.data != ("file", keep):
                select_node = next(
                    (n for n in _tree_nodes(tree.root) if n.data == ("file", keep)),
                    None,
                )
            if select_node is not None:
                tree.select_node(select_node)
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

    def _paint_context(self) -> None:
        point = self._current_point()
        prompt = point.prompt_text if point is not None else ""
        assistant = point.assistant_text if point is not None else ""
        prompt_w = self.query_one("#diff-prompt", SelectableStatic)
        asst_w = self.query_one("#diff-assistant", SelectableStatic)
        if prompt.strip():
            set_static_renderable(prompt_w, Text(prompt))
        else:
            set_static_renderable(prompt_w, Text(t("diff-empty-context"), style="dim"))
        if assistant.strip():
            set_static_renderable(asst_w, md_content(assistant, indent=0))
        else:
            set_static_renderable(asst_w, Text(t("diff-empty-context"), style="dim"))

    def _paint_body(self) -> None:
        widget = self.query_one("#diff-content", SelectableStatic)
        point = self._current_point()
        hunk = self._hunk()
        if hunk is None:
            empty = t("diff-empty-session") if point is None else t("diff-empty-files")
            set_static_renderable(widget, Text(empty, style="dim"))
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
        set_static_renderable(widget, hunk_text)
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
            scroll.scroll_to(y=max(0, line), animate=False)

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
        self._paint_context()
        self._paint_body()

    @on(Tree.NodeHighlighted, "#diff-file-list")
    def _on_file_highlighted(self, event: Tree.NodeHighlighted[tuple[str, str]]) -> None:
        if self._syncing:
            return
        data = event.node.data
        if data is None or data[0] != "file":
            return
        key = data[1]
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
