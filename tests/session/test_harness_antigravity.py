"""Antigravity adapter against the committed synthesized store fixture."""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
from pathlib import Path

from anqa.harness.antigravity import ANTIGRAVITY_HARNESS_ID, AntigravityAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "antigravity" / "antigravity-cli"
)
_SID = "aaaaaaaa-1111-4111-8111-000000000001"
_RUNNING_SID = "bbbbbbbb-2222-4222-8222-000000000002"
_CHILD_SID = "cccccccc-3333-4333-8333-000000000003"


def _install_store() -> Path:
    dest = Path.home() / ".gemini" / "antigravity-cli"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "conversations" / f"{_SID}.db"


def test_discover_skips_child_sessions() -> None:
    path = _install_store()
    refs = AntigravityAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert set(by_id) == {_SID, _RUNNING_SID}
    assert _CHILD_SID not in by_id
    assert by_id[_SID].harness == ANTIGRAVITY_HARNESS_ID
    assert by_id[_SID].ref_string() == f"antigravity:{_SID}"
    assert by_id[_SID].locator == path.resolve()


def test_catalog_lists_antigravity_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert _CHILD_SID not in by_id
    assert by_id[_SID]["harness"] == ANTIGRAVITY_HARNESS_ID
    assert by_id[_SID]["path"] == f"antigravity:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "running"


def test_discover_and_meta() -> None:
    _install_store()
    probe = Path(f"antigravity:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == ANTIGRAVITY_HARNESS_ID
    assert meta.title == "Reply with AGY_PROBE_OK"
    assert meta.run_dir == "/tmp/probe-ws"
    assert meta.tool_call_count >= 1
    assert meta.has_subagents
    assert meta.subagent_count == 1
    assert meta.list_status_label() == "complete"


def test_load_meta_does_not_read_the_full_transcript(monkeypatch) -> None:
    _install_store()

    def boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("list-meta must not read the full transcript")

    monkeypatch.setattr("anqa.json_lines.json_lines", boom)
    meta = require_adapter(Path(f"antigravity:{_SID}")).load_meta(Path(f"antigravity:{_SID}"))
    assert meta.session_id == _SID
    assert meta.title == "Reply with AGY_PROBE_OK"
    assert meta.list_status_label() == "complete"


def test_load_meta_reads_model_from_conversation_db() -> None:
    path = _install_store()
    blob = b"\x00model_enum\x00gemini-3.7-flash\x00used_claude\x00"
    con = sqlite3.connect(path)
    cols = [str(row[1]) for row in con.execute("PRAGMA table_info(gen_metadata)")]
    if "size" in cols:
        con.execute("DELETE FROM gen_metadata")
        con.execute(
            "INSERT INTO gen_metadata (idx, data, size) VALUES (0, ?, ?)", (blob, len(blob))
        )
    else:
        con.execute("CREATE TABLE IF NOT EXISTS gen_metadata (idx INTEGER, data BLOB)")
        con.execute("DELETE FROM gen_metadata")
        con.execute("INSERT INTO gen_metadata (idx, data) VALUES (0, ?)", (blob,))
    con.commit()
    con.close()
    meta = require_adapter(Path(f"antigravity:{_SID}")).load_meta(Path(f"antigravity:{_SID}"))
    assert meta.model_id == "gemini-3.7-flash"


def test_running_session_is_not_complete() -> None:
    _install_store()
    assert_adapter_turn(Path(f"antigravity:{_SID}"), "complete")
    assert_adapter_turn(Path(f"antigravity:{_RUNNING_SID}"), "running")


def _add_transcript(root: Path, sid: str, lines: str) -> None:
    shutil.copy2(root / "conversations" / f"{_SID}.db", root / "conversations" / f"{sid}.db")
    dest = root / "brain" / sid / ".system_generated" / "logs"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "transcript.jsonl").write_text(lines, encoding="utf-8")


def test_list_status_last_user_and_later_open_after_close() -> None:
    root = _install_store().parent.parent
    _add_transcript(
        root,
        "dddddddd-4444-4444-8444-000000000004",
        '{"type":"USER_INPUT","content":"hi"}\n',
    )
    _add_transcript(
        root,
        "eeeeeeee-5555-4555-8555-000000000005",
        '{"type":"USER_INPUT","status":"DONE","content":"hi"}\n'
        '{"type":"USER_INPUT","content":"again"}\n',
    )
    assert_adapter_turn(Path("antigravity:dddddddd-4444-4444-8444-000000000004"), "idle")
    assert_adapter_turn(Path("antigravity:eeeeeeee-5555-4555-8555-000000000005"), "idle")


def test_overview_stats_count_timeline_tools() -> None:
    _install_store()
    ref = AntigravityAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == ANTIGRAVITY_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "Antigravity"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("run_command", 0) >= 1


def test_timeline_user_tool_result() -> None:
    _install_store()
    ref = AntigravityAdapter().ref_for_id(_SID)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types[0] == "turn_started"
    assert "user_message_chunk" in types
    assert "agent_thought_chunk" in types
    assert "tool_call" in types
    assert "tool_call_update" in types
    tool = next(e for e in events if e.event_type == "tool_call")
    assert tool.tool_name == "run_command"
    assert tool.raw_input.as_str("CommandLine") == "echo AGY_PROBE_OK"
    texts = " ".join(e.content for e in events)
    assert "AGY_PROBE_OK" in texts
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 5


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = AntigravityAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is not None
    dest = path.parent / "antigravity.tar.gz"
    members = adapter.write_archive(found, dest)
    assert f"{_SID}/{path.name}" in members
    with tarfile.open(dest, "r:gz") as tf:
        assert set(tf.getnames()) == set(members)
    row = CatalogQueryRow(session_id=_SID, title="x", harness="antigravity")
    assert row_matches_query(row, "harness:antigravity")
    assert not row_matches_query(row, "harness:gemini")


def test_db_wal_write_is_a_list_rebuild_path() -> None:
    from anqa.control.daemon import CatalogWatchApply

    assert CatalogWatchApply.list_rebuild_path(
        Path("/store/aaaaaaaa-1111-4111-8111-000000000001.db")
    )
    assert CatalogWatchApply.list_rebuild_path(
        Path("/store/aaaaaaaa-1111-4111-8111-000000000001.db-wal")
    )


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"antigravity:{_SID}"), dest=dest)
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
    assert f"{_SID}/{_SID}.db" in members
    assert f"{_SID}/transcript.jsonl" in members


def test_timeline_stamp_follows_that_session_transcript() -> None:
    db = _install_store()
    adapter = AntigravityAdapter()
    ref_a = Path(f"antigravity:{_SID}")
    stamp_a = adapter.timeline_stamp(ref_a)
    root = db.parent.parent
    events_b = root / "brain" / _RUNNING_SID / ".system_generated" / "logs" / "transcript.jsonl"
    events_b.write_text(
        events_b.read_text(encoding="utf-8") + '{"type":"USER_INPUT","content":"later-b"}\n',
        encoding="utf-8",
    )
    assert adapter.timeline_stamp(ref_a) == stamp_a
    events_a = root / "brain" / _SID / ".system_generated" / "logs" / "transcript.jsonl"
    events_a.write_text(
        events_a.read_text(encoding="utf-8") + '{"type":"USER_INPUT","content":"later-a"}\n',
        encoding="utf-8",
    )
    assert adapter.timeline_stamp(ref_a) != stamp_a
