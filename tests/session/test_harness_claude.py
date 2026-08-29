"""Claude adapter against the committed synthesized store fixture."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from anqa.harness.claude import CLAUDE_HARNESS_ID, ClaudeAdapter
from anqa.harness.registry import require_adapter
from anqa.harness.views import session_overview, session_timeline
from anqa.session.catalog import list_session_catalog
from anqa.session.export_bundle import export_session_bundle
from anqa.session.query import CatalogQueryRow, row_matches_query

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "harness" / "claude" / "projects"
_SID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000001"
_RUNNING_SID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000002"
_CHILD_SID = "explore-fixture"
_FIXTURE_FILE = _FIXTURE_ROOT / "-tmp-probe-ws" / f"{_SID}.jsonl"


def _install_store() -> Path:
    dest = Path.home() / ".claude" / "projects"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_FIXTURE_ROOT, dest, dirs_exist_ok=True)
    return dest / "-tmp-probe-ws" / _FIXTURE_FILE.name


def test_discover_and_meta() -> None:
    path = _install_store()
    refs = ClaudeAdapter().discover()
    by_id = {ref.session_id: ref for ref in refs}
    assert set(by_id) == {_SID, _RUNNING_SID}
    assert _CHILD_SID not in by_id
    assert by_id[_SID].harness == CLAUDE_HARNESS_ID
    assert by_id[_SID].ref_string() == f"claude:{_SID}"
    assert by_id[_SID].locator == path.resolve()
    probe = Path(f"claude:{_SID}")
    meta = require_adapter(probe).load_meta(probe)
    assert meta.harness == CLAUDE_HARNESS_ID
    assert meta.title == "Reply with CLAUDE_PROBE_OK"
    assert meta.model_id == "claude-opus-5"
    assert meta.harness_version == "2.1.251"
    assert meta.tool_call_count >= 1
    assert meta.has_subagents
    assert meta.subagent_count == 1
    assert meta.list_status_label() == "complete"


def test_catalog_lists_claude_sessions() -> None:
    _install_store()
    rows = list_session_catalog(include_host=True)
    by_id = {str(row["sessionId"]): row for row in rows}
    assert _SID in by_id
    assert _RUNNING_SID in by_id
    assert _CHILD_SID not in by_id
    assert by_id[_SID]["harness"] == CLAUDE_HARNESS_ID
    assert by_id[_SID]["path"] == f"claude:{_SID}"
    assert by_id[_SID]["status"] == "complete"
    assert by_id[_RUNNING_SID]["status"] == "running"


def test_running_session_is_not_complete() -> None:
    _install_store()
    live = Path(f"claude:{_RUNNING_SID}")
    meta = require_adapter(live).load_meta(live)
    assert meta.list_status_label() == "running"
    assert require_adapter(live).list_turn_outcome(live) == "running"


def test_overview_stats_count_timeline_tools() -> None:
    _install_store()
    ref = ClaudeAdapter().ref_for_id(_SID)
    assert ref is not None
    ov = session_overview(ref)
    assert ov["meta"]["harness"] == CLAUDE_HARNESS_ID
    assert ov["meta"]["harnessLabel"] == "Claude Code"
    stats = ov["stats"]
    types = {row["id"]: int(row["count"]) for row in stats["eventTypes"]}
    tools = {row["id"]: int(row["count"]) for row in stats["tools"]}
    assert types.get("tool_call", 0) >= 1
    assert tools.get("Bash", 0) >= 1


def test_timeline_user_tool_result() -> None:
    _install_store()
    ref = ClaudeAdapter().ref_for_id(_SID)
    assert ref is not None
    events = require_adapter(ref).parse_timeline(ref)
    types = [e.event_type for e in events]
    assert types[0] == "turn_started"
    assert "user_message_chunk" in types
    assert "agent_thought_chunk" in types
    assert "tool_call" in types
    assert "tool_call_update" in types
    tool = next(e for e in events if e.event_type == "tool_call" and e.tool_name == "Bash")
    assert tool.raw_input.as_str("command") == "echo CLAUDE_PROBE_OK"
    result = next(e for e in events if e.event_type == "tool_call_update" and e.tool_name == "Bash")
    assert "CLAUDE_PROBE_OK" in result.content
    page = session_timeline(ref)
    assert int(page["total"] or 0) >= 5
    texts = " ".join(str(ev.get("content") or "") for ev in page["events"])
    assert "CLAUDE_PROBE_OK" in texts


def test_agent_tool_emits_subagent_bookends() -> None:
    _install_store()
    adapter = ClaudeAdapter()
    child = adapter.ref_for_id(_CHILD_SID)
    assert child is not None
    assert child.session_id == _CHILD_SID
    probe = Path(f"claude:{_SID}")
    events = require_adapter(probe).parse_timeline(probe)
    types = [e.event_type for e in events]
    assert "subagent_spawned" in types
    assert "subagent_finished" in types
    spawn = next(e for e in events if e.event_type == "subagent_spawned")
    assert spawn.raw_input.as_str("child_session_id") == _CHILD_SID
    assert spawn.raw_input.as_str("subagent_type") == "explore"
    bound = adapter.ref_for_id(_SID)
    assert bound is not None
    ov = session_overview(bound)
    runs = ov["turns"]["subagentRuns"]
    assert runs
    assert runs[0]["childSessionId"] == _CHILD_SID
    assert runs[0]["openable"] is True
    assert runs[0]["childPath"] == f"claude:{_CHILD_SID}"


def test_ref_for_id_and_query() -> None:
    path = _install_store()
    adapter = ClaudeAdapter()
    found = adapter.ref_for_id(_SID)
    assert found is not None
    assert found.session_id == _SID
    assert adapter.looks_like(found)
    assert adapter.bind_locator(path) is not None
    dest = path.parent / "claude.tar.gz"
    members = adapter.write_archive(found, dest)
    assert f"{_SID}/{path.name}" in members
    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
    assert set(members) == names
    row = CatalogQueryRow(session_id=_SID, title="x", harness="claude")
    assert row_matches_query(row, "harness:claude")
    assert not row_matches_query(row, "harness:pi")


def test_jsonl_write_adds_new_claude_session(tmp_path: Path) -> None:
    """A new jsonl under the Claude store remetas so the session appears."""
    from anqa.control.daemon import apply_fs_catalog_events
    from anqa.session.catalog import SessionCatalogCache

    dest = _install_store()
    traces = tmp_path / "traces"
    traces.mkdir()
    cache = SessionCatalogCache(traces_path=traces, include_host=True, ttl=3600.0)
    cache.get(force=True)
    ids = {str(row["sessionId"]) for row in cache.get()}
    assert _SID in ids
    new_sid = "aaaaaaaa-bbbb-4ccc-8ddd-000000000003"
    assert new_sid not in ids

    new = dest.parent / f"{new_sid}.jsonl"
    new.write_text(
        '{"type": "user", "sessionId": "aaaaaaaa-bbbb-4ccc-8ddd-000000000003", '
        '"timestamp": "2026-08-09T12:00:20.000Z", "cwd": "/tmp/probe-ws", '
        '"version": "2.1.251", '
        '"message": {"role": "user", "content": "new"}}\n',
        encoding="utf-8",
    )
    apply_fs_catalog_events(cache, [str(new)], [traces])
    assert cache._rows is not None
    ids = {str(row["sessionId"]) for row in cache._rows}
    assert new_sid in ids


def test_export_bundle_from_harness_ref(tmp_path: Path) -> None:
    _install_store()
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(Path(f"claude:{_SID}"), dest=dest)
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
