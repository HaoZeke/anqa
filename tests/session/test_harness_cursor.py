"""Cursor adapter against the committed synthesized store fixture."""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
from pathlib import Path

from anqa.harness.cursor import CURSOR_HARNESS_ID, CursorAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "cursor"
_SID = "aaaaaaaa-1111-4111-8111-000000000001"
_RUNNING_SID = "bbbbbbbb-2222-4222-8222-000000000002"
_FIXTURE_FILE = (
    _FIXTURE_ROOT / "projects" / "tmp-cursor-probe" / "agent-transcripts" / _SID / f"{_SID}.jsonl"
)


def _install_store() -> Path:
    dest = Path.home() / ".cursor"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "projects" / "tmp-cursor-probe" / "agent-transcripts" / _SID / f"{_SID}.jsonl"


def test_discover_and_meta() -> None:
    path = _install_store()
    refs = CursorAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID].harness == CURSOR_HARNESS_ID
    assert by_id[_SID].ref_string() == f"cursor:{_SID}"
    assert by_id[_SID].locator == path.resolve()
    probe = Path(f"cursor:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == CURSOR_HARNESS_ID
    assert meta.title == "Reply with CURSOR_PROBE_OK"
    assert not (meta.title or "").lstrip().startswith("<timestamp>")
    assert meta.run_dir == "/tmp/cursor-probe-ws"
    assert meta.tool_call_count >= 1
    assert meta.list_status_label() == "complete"


def test_load_meta_does_not_read_the_full_transcript(monkeypatch) -> None:
    path = _install_store()

    def boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("list-meta must not read the full transcript")

    monkeypatch.setattr("anqa.json_lines.json_lines", boom)
    meta = CursorAdapter().load_meta(path)
    assert meta.session_id == _SID
    assert meta.title == "Reply with CURSOR_PROBE_OK"
    assert meta.list_status_label() == "complete"


def test_catalog_lists_cursor_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID]["harness"] == CURSOR_HARNESS_ID
    assert by_id[_SID]["path"] == f"cursor:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "idle"


def test_load_meta_reads_model_from_store() -> None:
    _install_store()
    dest = Path.home() / ".cursor" / "chats" / "probe" / _SID
    dest.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(dest / "store.db")
    con.execute("CREATE TABLE blobs (id TEXT, data BLOB)")
    con.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        ("1", b'{"options":{"cursor":{"modelName":"composer-2.5"}}}'),
    )
    con.commit()
    con.close()
    meta = require_adapter(Path(f"cursor:{_SID}")).load_meta(Path(f"cursor:{_SID}"))
    assert meta.model_id == "composer-2.5"


def test_last_open_turn_is_idle() -> None:
    _install_store()
    assert_adapter_turn(Path(f"cursor:{_RUNNING_SID}"), "idle")


def test_list_status_close_bookend_and_later_user(tmp_path: Path) -> None:
    root = tmp_path / "proj" / "agent-transcripts" / "ws"
    root.mkdir(parents=True)

    def _write(name: str, body: str) -> Path:
        path = root / name
        path.write_text(body, encoding="utf-8")
        return path

    closed = _write(
        "closed.jsonl",
        '{"role":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
        '{"type":"turn_ended","status":"completed"}\n',
    )
    bookend = _write(
        "bookend.jsonl",
        '{"role":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n',
    )
    later = _write(
        "later.jsonl",
        '{"type":"turn_ended","status":"completed"}\n'
        '{"role":"user","message":{"content":[{"type":"text","text":"again"}]}}\n',
    )
    assert_adapter_turn(closed, "complete")
    assert_adapter_turn(bookend, "idle")
    assert_adapter_turn(later, "idle")


def test_overview_and_timeline() -> None:
    _install_store()
    ref = CursorAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == CURSOR_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "Cursor"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("Read", 0) >= 1
    events = require_adapter(ref).parse_timeline(ref)
    kinds = [e.event_type for e in events]
    assert "user_message_chunk" in kinds
    assert "agent_message_chunk" in kinds
    assert "tool_call" in kinds
    assert "turn_completed" in kinds
    tool = next(e for e in events if e.event_type == "tool_call")
    assert tool.tool_name == "Read"
    assert tool.raw_input.as_str("path") == "/tmp/cursor-probe.txt"
    texts = " ".join(e.content for e in events)
    assert "CURSOR_PROBE_OK" in texts
    assert "<timestamp>" not in texts
    user = next(e for e in events if e.event_type == "user_message_chunk")
    assert user.content == "Reply with CURSOR_PROBE_OK"
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 3


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = CursorAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is not None
    dest = path.parent / "cursor.tar.gz"
    members = adapter.write_archive(found, dest)
    assert f"{_SID}/{path.name}" in members
    with tarfile.open(dest, "r:gz") as tf:
        assert set(tf.getnames()) == set(members)
    row = CatalogQueryRow(session_id=_SID, title="x", harness="cursor")
    assert row_matches_query(row, "harness:cursor")
    assert not row_matches_query(row, "harness:codex")


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"cursor:{_SID}"), dest=dest)
    assert dest.is_file()
    assert result.session_id == _SID
    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
    assert "session.tar.gz" in names
    inner = tmp_path / "session.tar.gz"
    with tarfile.open(dest, "r:gz") as tf:
        extracted = tf.extractfile("session.tar.gz")
        assert extracted is not None
        inner.write_bytes(extracted.read())
    with tarfile.open(inner, "r:gz") as tf:
        members = tf.getnames()
    assert any(name.endswith(".jsonl") and _SID in name for name in members)
