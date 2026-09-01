"""Pi adapter against the committed synthesized store fixture."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from anqa.harness.pi import PI_HARNESS_ID, PiAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

from .turn_status import assert_adapter_turn

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "pi" / "sessions"
_SID = "019fe000-0000-7000-8000-000000000001"
_RUNNING_SID = "019fe000-0000-7000-8000-000000000002"
_FIXTURE_FILE = _FIXTURE_ROOT / "tmp-probe" / f"2026-08-09T12-00-00-000Z_{_SID}.jsonl"


def _install_store() -> Path:
    dest = Path.home() / ".pi" / "agent" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "tmp-probe" / _FIXTURE_FILE.name


def test_discover_and_meta() -> None:
    path = _install_store()
    refs = PiAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert set(by_id) == {_SID, _RUNNING_SID}
    assert by_id[_SID].harness == PI_HARNESS_ID
    assert by_id[_SID].ref_string() == f"pi:{_SID}"
    assert by_id[_SID].locator == path.resolve()
    probe = Path(f"pi:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == PI_HARNESS_ID
    assert meta.title == "Reply with PI_PROBE_OK"
    assert meta.model_id == "xai/grok-4.5"
    assert meta.tool_call_count >= 1
    assert meta.list_status_label() == "complete"


def test_catalog_lists_pi_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert by_id[_SID]["harness"] == PI_HARNESS_ID
    assert by_id[_SID]["path"] == f"pi:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "running"


def test_last_tool_use_is_running() -> None:
    _install_store()
    assert_adapter_turn(Path(f"pi:{_RUNNING_SID}"), "running")


def test_list_status_close_bookend_and_later_user(tmp_path: Path) -> None:
    def _write(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    header = '{"type":"session","version":3,"id":"pi-x","timestamp":"2026-08-09T12:00:00.000Z"}\n'
    closed = _write(
        "closed.jsonl",
        header
        + '{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"ok"}],'
        '"stopReason":"stop"}}\n',
    )
    bookend = _write(
        "bookend.jsonl",
        header
        + '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"hi"}]}}\n',
    )
    later = _write(
        "later.jsonl",
        header
        + '{"type":"message","message":{"role":"assistant","stopReason":"stop"}}\n'
        + '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"again"}]}}\n',
    )
    inflight = _write(
        "inflight.jsonl",
        header + '{"type":"message","message":{"role":"assistant","stopReason":"toolUse",'
        '"content":[{"type":"toolCall","name":"bash","arguments":{"command":"echo x"}}]}}\n'
        + '{"type":"message","message":{"role":"toolResult","toolName":"bash",'
        '"content":[{"type":"text","text":"x"}]}}\n',
    )
    assert_adapter_turn(closed, "complete")
    assert_adapter_turn(bookend, "—")
    assert_adapter_turn(later, "—")
    assert_adapter_turn(inflight, "running")


def test_overview_stats_count_timeline_tools() -> None:
    _install_store()
    ref = PiAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == PI_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "Pi"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("bash", 0) >= 1


def test_timeline_user_tool_result() -> None:
    _install_store()
    ref = PiAdapter().ref_for_id(_SID)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types[0] == "turn_started"
    assert "user_message_chunk" in types
    assert "agent_thought_chunk" in types
    assert "tool_call" in types
    assert "tool_call_update" in types
    tool = next(e for e in events if e.event_type == "tool_call")
    assert tool.tool_name == "bash"
    assert tool.raw_input.as_str("command") == "echo PI_PROBE_OK"
    result = next(e for e in events if e.event_type == "tool_call_update")
    assert "PI_PROBE_OK" in result.content
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 5
    texts = " ".join(str(ev.get("content") or "") for ev in page["events"])
    assert "PI_PROBE_OK" in texts


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = PiAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is not None
    dest = path.parent / "pi.tar.gz"
    members = adapter.write_archive(found, dest)
    assert members == [f"{_SID}/{path.name}"]
    with tarfile.open(dest, "r:gz") as tf:
        assert tf.getnames() == members
    row = CatalogQueryRow(session_id=_SID, title="x", harness="pi")
    assert row_matches_query(row, "harness:pi")
    assert not row_matches_query(row, "harness:opencode")


def test_session_diff_empty_without_file_edits() -> None:
    """Pi probe session only ran bash, so Diff has no points."""
    from anqa.harness.views import session_diff

    _install_store()
    ref = PiAdapter().ref_for_id(_SID)
    assert ref is not None
    payload = session_diff(ref)
    assert payload["sessionId"] == _SID
    assert payload["points"] == []


def test_session_diff_uses_edit_and_write_tools(tmp_path: Path) -> None:
    """Diff pane for a Pi locator is built from edit/write tool calls."""
    from anqa.harness.views import session_diff

    path = tmp_path / "2026-08-09T12-00-00-000Z_019fe000-0000-7000-8000-00000000ed01.jsonl"
    path.write_text(
        '{"type":"session","version":3,"id":"019fe000-0000-7000-8000-00000000ed01",'
        '"timestamp":"2026-08-09T12:00:00.000Z","cwd":"/tmp/probe-ws"}\n'
        '{"type":"message","id":"u1","timestamp":"2026-08-09T12:00:01.000Z",'
        '"message":{"role":"user","content":[{"type":"text","text":"edit the file"}]}}\n'
        '{"type":"message","id":"a1","timestamp":"2026-08-09T12:00:02.000Z",'
        '"message":{"role":"assistant","content":['
        '{"type":"toolCall","id":"c1","name":"edit","arguments":'
        '{"path":"/tmp/probe-ws/hello.py","edits":[{"oldText":"return 1","newText":"return 2"}]}}'
        "]}}\n"
        '{"type":"message","id":"a2","timestamp":"2026-08-09T12:00:03.000Z",'
        '"message":{"role":"assistant","content":['
        '{"type":"toolCall","id":"c2","name":"write","arguments":'
        '{"path":"/tmp/probe-ws/NOTE.txt","content":"WS1\\n"}}'
        "]}}\n",
        encoding="utf-8",
    )
    ref = PiAdapter().bind_locator(path)
    assert ref is not None
    payload = session_diff(ref)
    assert payload["sessionId"] == "019fe000-0000-7000-8000-00000000ed01"
    assert payload["source"] == "search_replace"
    points = payload["points"]
    assert len(points) == 1
    paths = [str(f["path"]) for f in points[0]["files"]]
    assert paths == ["/tmp/probe-ws/NOTE.txt", "/tmp/probe-ws/hello.py"]
    unified = "\n".join(str(f["unified"]) for f in points[0]["files"])
    assert "return 2" in unified
    assert "WS1" in unified


def test_jsonl_write_is_a_list_rebuild_path() -> None:
    from anqa.control.daemon import CatalogWatchApply

    assert CatalogWatchApply.list_rebuild_path(Path("/store/tmp-probe/sess.jsonl")) is True
    assert CatalogWatchApply.list_rebuild_path(Path("/store/noise.bin")) is False


def test_jsonl_write_adds_new_pi_session(tmp_path: Path) -> None:
    """A new jsonl under the Pi store remetas so the session appears."""
    from anqa.control.daemon import apply_fs_catalog_events
    from anqa.session.catalog import SessionCatalogCache

    dest = _install_store()
    traces = tmp_path / "traces"
    traces.mkdir()
    cache = SessionCatalogCache(traces_path=traces, include_host=True, ttl=3600.0)
    cache.get(force=True)
    ids = {str(row["sessionId"]) for row in cache.get()}
    assert _SID in ids
    assert "019fe000-0000-7000-8000-000000000003" not in ids

    new = dest.parent / "2026-08-09T12-00-02-000Z_019fe000-0000-7000-8000-000000000003.jsonl"
    new.write_text(
        '{"type": "session", "version": 3, "id": "019fe000-0000-7000-8000-000000000003", '
        '"timestamp": "2026-08-09T12:00:20.000Z", "cwd": "/tmp/probe-ws"}\n'
        '{"type": "message", "id": "u3", "parentId": "019fe000-0000-7000-8000-000000000003", '
        '"timestamp": "2026-08-09T12:00:21.000Z", '
        '"message": {"role": "user", "content": [{"type": "text", "text": "new"}]}}\n',
        encoding="utf-8",
    )
    apply_fs_catalog_events(cache, [str(new)], [traces])
    assert cache._rows is not None
    ids = {str(row["sessionId"]) for row in cache._rows}
    assert "019fe000-0000-7000-8000-000000000003" in ids


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"pi:{_SID}"), dest=dest)
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
