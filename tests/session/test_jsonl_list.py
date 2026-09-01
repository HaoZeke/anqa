"""Catalog list-meta reads a jsonl header and tail, not the whole file."""

from __future__ import annotations

from pathlib import Path

from anqa.harness.jsonl_list import JsonlFile


def test_first_and_last_objects_ignore_the_middle(tmp_path: Path) -> None:
    path = tmp_path / "sess.jsonl"
    lines = ['{"type":"session","id":"s1"}\n']
    lines.extend(f'{{"type":"noise","n":{i}}}\n' for i in range(200))
    lines.append('{"type":"message","message":{"role":"assistant","stopReason":"stop"}}\n')
    path.write_text("".join(lines), encoding="utf-8")
    transcript = JsonlFile(path)
    first = transcript.first_object()
    assert first is not None
    assert first["type"] == "session"
    assert first["id"] == "s1"
    assert transcript.first_objects(limit=1)[0]["id"] == "s1"
    last = transcript.last_objects(limit=2)
    assert last[-1]["type"] == "message"
    meta = transcript.list_meta(session_id="s1", harness="pi", title="t")
    assert meta.session_id == "s1"
    assert meta.harness == "pi"
    assert meta.title == "t"


def test_list_window_skips_the_middle_of_a_large_file(tmp_path: Path) -> None:
    path = tmp_path / "big.jsonl"
    pad = "x" * 200
    chunks = ['{"type":"session","id":"s1"}\n']
    chunks.extend(f'{{"type":"noise","n":{i},"pad":"{pad}"}}\n' for i in range(4000))
    chunks.append('{"type":"message","id":"tail"}\n')
    path.write_text("".join(chunks), encoding="utf-8")
    assert path.stat().st_size > 64 * 1024
    window = JsonlFile(path).window()
    kinds = [str(row.get("type") or "") for row in window]
    assert "session" in kinds
    assert kinds[-1] == "message"
    assert all(str(row.get("type") or "") != "noise" or "n" in row for row in window)
    assert not any(row.get("n") == 2000 for row in window)
