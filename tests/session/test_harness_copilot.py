"""Copilot adapter against the committed synthesized store fixture."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

import pytest
from anqa.harness.copilot import COPILOT_HARNESS_ID, CopilotAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.delete import delete_session_dirs
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "copilot"
_SID = "aaaaaaaa-1111-4111-8111-000000000001"
_RUNNING_SID = "bbbbbbbb-2222-4222-8222-000000000002"


def _install_store() -> Path:
    dest = Path.home() / ".copilot"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "session-store.db"


def test_discover_and_meta() -> None:
    path = _install_store()
    refs = CopilotAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID].harness == COPILOT_HARNESS_ID
    assert by_id[_SID].ref_string() == f"copilot:{_SID}"
    assert by_id[_SID].locator == path.resolve()
    probe = Path(f"copilot:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == COPILOT_HARNESS_ID
    assert meta.title == "Reply with COPILOT_PROBE_OK"
    assert meta.run_dir == "/tmp/copilot-probe-ws"
    assert meta.model_id == "gpt-5-mini"
    assert meta.harness_version == "1.0.82"
    assert meta.tool_call_count >= 1
    assert meta.has_subagents
    assert meta.subagent_count == 1
    assert meta.list_status_label() == "complete"


def test_load_meta_does_not_read_the_full_transcript(monkeypatch) -> None:
    _install_store()

    def boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("list-meta must not read the full transcript")

    monkeypatch.setattr("anqa.json_lines.json_lines", boom)
    meta = CopilotAdapter().load_meta(Path(f"copilot:{_SID}"))
    assert meta.session_id == _SID
    assert meta.title == "Reply with COPILOT_PROBE_OK"
    assert meta.list_status_label() == "complete"


def test_list_meta_does_not_map_the_window() -> None:
    _install_store()
    meta = CopilotAdapter().load_meta(Path(f"copilot:{_SID}"))
    assert meta.title == "Reply with COPILOT_PROBE_OK"
    assert meta.turn_outcome != ""


def test_catalog_lists_copilot_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID]["harness"] == COPILOT_HARNESS_ID
    assert by_id[_SID]["path"] == f"copilot:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "idle"


def test_delete_session_removes_row_and_state() -> None:
    dest = _install_store()
    state = dest.parent / "session-state" / _SID
    assert state.is_dir()
    stats = delete_session_dirs([Path(f"copilot:{_SID}")])
    assert int(stats["deleted"] or 0) == 1
    assert not state.exists()
    with pytest.raises(FileNotFoundError):
        require_adapter(Path(f"copilot:{_SID}")).load_meta(Path(f"copilot:{_SID}"))


def test_last_open_turn_is_idle() -> None:
    dest = _install_store()
    assert_adapter_turn(Path(f"copilot:{_SID}"), "complete")
    assert_adapter_turn(Path(f"copilot:{_RUNNING_SID}"), "idle")
    events = dest.parent / "session-state" / _SID / "events.jsonl"
    events.write_text(
        events.read_text(encoding="utf-8")
        + '{"type":"assistant.turn_start","id":"later","timestamp":"2026-08-30T12:10:00.000Z"}\n',
        encoding="utf-8",
    )
    assert_adapter_turn(Path(f"copilot:{_SID}"), "idle")


def test_overview_stats_count_timeline_tools() -> None:
    _install_store()
    ref = CopilotAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == COPILOT_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "GitHub Copilot"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("bash", 0) >= 1
    turns = ov["turns"]
    assert isinstance(turns, dict)
    runs = turns.get("subagentRuns")
    assert isinstance(runs, list)
    assert len(runs) == 1
    run = runs[0]
    assert run["subagentId"] == "child-1"
    assert run["subagentType"] == "explore"
    assert run["status"] == "completed"


def test_timeline_user_tool_result() -> None:
    _install_store()
    ref = CopilotAdapter().ref_for_id(_SID)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types[0] == "turn_started"
    assert "user_message_chunk" in types
    assert "agent_message_chunk" in types
    assert "tool_call" in types
    assert "tool_call_update" in types
    assert "subagent_spawned" in types
    assert "subagent_finished" in types
    tool = next(e for e in events if e.event_type == "tool_call" and e.tool_name == "bash")
    assert tool.raw_input.as_str("command") == "echo COPILOT_PROBE_OK"
    texts = " ".join(e.content for e in events)
    assert "COPILOT_PROBE_OK" in texts
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 5


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = CopilotAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is None
    dest = path.parent / "copilot.tar.gz"
    members = adapter.write_archive(found, dest)
    assert f"{_SID}/events.jsonl" in members
    with tarfile.open(dest, "r:gz") as tf:
        assert set(tf.getnames()) == set(members)
    row = CatalogQueryRow(session_id=_SID, title="x", harness="copilot")
    assert row_matches_query(row, "harness:copilot")
    assert not row_matches_query(row, "harness:opencode")


def test_db_wal_write_is_a_list_rebuild_path() -> None:
    from anqa.control.daemon import CatalogWatchApply

    assert CatalogWatchApply.list_rebuild_path(Path("/store/session-store.db"))
    assert CatalogWatchApply.list_rebuild_path(Path("/store/session-store.db-wal"))


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"copilot:{_SID}"), dest=dest)
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
    assert f"{_SID}/events.jsonl" in members


def test_timeline_stamp_follows_that_session_events_file() -> None:
    db = _install_store()
    adapter = CopilotAdapter()
    ref_a = Path(f"copilot:{_SID}")
    stamp_a = adapter.timeline_stamp(ref_a)
    events_b = db.parent / "session-state" / _RUNNING_SID / "events.jsonl"
    events_b.write_text(
        events_b.read_text(encoding="utf-8") + '{"type":"user.message","id":"later-b"}\n',
        encoding="utf-8",
    )
    assert adapter.timeline_stamp(ref_a) == stamp_a
    db.write_bytes(db.read_bytes() + b"\x00")
    assert adapter.timeline_stamp(ref_a) == stamp_a
    events_a = db.parent / "session-state" / _SID / "events.jsonl"
    events_a.write_text(
        events_a.read_text(encoding="utf-8") + '{"type":"user.message","id":"later-a"}\n',
        encoding="utf-8",
    )
    assert adapter.timeline_stamp(ref_a) != stamp_a
