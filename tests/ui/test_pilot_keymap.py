"""Pilot: keys.toml remaps drive set_keymap and footer display."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.session.turn_gate import read_turn_gate_status, session_awaits_follow_up
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
    app = AnqaApp(work_dir=work, traces_path=traces)
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
async def test_refused_overlay_keeps_default_follow_and_list_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"list.down" = "n"\n', encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    gate = traces / ".anqa-turn"
    gate.mkdir(parents=True, exist_ok=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "pilot-keymap-1", "turn": 1})
        + "\n",
        encoding="utf-8",
    )
    app = AnqaApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: (
                app._keymap.get("session.follow") == "n"
                and app._keymap.get("list.down") == "j,down"
            ),
            description="refused overlay leaves follow=n and list.down=j,down",
        )
        assert app._keymap["session.follow"] == "n"
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
    app = AnqaApp(work_dir=work, traces_path=traces)
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
    app = AnqaApp(work_dir=work, traces_path=traces)
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


def _write_awaiting(traces: Path, session_id: str) -> None:
    gate = traces / ".anqa-turn"
    gate.mkdir(parents=True, exist_ok=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": session_id, "turn": 1}) + "\n",
        encoding="utf-8",
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
    app = AnqaApp(work_dir=work, traces_path=traces)
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
async def test_colemak_leader_follow_and_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text(_colemak_text(), encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    _write_awaiting(traces, "pilot-keymap-1")
    app = AnqaApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="session row")
        table.focus()
        await wait_until(
            pilot,
            lambda: app._resolved_keymap is not None and app._resolved_keymap.leader == ";",
            description="colemak leader loaded",
        )
        await pilot.press("semicolon")
        await wait_until(pilot, lambda: app._leader_armed, description="leader armed")
        displays = _footer_key_displays(app)
        assert any(";" in d or "semicolon" in d for d in displays)
        await pilot.press("n")
        await wait_until(
            pilot,
            lambda: any(type(s).__name__ == "InteractiveSessionsModal" for s in app.screen_stack),
            description="leader+n opens follow-up",
        )
        assert not app._leader_armed
        await pilot.press("escape")
        await wait_until(
            pilot,
            lambda: (
                not any(type(s).__name__ == "InteractiveSessionsModal" for s in app.screen_stack)
            ),
            description="follow-up dismissed",
        )
        table.focus()
        table.move_cursor(row=0, animate=False)
        sess = traces / "pilot-keymap-1"
        await wait_until(
            pilot,
            lambda: session_awaits_follow_up(sess) and bool(app._awaiting_session_targets()),
            description="still awaiting after dismissed follow-up",
        )
        await pilot.press("f24")
        await pilot.pause()
        assert not any(type(s).__name__ == "InteractiveSessionsModal" for s in app.screen_stack)
        assert session_awaits_follow_up(sess)
        await pilot.press("semicolon")
        await wait_until(pilot, lambda: app._leader_armed, description="leader armed for done")
        await pilot.press("e")
        await wait_until(
            pilot,
            lambda: (
                session_awaits_follow_up(sess) is False
                or read_turn_gate_status(sess).get("state") == "done"
            ),
            description="leader+e marks the session done",
        )
        assert not app._leader_armed
        assert session_awaits_follow_up(sess) is False


@pytest.mark.asyncio
async def test_leader_cancelled_by_escape_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    app = AnqaApp(work_dir=work, traces_path=traces)
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
    keys.write_text(_colemak_text(), encoding="utf-8")
    monkeypatch.setenv("ANQA_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = AnqaApp(work_dir=work, traces_path=traces)
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
