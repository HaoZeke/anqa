"""Pilot: keys.toml remaps drive set_keymap and footer display."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.ui.app import AnqaApp
from textual.widgets import DataTable

from .pilot_helpers import wait_until


def _minimal_traces(work: Path) -> Path:
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sd = traces / "pilot-keymap-1"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-keymap-1", "cwd": "/workspace"},
                "session_summary": "pilot",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "ts": "2026-06-25T00:01:00Z", "outcome": "success"})
        + "\n",
        encoding="utf-8",
    )
    return traces


def _binding_keys(app: AnqaApp, binding_id: str) -> set[str]:
    return {
        key
        for key, ab in app.active_bindings.items()
        if getattr(ab.binding, "id", None) == binding_id
    }


def _footer_key_displays(app: AnqaApp) -> list[str]:
    footer = app.query_one("Footer")
    return [str(w.key_display).lower() for w in footer.query("FooterKey")]


@pytest.mark.asyncio
async def test_overlay_remap_updates_footer_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"search.focus" = "z"\n', encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: (
                "z" in _binding_keys(app, "search.focus")
                and any(d == "z" for d in _footer_key_displays(app))
            ),
            description="search.focus remapped to z in bindings and footer",
        )
        assert _binding_keys(app, "search.focus") == {"z"}
        displays = _footer_key_displays(app)
        assert any(d == "z" for d in displays)
        assert not any(d == "/" or d == "slash" for d in displays)


@pytest.mark.asyncio
async def test_refused_overlay_keeps_default_list_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"list.down" = "k"\n', encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: app._keymap.get("list.down") == "j,down",
            description="refused overlay leaves list.down=j,down",
        )
        assert app._keymap["list.down"] == "j,down"
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="session row")
        table.focus()
        await wait_until(
            pilot,
            lambda: _binding_keys(app, "list.down") == {"j", "down"},
            description="focused table still uses default j,down",
        )
        start = table.cursor_row
        await pilot.press("n")
        await pilot.pause()
        assert table.cursor_row == start


@pytest.mark.asyncio
async def test_default_keymap_footer_unchanged(tmp_path: Path) -> None:
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: "slash" in _binding_keys(app, "search.focus"),
            description="search.focus default slash",
        )
        assert _binding_keys(app, "search.focus") == {"slash"}
        help_keys = _binding_keys(app, "help.toggle")
        assert help_keys, "help.toggle stays bound"
        assert "z" not in help_keys


@pytest.mark.asyncio
async def test_list_down_remap_moves_table_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"list.down" = "h"\n', encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    extra = traces / "pilot-keymap-2"
    extra.mkdir()
    (extra / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-keymap-2", "cwd": "/workspace"},
                "session_summary": "pilot-2",
                "created_at": "2026-06-25T00:02:00Z",
                "updated_at": "2026-06-25T00:03:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="two session rows")
        table.focus()
        await wait_until(
            pilot,
            lambda: "h" in _binding_keys(app, "list.down"),
            description="list.down remapped to h",
        )
        table.move_cursor(row=0, animate=False)
        start = table.cursor_row
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row == start
        await pilot.press("h")
        await wait_until(
            pilot,
            lambda: table.cursor_row != start,
            description="remapped h moves the list",
        )


def _colemak_text() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "examples" / "keys" / "colemak.toml").read_text(encoding="utf-8")


def _leader_nav_text() -> str:
    return (
        'leader = ";"\n'
        "leader_timeout_ms = 800\n\n"
        "[home]\n"
        '"list.down" = "n"\n'
        '"list.up" = "e"\n'
        '"sessions.home" = "leader+n"\n'
    )


@pytest.mark.asyncio
async def test_colemak_n_e_are_list_nav(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text(_colemak_text(), encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    extra = traces / "pilot-keymap-2"
    extra.mkdir()
    (extra / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-keymap-2", "cwd": "/workspace"},
                "session_summary": "pilot-2",
                "created_at": "2026-06-25T00:02:00Z",
                "updated_at": "2026-06-25T00:03:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="two session rows")
        table.focus()
        await wait_until(
            pilot,
            lambda: "n" in _binding_keys(app, "list.down"),
            description="list.down remapped to n",
        )
        table.move_cursor(row=0, animate=False)
        start = table.cursor_row
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row == start
        await pilot.press("n")
        await wait_until(
            pilot,
            lambda: table.cursor_row != start,
            description="colemak n moves the list",
        )
        after_n = table.cursor_row
        await pilot.press("e")
        await wait_until(
            pilot,
            lambda: table.cursor_row == start,
            description="colemak e moves the list up",
        )
        assert after_n != start


@pytest.mark.asyncio
async def test_leader_cancelled_by_escape_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text(_leader_nav_text(), encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    extra = traces / "pilot-keymap-2"
    extra.mkdir()
    (extra / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-keymap-2", "cwd": "/workspace"},
                "session_summary": "pilot-2",
                "created_at": "2026-06-25T00:02:00Z",
                "updated_at": "2026-06-25T00:03:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="two rows")
        table.focus()
        await pilot.press("semicolon")
        await wait_until(pilot, lambda: app._leader_armed, description="leader armed")
        await pilot.press("escape")
        await wait_until(pilot, lambda: not app._leader_armed, description="Esc cancels leader")
        table.move_cursor(row=0, animate=False)
        start = table.cursor_row
        await pilot.press("n")
        await wait_until(
            pilot,
            lambda: table.cursor_row != start,
            description="n after cancel is list nav",
        )
        await pilot.press("semicolon")
        await wait_until(pilot, lambda: app._leader_armed, description="leader armed again")
        app._leader_disarm()
        await wait_until(pilot, lambda: not app._leader_armed, description="timeout/disarm")


@pytest.mark.asyncio
async def test_leader_not_armed_in_search_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text(_leader_nav_text(), encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = AnqaApp(traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        search = app.query_one("#session-search-input")
        search.focus()
        await wait_until(pilot, lambda: search.has_focus, description="search focused")
        await pilot.press("semicolon")
        await pilot.pause()
        assert not app._leader_armed
        value = getattr(search, "value", "")
        assert ";" in str(value) or "semicolon" in str(value) or value != ""
