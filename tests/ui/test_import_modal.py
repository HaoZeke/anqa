"""Import picker returns a tree selection or a typed path."""

from __future__ import annotations

from pathlib import Path

import pytest
from anqa.ui.import_modal import (
    ImportPathModal,
    ImportSourceTree,
    parent_location,
    visible_import_paths,
)
from textual.app import App
from textual.widgets import Input, Static

from .pilot_helpers import wait_until


def _harness(location: Path, results: list[str | None]) -> App[None]:
    class Harness(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(ImportPathModal(location=location), results.append)

    return Harness()


@pytest.mark.asyncio
async def test_import_path_modal_returns_typed_path(tmp_path: Path) -> None:
    results: list[str | None] = []
    app = _harness(tmp_path, results)
    async with app.run_test(size=(80, 28)) as pilot:
        await wait_until(pilot, lambda: isinstance(app.screen, ImportPathModal))
        modal = app.screen
        assert isinstance(modal, ImportPathModal)
        modal.query_one("#import-path-input", Input).value = "/tmp/sess.tar.gz"
        modal._commit()
        await wait_until(pilot, lambda: bool(results))
        assert results[0] == "/tmp/sess.tar.gz"


@pytest.mark.asyncio
async def test_import_path_modal_cancel_returns_none(tmp_path: Path) -> None:
    results: list[str | None] = []
    app = _harness(tmp_path, results)
    async with app.run_test(size=(80, 28)) as pilot:
        await wait_until(pilot, lambda: isinstance(app.screen, ImportPathModal))
        app.screen.action_cancel()
        await wait_until(pilot, lambda: bool(results))
        assert results[0] is None


@pytest.mark.asyncio
async def test_import_path_modal_enter_on_archive(tmp_path: Path) -> None:
    archive = tmp_path / "sess.tar.gz"
    archive.write_bytes(b"x")
    results: list[str | None] = []
    app = _harness(tmp_path, results)
    async with app.run_test(size=(80, 28)) as pilot:
        await wait_until(pilot, lambda: isinstance(app.screen, ImportPathModal))
        modal = app.screen
        assert isinstance(modal, ImportPathModal)
        tree = modal.query_one("#import-path-tree", ImportSourceTree)

        def _has_archive() -> bool:
            return any(
                node.data is not None and node.data.path.name == "sess.tar.gz"
                for node in tree.root.children
            )

        await wait_until(pilot, _has_archive)
        node = next(
            n
            for n in tree.root.children
            if n.data is not None and n.data.path.name == "sess.tar.gz"
        )
        tree.move_cursor(node)
        await pilot.press("enter")
        await wait_until(pilot, lambda: bool(results))
        assert results[0] == str(archive)


def test_import_tree_lists_archives_and_skips_junk(tmp_path: Path) -> None:
    (tmp_path / "sess.tar.gz").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("nope", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    keep = tmp_path / "keep"
    keep.mkdir()
    shown = {p.name for p in visible_import_paths(tmp_path.iterdir())}
    assert shown == {"sess.tar.gz", "keep"}


def test_parent_location_stops_at_root() -> None:
    leaf = Path("/tmp/anqa-import-parent/child")
    got = parent_location(leaf)
    assert got is not None
    assert got == leaf.parent.resolve()
    root = Path(leaf.anchor or "/")
    assert parent_location(root) is None


def test_import_tree_icons_are_tree_chevrons() -> None:
    assert "📁" not in ImportSourceTree.ICON_NODE
    assert "📂" not in ImportSourceTree.ICON_NODE_EXPANDED
    assert "📄" not in ImportSourceTree.ICON_FILE
    assert ImportSourceTree.ICON_NODE == "▶ "
    assert ImportSourceTree.ICON_NODE_EXPANDED == "▼ "


@pytest.mark.asyncio
async def test_import_path_modal_backspace_goes_to_parent(tmp_path: Path) -> None:
    child = tmp_path / "nested"
    child.mkdir()
    results: list[str | None] = []
    app = _harness(child, results)
    async with app.run_test(size=(80, 28)) as pilot:
        await wait_until(pilot, lambda: isinstance(app.screen, ImportPathModal))
        modal = app.screen
        assert isinstance(modal, ImportPathModal)
        tree = modal.query_one("#import-path-tree", ImportSourceTree)

        def _at(path: Path) -> bool:
            try:
                return Path(tree.path).resolve() == path.resolve()
            except OSError:
                return False

        await wait_until(pilot, lambda: _at(child))
        await pilot.press("backspace")
        await wait_until(pilot, lambda: _at(tmp_path))
        here = modal.query_one("#import-path-here", Static)
        assert tmp_path.name in str(here.content)
        assert results == []
