"""Resume seed: locate a leftover fork parent."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from anqa.session.resume import (
    RESUME_SEED_DIRNAME,
    can_resume_session,
    fork_parent_session_dir,
    is_resume_seed_path,
)


def _fake_session(root: Path, *, sid: str = "sess-abc") -> Path:
    token = "%2Fworkspace"
    session = root / "traces" / "anqa-old" / token / sid
    session.mkdir(parents=True)
    (session / "chat_history.jsonl").write_text(
        '{"role":"user","content":"hi"}\n', encoding="utf-8"
    )
    (session / "summary.json").write_text("{}", encoding="utf-8")
    (session.parent / "prompt_history.jsonl").write_text("p\n", encoding="utf-8")
    return session


def _seed_parent(vol: Path, source: Path) -> Path:
    token = source.parent.name
    dest = vol / RESUME_SEED_DIRNAME / token / source.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    live = vol / token / source.name
    live.parent.mkdir(parents=True, exist_ok=True)
    if live.exists() or live.is_symlink():
        live.unlink()
    live.symlink_to(dest)
    return dest


def _write_launch_meta(vol: Path, *, parent: str, child: str) -> None:
    (vol / "anqa-launch.json").write_text(
        json.dumps(
            {
                "model": "v9",
                "resume_parent_session_id": parent,
                "resume_fork_session_id": child,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_can_resume_session(tmp_path: Path) -> None:
    s = _fake_session(tmp_path)
    assert can_resume_session(s) is True
    assert can_resume_session(tmp_path / "missing") is False


def test_find_sessions_skips_resume_seed_and_live_link(tmp_path: Path) -> None:
    """Substrate and its live symlink are not operator session rows."""
    from anqa.harness.grok_parse import find_sessions

    source = _fake_session(tmp_path)
    dest_vol = tmp_path / "traces" / "anqa-new"
    dest_vol.mkdir(parents=True)
    seed = _seed_parent(dest_vol, source)
    live = dest_vol / "%2Fworkspace" / "sess-abc"
    child = dest_vol / "%2Fworkspace" / "forked-child-id"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")

    found = {p.name for p in find_sessions(dest_vol)}
    assert "sess-abc" not in found
    assert "forked-child-id" in found
    assert is_resume_seed_path(seed) is True
    assert is_resume_seed_path(live) is True


def test_flat_session_is_resumeable(tmp_path: Path) -> None:
    flat = tmp_path / "sess-flat"
    flat.mkdir()
    (flat / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert can_resume_session(flat) is True


def _append_update(
    updates_path: Path,
    *,
    session_id: str,
    timestamp: int,
    session_update: str,
    text: str,
) -> None:
    row = {
        "timestamp": timestamp,
        "method": "session/update",
        "params": {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": session_update,
                "content": {"type": "text", "text": text},
            },
        },
    }
    prev_u = updates_path.read_text(encoding="utf-8") if updates_path.is_file() else ""
    updates_path.write_text(prev_u + json.dumps(row) + "\n", encoding="utf-8")


def _write_completed_turn(
    events_path: Path,
    updates_path: Path,
    *,
    session_id: str,
    turn_number: int,
    text: str,
    t0: int,
) -> None:
    """One turn with interleaved marker/update timestamps (epoch seconds)."""
    started = {
        "ts": t0,
        "type": "turn_started",
        "session_id": session_id,
        "turn_number": turn_number,
        "model_id": "v9-test",
    }
    ended = {"ts": t0 + 3, "type": "turn_ended", "outcome": "completed"}
    prev = events_path.read_text(encoding="utf-8") if events_path.is_file() else ""
    events_path.write_text(
        prev + json.dumps(started) + "\n" + json.dumps(ended) + "\n",
        encoding="utf-8",
    )
    # User + assistant so consecutive multi-turn fixtures do not coalesce users.
    _append_update(
        updates_path,
        session_id=session_id,
        timestamp=t0 + 1,
        session_update="user_message_chunk",
        text=text,
    )
    _append_update(
        updates_path,
        session_id=session_id,
        timestamp=t0 + 2,
        session_update="agent_message_chunk",
        text=f"reply-{turn_number}",
    )


def test_fork_parent_session_dir_resolves_seed(tmp_path: Path) -> None:
    source = _fake_session(tmp_path, sid="parent-1")
    (source / "events.jsonl").write_text("{}\n", encoding="utf-8")
    vol = tmp_path / "traces" / "anqa-fork"
    vol.mkdir(parents=True)
    _seed_parent(vol, source)
    _write_launch_meta(vol, parent="parent-1", child="child-1")
    child = vol / "%2Fworkspace" / "child-1"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")

    parent = fork_parent_session_dir(child)
    assert parent is not None
    assert parent.name == "parent-1"
    assert RESUME_SEED_DIRNAME in parent.parts
    assert fork_parent_session_dir(parent) is None
    assert fork_parent_session_dir(vol / "%2Fworkspace" / "other") is None


def test_parse_timeline_inherits_parent_turns_on_fork(tmp_path: Path) -> None:
    """Fork child with only turn_number=1 still shows parent turn 0 in the timeline."""
    from anqa.harness.grok_parse import parse_timeline
    from anqa.session.turns import segment_timeline_turns

    source = _fake_session(tmp_path, sid="parent-turns")
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-turns",
        turn_number=0,
        text="parent prompt",
        t0=1_700_000_000,
    )

    vol = tmp_path / "traces" / "anqa-fork-turns"
    vol.mkdir(parents=True)
    _seed_parent(vol, source)
    _write_launch_meta(vol, parent="parent-turns", child="child-turns")
    child = vol / "%2Fworkspace" / "child-turns"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")
    _write_completed_turn(
        child / "events.jsonl",
        child / "updates.jsonl",
        session_id="child-turns",
        turn_number=1,
        text="fork continuation",
        t0=1_700_003_600,
    )

    tl = parse_timeline(child)
    turns = segment_timeline_turns(tl)
    assert len(turns) == 2
    assert turns[0].turn_number == 0
    assert turns[1].turn_number == 1
    contents = " ".join(e.content for e in tl)
    assert "parent prompt" in contents
    assert "fork continuation" in contents


def test_parse_timeline_fork_strips_restamped_parent_replay(tmp_path: Path) -> None:
    """Child updates replaying parent tools must not appear again under turn 1."""
    from anqa.harness.grok_parse import parse_timeline
    from anqa.session.turns import segment_timeline_turns

    source = _fake_session(tmp_path, sid="parent-replay")
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-replay",
        turn_number=0,
        text="parent prompt",
        t0=1_700_000_000,
    )
    # Parent also has a tool_call so the restamp is unambiguous.
    tool_row = {
        "timestamp": 1_700_000_001,
        "method": "session/update",
        "params": {
            "sessionId": "parent-replay",
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-parent-1",
                "title": "read_file",
                "rawInput": {"target_file": "a.py"},
            },
        },
    }
    # Insert tool between user and agent by rewriting updates in order.
    updates = [
        {
            "timestamp": 1_700_000_001,
            "method": "session/update",
            "params": {
                "sessionId": "parent-replay",
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "parent prompt"},
                },
            },
        },
        tool_row,
        {
            "timestamp": 1_700_000_002,
            "method": "session/update",
            "params": {
                "sessionId": "parent-replay",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "reply-0"},
                },
            },
        },
    ]
    (source / "updates.jsonl").write_text(
        "\n".join(json.dumps(r) for r in updates) + "\n", encoding="utf-8"
    )
    (source / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": 1_700_000_000,
                "type": "turn_started",
                "session_id": "parent-replay",
                "turn_number": 0,
                "model_id": "v9-test",
            }
        )
        + "\n"
        + json.dumps({"ts": 1_700_000_003, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )

    vol = tmp_path / "traces" / "anqa-fork-replay"
    vol.mkdir(parents=True)
    _seed_parent(vol, source)
    _write_launch_meta(vol, parent="parent-replay", child="child-replay")
    child = vol / "%2Fworkspace" / "child-replay"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")
    # Child: restamped full parent replay + continuation, single turn_number=1.
    restamp_ts = 1_700_010_000
    child_updates = []
    for row in updates:
        r = json.loads(json.dumps(row))
        r["timestamp"] = restamp_ts
        r["params"]["sessionId"] = "child-replay"
        child_updates.append(r)
    child_updates.append(
        {
            "timestamp": restamp_ts + 10,
            "method": "session/update",
            "params": {
                "sessionId": "child-replay",
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "fork continuation"},
                },
            },
        }
    )
    child_updates.append(
        {
            "timestamp": restamp_ts + 11,
            "method": "session/update",
            "params": {
                "sessionId": "child-replay",
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-child-new",
                    "title": "run_terminal_command",
                    "rawInput": {"command": "echo hi"},
                },
            },
        }
    )
    (child / "updates.jsonl").write_text(
        "\n".join(json.dumps(r) for r in child_updates) + "\n", encoding="utf-8"
    )
    (child / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": restamp_ts,
                "type": "turn_started",
                "session_id": "child-replay",
                "turn_number": 1,
                "model_id": "v9-test",
            }
        )
        + "\n"
        + json.dumps({"ts": restamp_ts + 12, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )

    tl = parse_timeline(child)
    turns = segment_timeline_turns(tl)
    assert len(turns) == 2
    t0_tools = [e.tool_call_id for e in turns[0].events if e.tool_call_id]
    t1_tools = [e.tool_call_id for e in turns[1].events if e.tool_call_id]
    assert "call-parent-1" in t0_tools
    assert "call-parent-1" not in t1_tools
    assert "call-child-new" in t1_tools
    assert "fork continuation" in " ".join(e.content for e in turns[1].events)
    assert "parent prompt" not in " ".join(e.content for e in turns[1].events)


def test_parse_timeline_fork_empty_events_keeps_parent_turns(tmp_path: Path) -> None:
    """Child with empty events and re-stamped updates does not collapse parent turns."""
    from anqa.harness.grok_parse import parse_timeline
    from anqa.session.turns import segment_timeline_turns

    source = _fake_session(tmp_path, sid="parent-multi")
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-multi",
        turn_number=0,
        text="turn0",
        t0=1_700_000_000,
    )
    _write_completed_turn(
        source / "events.jsonl",
        source / "updates.jsonl",
        session_id="parent-multi",
        turn_number=1,
        text="turn1",
        t0=1_700_000_100,
    )

    vol = tmp_path / "traces" / "anqa-fork-empty-ev"
    vol.mkdir(parents=True)
    _seed_parent(vol, source)
    _write_launch_meta(vol, parent="parent-multi", child="child-empty-ev")
    child = vol / "%2Fworkspace" / "child-empty-ev"
    child.mkdir(parents=True)
    (child / "summary.json").write_text("{}", encoding="utf-8")
    (child / "events.jsonl").write_text("", encoding="utf-8")
    # Re-timestamped full copy of parent updates (Grok fork pattern).
    restamped = []
    for line in (source / "updates.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["timestamp"] = 1_700_010_000
        row["params"]["sessionId"] = "child-empty-ev"
        restamped.append(json.dumps(row))
    (child / "updates.jsonl").write_text("\n".join(restamped) + "\n", encoding="utf-8")

    parent_turns = len(
        segment_timeline_turns(
            parse_timeline(vol / RESUME_SEED_DIRNAME / "%2Fworkspace" / "parent-multi")
        )
    )
    assert parent_turns == 2
    child_turns = segment_timeline_turns(parse_timeline(child))
    assert len(child_turns) == 2
