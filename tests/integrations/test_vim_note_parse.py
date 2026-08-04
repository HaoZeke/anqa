"""Neovim Markdown note round-trip (``note_at_row``) — skip without nvim."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from importlib import import_module
from pathlib import Path

import pytest
from groket.notes import NoteEntry, NotesDoc, save_notes


def _write_session(session_dir: Path) -> None:
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "sessionId": session_dir.name,
                "title": "Note parse",
                "model": "test-model",
            }
        ),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1001,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "hello"},
                    "_meta": {"promptIndex": 1},
                }
            },
        },
        {
            "timestamp": 1002,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "world"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(u) + "\n" for u in updates),
        encoding="utf-8",
    )
    markers = [
        {"type": "turn_started", "turn_number": 1, "ts": 1000},
        {"type": "turn_ended", "outcome": "success", "ts": 1100},
    ]
    (session_dir / "events.jsonl").write_text(
        "".join(json.dumps(m) + "\n" for m in markers),
        encoding="utf-8",
    )


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_note_at_row_round_trips_heading_field_values(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-nvim-note"
    session_dir.mkdir()
    _write_session(session_dir)
    detail = "# repro\nsteps here\n<!-- groket:field-id=spoof -->\nmore"
    note = NoteEntry.new(
        turn_index=0,
        fields={"summary": "Title line", "detail": detail},
        event_indices=[2],
        note_id="n-round",
    )
    save_notes(session_dir, NotesDoc(session_id=session_dir.name, notes=[note]))

    editor = import_module("groket.integrations.editor")
    document = editor.render_editor_document(session_dir, format="markdown")
    md_path = tmp_path / "session.md"
    md_path.write_text(document.text, encoding="utf-8")

    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    assert (vim_root / "lua" / "groket" / "init.lua").is_file()

    # Find a line inside the detail field body for cursor placement.
    detail_anchor = "<!-- groket:field-id=detail note-id=n-round -->"
    lines = document.text.splitlines()
    cursor_row = next(i + 2 for i, line in enumerate(lines) if line == detail_anchor)

    harness = tmp_path / "note_roundtrip.lua"
    out_json = tmp_path / "note_out.json"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local path = {str(md_path)!r}
            local lines = vim.fn.readfile(path)
            local buf = vim.api.nvim_create_buf(false, true)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            local note = groket._note_at_row(buf, {cursor_row})
            vim.fn.writefile({{ vim.json.encode(note) }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    assert out_json.is_file(), f"no output:\n{proc.stdout}\n{proc.stderr}"

    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["id"] == "n-round"
    assert parsed["fields"]["summary"] == "Title line"
    assert parsed["fields"]["detail"] == detail


@pytest.mark.skipif(shutil.which("nvim") is None, reason="nvim not on PATH")
def test_nvim_parse_groket_comment_requires_column_zero(tmp_path: Path) -> None:
    vim_root = Path(import_module("groket.integrations").__file__).resolve().parent / "vim"
    harness = tmp_path / "anchor.lua"
    out_json = tmp_path / "anchor_out.json"
    harness.write_text(
        textwrap.dedent(
            f"""\
            vim.opt.runtimepath:prepend({str(vim_root)!r})
            local groket = require("groket")
            local parse = groket._parse_groket_comment
            local payload = {{
              col0 = parse("<!-- groket:note-id=n1 -->") ~= nil,
              indented = parse("    <!-- groket:note-id=n1 -->") ~= nil,
              mid = parse("x <!-- groket:note-id=n1 -->") ~= nil,
            }}
            vim.fn.writefile({{ vim.json.encode(payload) }}, {str(out_json)!r})
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["nvim", "--headless", "-u", "NONE", "-n", "-l", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"nvim failed:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["col0"] is True
    assert payload["indented"] is False
    assert payload["mid"] is False
