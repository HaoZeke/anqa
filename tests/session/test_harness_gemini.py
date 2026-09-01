"""Gemini CLI adapter against the committed synthesized store fixture."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from anqa.harness.gemini import GEMINI_HARNESS_ID, GeminiAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "gemini" / "tmp"
_SID = "aaaaaaaa-1111-4111-8111-000000000001"
_RUNNING_SID = "bbbbbbbb-2222-4222-8222-000000000002"
_CHILD_SID = "cccccccc-3333-4333-8333-000000000003"
_CONTEXT_SID = "eeeeeeee-5555-4555-8555-000000000005"
_FIXTURE_FILE = _FIXTURE_ROOT / "probe-ws" / "chats" / "session-2026-08-09T12-00-aaaaaaaa.jsonl"


def _install_store() -> Path:
    dest = Path.home() / ".gemini" / "tmp"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "probe-ws" / "chats" / _FIXTURE_FILE.name


def test_discover_and_meta() -> None:
    path = _install_store()
    refs = GeminiAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert set(by_id) == {_SID, _RUNNING_SID}
    assert _CHILD_SID not in by_id
    assert _CONTEXT_SID not in by_id
    assert by_id[_SID].harness == GEMINI_HARNESS_ID
    assert by_id[_SID].ref_string() == f"gemini:{_SID}"
    assert by_id[_SID].locator == path.resolve()
    probe = Path(f"gemini:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == GEMINI_HARNESS_ID
    assert meta.title == "Reply with GEMINI_PROBE_OK"
    assert meta.model_id == "gemini-2.5-pro"
    assert meta.run_dir == "/tmp/probe-ws"
    assert meta.tool_call_count >= 1
    assert meta.list_status_label() == "complete"


def test_catalog_lists_gemini_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert _CHILD_SID not in by_id
    assert _CONTEXT_SID not in by_id
    assert by_id[_SID]["harness"] == GEMINI_HARNESS_ID
    assert by_id[_SID]["path"] == f"gemini:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "running"


def test_session_context_only_is_not_a_list_row() -> None:
    """Gemini CLI bootstrap dumps are not operator sessions."""
    _install_store()
    live = Path(f"gemini:{_CONTEXT_SID}")
    meta = require_adapter(live).load_meta(live)
    assert meta.title == ""
    assert "<session_context>" not in (meta.title or "")
    rows = list_session_catalog(include_host=True)
    assert _CONTEXT_SID not in {str(row["sessionId"]) for row in rows}


def test_running_session_is_not_complete() -> None:
    _install_store()
    assert_adapter_turn(Path(f"gemini:{_RUNNING_SID}"), "running")


def test_list_status_close_bookend_live_and_later_user(tmp_path: Path) -> None:
    def _conv(sid: str, messages: str) -> Path:
        path = tmp_path / f"session-2026-08-09T12-00-{sid}.jsonl"
        path.write_text(
            '{"sessionId":"'
            + sid
            + '","projectHash":"abc","kind":"main","startTime":"2026-08-09T12:00:00.000Z"}\n'
            '{"$set":{"messages":[' + messages + "]}}\n",
            encoding="utf-8",
        )
        return path

    closed = _conv(
        "aaaaaaaa-1111-4111-8111-0000000000c1",
        '{"id":"u1","type":"user","content":[{"text":"hi"}]},'
        '{"id":"g1","type":"gemini","content":[{"text":"ok"}]}',
    )
    bookend = _conv(
        "aaaaaaaa-1111-4111-8111-0000000000c2",
        '{"id":"u1","type":"user","content":[{"text":"hi"}]}',
    )
    live = _conv(
        "aaaaaaaa-1111-4111-8111-0000000000c3",
        '{"id":"u1","type":"user","content":[{"text":"hi"}]},'
        '{"id":"g1","type":"gemini","content":[{"text":"work"}],'
        '"toolCalls":[{"status":"executing"}]}',
    )
    later = _conv(
        "aaaaaaaa-1111-4111-8111-0000000000c4",
        '{"id":"g1","type":"gemini","content":[{"text":"ok"}]},'
        '{"id":"u2","type":"user","content":[{"text":"again"}]}',
    )
    assert_adapter_turn(closed, "complete")
    assert_adapter_turn(bookend, "—")
    assert_adapter_turn(live, "running")
    assert_adapter_turn(later, "—")


def test_overview_stats_count_timeline_tools() -> None:
    _install_store()
    ref = GeminiAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == GEMINI_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "Gemini CLI"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("run_shell_command", 0) >= 1


def test_timeline_user_tool_result() -> None:
    _install_store()
    ref = GeminiAdapter().ref_for_id(_SID)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types[0] == "turn_started"
    assert "user_message_chunk" in types
    assert "agent_thought_chunk" in types
    assert "tool_call" in types
    assert "tool_call_update" in types
    tool = next(e for e in events if e.event_type == "tool_call")
    assert tool.tool_name == "run_shell_command"
    assert tool.raw_input.as_str("command") == "echo GEMINI_PROBE_OK"
    result = next(e for e in events if e.event_type == "tool_call_update")
    assert "GEMINI_PROBE_OK" in result.content
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 5
    texts = " ".join(str(ev.get("content") or "") for ev in page["events"])
    assert "GEMINI_PROBE_OK" in texts


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = GeminiAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is not None
    dest = path.parent / "gemini.tar.gz"
    members = adapter.write_archive(found, dest)
    assert members == [f"{_SID}/{path.name}"]
    with tarfile.open(dest, "r:gz") as tf:
        assert tf.getnames() == members
    row = CatalogQueryRow(session_id=_SID, title="x", harness="gemini")
    assert row_matches_query(row, "harness:gemini")
    assert not row_matches_query(row, "harness:claude")


def test_jsonl_write_adds_new_gemini_session(tmp_path: Path) -> None:
    """A new chat jsonl under the Gemini store remetas so the session appears."""
    from anqa.control.daemon import apply_fs_catalog_events
    from anqa.session.catalog import SessionCatalogCache

    dest = _install_store()
    traces = tmp_path / "traces"
    traces.mkdir()
    cache = SessionCatalogCache(traces_path=traces, include_host=True, ttl=3600.0)
    cache.get(force=True)
    ids = {str(row["sessionId"]) for row in cache.get()}
    assert _SID in ids
    new_sid = "dddddddd-4444-4444-8444-000000000004"
    assert new_sid not in ids

    new = dest.parent / "session-2026-08-09T12-03-dddddddd.jsonl"
    new.write_text(
        '{"sessionId": "dddddddd-4444-4444-8444-000000000004", '
        '"projectHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", '
        '"startTime": "2026-08-09T12:03:00.000Z", '
        '"lastUpdated": "2026-08-09T12:03:00.000Z", "kind": "main"}\n'
        '{"$set": {"messages": [{"id": "u3", "timestamp": "2026-08-09T12:03:01.000Z", '
        '"type": "user", "content": [{"text": "new"}]}]}}\n',
        encoding="utf-8",
    )
    apply_fs_catalog_events(cache, [str(new)], [traces])
    assert cache._rows is not None
    ids = {str(row["sessionId"]) for row in cache._rows}
    assert new_sid in ids


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"gemini:{_SID}"), dest=dest)
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
    assert f"{_SID}/{_FIXTURE_FILE.name}" in members


def test_session_metadata_and_message_update(tmp_path: Path) -> None:
    """Gemini CLI 0.57 jsonl: session_metadata header plus message_update merge."""
    sid = "ffffffff-6666-4666-8666-000000000006"
    path = tmp_path / f"session-2026-08-31T12-00-{sid}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_metadata",
                "sessionId": sid,
                "projectHash": "abc",
                "kind": "main",
                "startTime": "2026-08-31T12:00:00.000Z",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "id": "u1",
                "content": [{"text": "Reply with GEMINI_META_OK"}],
                "timestamp": "2026-08-31T12:00:01.000Z",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "gemini",
                "id": "g1",
                "model": "gemini-2.5-pro",
                "content": [{"text": "ok"}],
                "timestamp": "2026-08-31T12:00:02.000Z",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "message_update",
                "id": "g1",
                "tokens": {"input": 10, "output": 5},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    adapter = GeminiAdapter()
    ref = adapter.bind_locator(path)
    assert ref is not None
    assert ref.session_id == sid
    meta = adapter.load_meta(ref)
    assert meta.title == "Reply with GEMINI_META_OK"
    assert meta.model_id == "gemini-2.5-pro"
    events = adapter.parse_timeline(ref)
    texts = [e.content for e in events if e.event_type == "agent_message_chunk"]
    assert texts == ["ok"]
