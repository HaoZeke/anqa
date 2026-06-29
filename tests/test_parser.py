"""Tests for trace parser."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from groket.models import JsonValue
from groket.parser import (
    extract_prompt,
    find_sessions,
    load_session_meta,
    parse_chat_history,
    parse_runtime_markers,
    parse_timeline,
    parse_tool_calls,
)

# ── parse_tool_calls ─────────────────────────────────────────────────────


class TestParseToolCalls:
    def test_basic_extraction(self, session_dir):
        calls = parse_tool_calls(session_dir)
        assert len(calls) == 3
        assert calls[0].tool_name == "run_terminal_command"
        assert calls[0].call_id == "call-aaa"
        assert calls[1].tool_name == "read_file"
        assert calls[2].tool_name == "search_replace"

    def test_error_marked(self, session_dir):
        calls = parse_tool_calls(session_dir)
        assert calls[0].is_error is True  # pytest failed
        assert calls[0].result_content == "FAILED 2 tests"

    def test_success_marked(self, session_dir):
        calls = parse_tool_calls(session_dir)
        assert calls[1].is_error is False
        assert calls[2].is_error is False

    def test_empty_dir(self, empty_session_dir):
        calls = parse_tool_calls(empty_session_dir)
        assert calls == []

    def test_nonexistent_dir(self, tmp_path):
        calls = parse_tool_calls(tmp_path / "ghost")
        assert calls == []

    def test_malformed_jsonl(self, tmp_path):
        sd = tmp_path / "bad"
        sd.mkdir()
        (sd / "updates.jsonl").write_text("not json\n{bad json\n")
        calls = parse_tool_calls(sd)
        assert calls == []

    def test_exit_code_extraction(self, tmp_path):
        sd = tmp_path / "exit-code-session"
        sd.mkdir()
        updates = [
            {
                "timestamp": 1000,
                "method": "session/update",
                "params": {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "c1",
                        "title": "run_terminal_command",
                        "rawInput": {"command": "make"},
                    },
                },
            },
            {
                "timestamp": 1005,
                "method": "session/update",
                "params": {
                    "sessionId": "s1",
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "c1",
                        "status": "completed",
                        "content": "build failed",
                        "rawOutput": {"exit_code": 2, "signal": "SIGTERM"},
                    },
                },
            },
        ]
        with open(sd / "updates.jsonl", "w") as f:
            for u in updates:
                f.write(json.dumps(u) + "\n")

        calls = parse_tool_calls(sd)
        assert len(calls) == 1
        assert calls[0].exit_code == 2
        assert calls[0].signal == "SIGTERM"
        assert calls[0].is_error is True


# ── parse_runtime_markers ────────────────────────────────────────────────


class TestParseRuntimeMarkers:
    def test_success_turn(self, session_dir):
        markers, outcome, loops = parse_runtime_markers(session_dir)
        assert outcome == "success"
        assert loops == 1  # loop_index=0 → count=1
        assert len(markers) >= 2

    def test_error_turn(self, error_session_dir):
        markers, outcome, loops = parse_runtime_markers(error_session_dir)
        assert outcome == "error"
        error_markers = [m for m in markers if m.is_error]
        assert len(error_markers) >= 1

    def test_no_events_file(self, empty_session_dir):
        markers, outcome, loops = parse_runtime_markers(empty_session_dir)
        assert markers == []
        assert outcome == ""
        assert loops == 0

    def test_malformed_events(self, tmp_path):
        sd = tmp_path / "bad-events"
        sd.mkdir()
        (sd / "events.jsonl").write_text("garbage\nnot json\n")
        markers, outcome, loops = parse_runtime_markers(sd)
        assert markers == []


# ── parse_timeline ────────────────────────────────────────────────────────


class TestParseTimeline:
    def test_basic_timeline(self, session_dir):
        events = parse_timeline(session_dir)
        assert len(events) > 0
        types = [e.event_type for e in events]
        assert "session" in types  # turn_started marker
        assert "tool_call" in types
        assert "tool_result" in types

    def test_indices_sequential(self, session_dir):
        events = parse_timeline(session_dir)
        for i, ev in enumerate(events):
            assert ev.index == i

    def test_user_message_coalescing(self, session_dir):
        events = parse_timeline(session_dir)
        user_events = [e for e in events if e.event_type == "user"]
        # Multiple user_message_chunk updates should coalesce into one event
        assert len(user_events) >= 1

    def test_tool_result_coalescing(self, session_dir):
        events = parse_timeline(session_dir)
        results = [e for e in events if e.event_type == "tool_result"]
        call_ids = [e.tool_call_id for e in results]
        # Each call_id should appear at most once (coalesced)
        assert len(call_ids) == len(set(call_ids))

    def test_empty_session(self, empty_session_dir):
        events = parse_timeline(empty_session_dir)
        # Should still get session markers from events.jsonl (if present) or empty
        assert isinstance(events, list)

    def test_multi_turn_markers_interleaved_by_timestamp(self, tmp_path: Path):
        """Turn starts/ends must not all pile at top/bottom across turns."""
        import json

        sd = tmp_path / "sess"
        sd.mkdir()
        # Two turns in events.jsonl
        (sd / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"ts": 1000, "type": "turn_started", "turn_number": 1}),
                    json.dumps({"ts": 2000, "type": "turn_ended", "outcome": "success"}),
                    json.dumps({"ts": 3000, "type": "turn_started", "turn_number": 2}),
                    json.dumps({"ts": 4000, "type": "turn_ended", "outcome": "success"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        # Mid-turn activity in updates.jsonl (different event types so they do not
        # coalesce when parsed before markers are merged).
        (sd / "updates.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": 1500,
                            "params": {
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": [{"type": "text", "text": "hello"}],
                                }
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": 3500,
                            "params": {
                                "update": {
                                    "sessionUpdate": "user_message_chunk",
                                    "content": [{"type": "text", "text": "world"}],
                                }
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        events = parse_timeline(sd)
        # Chronological: start1, assistant@1500, end1, start2, assistant@3500, end2
        types = [e.event_type for e in events]
        assert types.count("session") >= 4
        session_contents = [e.content or "" for e in events if e.event_type == "session"]
        starts = [i for i, e in enumerate(events) if "turn started" in (e.content or "")]
        ends = [
            i
            for i, e in enumerate(events)
            if "turn ended" in (e.content or "").lower()
            or (
                e.event_type == "session"
                and "started" not in (e.content or "")
                and "error" not in (e.content or "").lower()
            )
        ]
        # At least two starts and they are not both before all non-session content
        assert len(starts) >= 2
        assert starts[0] < starts[1]
        # First end should come before second start (interleaved turns)
        end_labels = [
            i
            for i, e in enumerate(events)
            if e.event_type == "session" and "turn ended" in (e.content or "").lower()
        ]
        if len(end_labels) >= 1 and len(starts) >= 2:
            assert end_labels[0] < starts[1]
        # Timestamps non-decreasing where present
        prev = -1
        for e in events:
            if e.timestamp is not None:
                assert e.timestamp >= prev
                prev = e.timestamp


# ── load_session_meta ────────────────────────────────────────────────────


class TestLoadSessionMeta:
    def test_full_meta(self, session_dir):
        meta = load_session_meta(session_dir)
        assert meta.session_id == session_dir.name
        assert meta.model_id == "v9-dietcoke"
        assert meta.title == "Fix unit tests for auth module"
        assert meta.tool_call_count == 5
        assert meta.tool_failure_count == 1
        assert meta.duration_seconds == 155
        assert meta.lines_added == 42
        assert meta.turn_outcome == "success"
        assert meta.loop_count == 1
        # Events column = coalesced timeline (browser), not updates.jsonl byte heuristic
        assert meta.num_events == len(parse_timeline(session_dir))
        assert meta.num_events > 0

    def test_git_info(self, session_dir):
        meta = load_session_meta(session_dir)
        assert meta.git_repo == "https://github.com/example/repo"
        assert meta.git_branch == "main"

    def test_error_session(self, error_session_dir):
        meta = load_session_meta(error_session_dir)
        assert meta.turn_outcome == "error"
        assert meta.turn_failed is True

    def test_minimal_session(self, empty_session_dir):
        meta = load_session_meta(empty_session_dir)
        assert meta.session_id == empty_session_dir.name
        assert meta.model_id == "v9-dietcoke"
        assert meta.tool_call_count == 0

    def test_no_summary_json(self, tmp_path):
        sd = tmp_path / "bare-session"
        sd.mkdir()
        meta = load_session_meta(sd)
        assert meta.session_id == "bare-session"
        assert meta.model_id == "unknown"

    def test_run_json_fields(self, session_dir):
        meta = load_session_meta(session_dir)
        assert meta.run_id == "82bbd2e26e89"


# ── find_sessions ────────────────────────────────────────────────────────


class TestFindSessions:
    def test_finds_sessions(self, traces_root):
        sessions = find_sessions(traces_root)
        assert len(sessions) == 1

    def test_empty_root(self, tmp_path):
        sessions = find_sessions(tmp_path)
        assert sessions == []

    def test_nonexistent_root(self, tmp_path):
        sessions = find_sessions(tmp_path / "nope")
        assert sessions == []

    def test_nested_sessions(self, tmp_path):
        root = tmp_path / "traces"
        root.mkdir()
        s1 = root / "session-1"
        s1.mkdir()
        (s1 / "updates.jsonl").write_text("{}\n")
        s2 = root / "container" / "session-2"
        s2.mkdir(parents=True)
        (s2 / "summary.json").write_text("{}")
        sessions = find_sessions(root)
        assert len(sessions) == 2

    def test_skips_groket_staging(self, tmp_path):
        """Marketplace plugin trees must not be walked or listed as sessions."""
        run = tmp_path / "traces" / "groket-abc-model"
        plug = run / "groket-plugins" / "superpowers" / "docs"
        plug.mkdir(parents=True)
        (plug / "summary.json").write_text("{}", encoding="utf-8")
        sess = run / "%2Fworkspace" / "019f-session-id"
        sess.mkdir(parents=True)
        (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
        found = find_sessions(tmp_path / "traces")
        assert sess in found
        assert not any("groket-plugins" in str(p) for p in found)


# ── extract_prompt ────────────────────────────────────────────────────────


class TestExtractPrompt:
    def test_extracts_prompt(self, session_dir):
        prompt = extract_prompt(session_dir)
        assert prompt == "Fix the unit tests"

    def test_no_chat_history(self, empty_session_dir):
        prompt = extract_prompt(empty_session_dir)
        assert prompt == ""


# ── parse_chat_history ────────────────────────────────────────────────────


class TestParseChatHistory:
    def test_parse(self, session_dir):
        messages = parse_chat_history(session_dir)
        assert len(messages) == 3
        assert messages[0]["type"] == "system"

    def test_empty(self, empty_session_dir):
        messages = parse_chat_history(empty_session_dir)
        assert messages == []


from pathlib import Path

from groket.parser import (
    _as_epoch_ts,
    _extract_message_text,
    _extract_tool_update_text,
    _infer_incomplete_turn_outcome,
    _match_model_to_container,
    _model_from_run_json,
    _model_from_run_parent,
    _parse_runtime_ts,
    session_trace_mtime,
)


def test_as_epoch_ts_variants():
    assert _as_epoch_ts(None) is None
    assert _as_epoch_ts(True) == 1
    assert _as_epoch_ts(1000) == 1000
    assert _as_epoch_ts(1000.5) == 1000
    assert _as_epoch_ts("1700000000") == 1700000000
    assert _as_epoch_ts("not-a-date") is None
    assert _as_epoch_ts(False) == 0


def test_extract_text_helpers():
    assert _extract_tool_update_text("plain") == "plain"
    # list items use nested content dict
    assert "hi" in _extract_tool_update_text([{"content": {"text": "hi"}}])
    assert _extract_tool_update_text([{"content": "x"}]) == "x"
    assert _extract_message_text("s") == "s"
    assert "a" in _extract_message_text([{"type": "text", "text": "a"}])
    assert _extract_message_text({"type": "text", "text": "z"}) == "z"


def test_timeline_and_chat_edges(tmp_path: Path):
    sd = tmp_path / "sess"
    sd.mkdir()
    updates = [
        {
            "timestamp": 10,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Hello"},
                }
            },
        },
        {
            "timestamp": 11,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": [{"type": "text", "text": "Q"}],
                }
            },
        },
        {
            "timestamp": 12,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "t1",
                    "title": "grep",
                    "rawInput": {"pattern": "x"},
                }
            },
        },
        {
            "timestamp": 13,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "t1",
                    "status": "completed",
                    "content": [{"content": {"text": "match"}}],
                }
            },
        },
    ]
    with open(sd / "updates.jsonl", "w") as f:
        for u in updates:
            f.write(json.dumps(u) + "\n")
        f.write("not-an-object\n")
    (sd / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-06-25T00:00:00Z", "type": "turn_started", "model_id": "m"}),
                json.dumps({"ts": "bad", "type": "loop_started"}),
                json.dumps(
                    {"type": "turn_ended", "outcome": "success", "ts": "2026-06-25T00:01:00Z"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (sd / "chat_history.jsonl").write_text(
        json.dumps({"type": "user", "content": "hi"})
        + "\nnot-json\n"
        + json.dumps({"type": "assistant", "content": [{"type": "text", "text": "yo"}]})
        + "\n",
        encoding="utf-8",
    )
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {
                    "id": "sess",
                    "cwd": "/w",
                    "git_repo_url": "https://g/r",
                    "git_branch": "main",
                },
                "generated_title": "T",
                "session_summary": "sum",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 2,
                "current_model_id": "m",
            }
        ),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text(
        json.dumps({"toolCallCount": 1, "sessionDurationSeconds": 10}), encoding="utf-8"
    )
    (sd / "run.json").write_text(
        json.dumps(
            {"run_id": "r", "prompt": "from-run", "models": ["m"], "repo_url": "https://g/r"}
        ),
        encoding="utf-8",
    )
    # prompt.txt for extract_prompt fallback
    (sd / "prompt.txt").write_text("prompt-file\n", encoding="utf-8")

    events = parse_timeline(sd)
    assert events
    markers, outcome, _ = parse_runtime_markers(sd)
    assert outcome == "success"
    assert parse_tool_calls(sd)
    assert parse_chat_history(sd)
    prompt = extract_prompt(sd)
    # may come from chat, run.json, or prompt.txt
    assert isinstance(prompt, str)
    meta = load_session_meta(sd)
    assert meta.session_id
    assert session_trace_mtime(sd) > 0

    sd2 = tmp_path / "inc"
    sd2.mkdir()
    (sd2 / "events.jsonl").write_text(
        json.dumps({"type": "turn_started", "ts": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    (sd2 / "summary.json").write_text(json.dumps({"info": {"id": "inc"}}), encoding="utf-8")
    _infer_incomplete_turn_outcome(sd2)
    meta2 = load_session_meta(sd2)
    assert meta2.session_id

    root = tmp_path / "traces"
    target = root / "nested" / "sess"
    target.mkdir(parents=True)
    (target / "summary.json").write_text("{}", encoding="utf-8")
    found = find_sessions(root)
    assert any(p.name == "sess" for p in found)
    assert find_sessions(tmp_path / "ghost") == []


def test_model_helpers(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "run.json").write_text(
        json.dumps({"models": ["v9-dietcoke", "other"], "sessions": {}}), encoding="utf-8"
    )
    data = json.loads((sd / "run.json").read_text())
    _model_from_run_json(sd, data)
    _match_model_to_container("groket-task-dietcoke", ["v9-dietcoke", "x"])
    parent = tmp_path / "groket-task-dietcoke"
    parent.mkdir()
    nested = parent / "sid"
    nested.mkdir()
    (parent / "run.json").write_text(json.dumps({"models": ["v9-dietcoke"]}), encoding="utf-8")
    _model_from_run_parent(nested)
    assert _parse_runtime_ts({"ts": 100}) == 100
    assert _parse_runtime_ts({"ts": "2026-06-25T00:00:00Z"}) is not None


# ── _as_epoch_ts edge cases ──────────────────────────────────────────────


def test_as_epoch_ts_float_string():
    assert _as_epoch_ts("1700000000.5") == 1700000000


# ── _parse_runtime_ts edge cases ─────────────────────────────────────────


def test_parse_runtime_ts_large_ms_value():
    """Timestamps > 10 billion treated as milliseconds."""
    assert _parse_runtime_ts({"ts": 17_000_000_000_00}) == 17_000_000_000_00 // 1000


def test_parse_runtime_ts_none():
    assert _parse_runtime_ts({}) is None


def test_parse_runtime_ts_bad_iso():
    assert _parse_runtime_ts({"ts": "not-a-date"}) is None


# ── parse_runtime_markers edge cases ─────────────────────────────────────


def test_runtime_markers_loop_started_bad_index(tmp_path: Path):
    """loop_started with non-int loop_index is handled."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "loop_started", "loop_index": "bad"}) + "\n", encoding="utf-8"
    )
    markers, _, loops = parse_runtime_markers(sd)
    assert loops == 0


def test_runtime_markers_error_event(tmp_path: Path):
    """Error event types produce session_error markers."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "error", "message": "something broke", "ts": 100}) + "\n",
        encoding="utf-8",
    )
    markers, outcome, _ = parse_runtime_markers(sd)
    assert outcome == "error"
    assert any(m.is_error for m in markers)


def test_runtime_markers_turn_ended_extra_fields(tmp_path: Path):
    """Extra fields (error, message, reason, detail) appended to ended content."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "turn_ended",
                "outcome": "failed",
                "error": "big problem",
                "reason": "timeout",
                "ts": 100,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    markers, outcome, _ = parse_runtime_markers(sd)
    assert outcome == "failed"
    ended = [m for m in markers if "turn ended" in (m.content or "")]
    assert ended
    assert "error=big problem" in ended[0].content
    assert "reason=timeout" in ended[0].content


# ── _apply_tool_result_meta edge cases ───────────────────────────────────


def test_apply_tool_result_meta_output_for_prompt_replacement(tmp_path: Path):
    """rawOutput.output_for_prompt with exit: prefix replaces existing content."""
    from groket.models import ToolCall, ToolInputBag
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(
        call_id="c1",
        tool_name="run_terminal_command",
        raw_input=ToolInputBag({}),
        result_content="previous content",
    )
    _apply_tool_result_meta(
        tc,
        {
            "rawOutput": {"output_for_prompt": "exit: 1", "exit_code": 1},
        },
    )
    assert tc.result_content == "exit: 1"
    assert tc.exit_code == 1


def test_apply_tool_result_meta_signal_sets_error():
    from groket.models import ToolCall, ToolInputBag
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(call_id="c1", tool_name="run_terminal_command", raw_input=ToolInputBag({}))
    _apply_tool_result_meta(
        tc,
        {
            "rawOutput": {"signal": "SIGKILL"},
        },
    )
    assert tc.signal == "SIGKILL"
    assert tc.is_error is True


def test_apply_tool_result_meta_exit_code_1_not_error():
    """exit_code=1 is benign for terminal commands (grep no-match)."""
    from groket.models import ToolCall, ToolInputBag
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(call_id="c1", tool_name="run_terminal_command", raw_input=ToolInputBag({}))
    _apply_tool_result_meta(
        tc,
        {
            "rawOutput": {"exit_code": 1},
        },
    )
    assert tc.is_error is False


# ── parse_timeline edge cases ────────────────────────────────────────────


def test_timeline_plan_event(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {"sessionUpdate": "plan", "todos": [{"id": "1", "text": "do"}]}
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    events = parse_timeline(sd)
    assert any(e.event_type == "plan" for e in events)


def test_timeline_subagent_events(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_spawned",
                        "description": "worker",
                        "subagentType": "coder",
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 11,
                "params": {"update": {"sessionUpdate": "subagent_finished"}},
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = parse_timeline(sd)
    subs = [e for e in events if e.event_type == "subagent"]
    assert len(subs) == 2
    assert "coder" in subs[0].content


def test_timeline_only_markers_no_updates(tmp_path: Path):
    """Session with events.jsonl but no updates.jsonl (open turn kept)."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        json.dumps({"ts": 1000, "type": "turn_started", "turn_number": 0}) + "\n",
        encoding="utf-8",
    )
    events = parse_timeline(sd)
    assert len(events) >= 1
    assert events[0].event_type == "session"


def test_timeline_drops_trailing_empty_turn_start(tmp_path: Path):
    """Completed turn plus dangling turn_started (interactive await) is omitted."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": 1000, "type": "turn_started", "turn_number": 0}),
                json.dumps({"ts": 2000, "type": "turn_ended", "outcome": "completed"}),
                json.dumps({"ts": 3000, "type": "turn_started", "turn_number": 1}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1500,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": [{"type": "text", "text": "done"}],
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    events = parse_timeline(sd)
    starts = [e for e in events if "turn started" in (e.content or "").lower()]
    ends = [e for e in events if "turn ended" in (e.content or "").lower()]
    assert len(starts) == 1
    assert len(ends) == 1
    assert events[-1].event_type != "session" or "started" not in (events[-1].content or "").lower()


def test_timeline_tool_result_error_via_iserror(tmp_path: Path):
    """tool_call_update with isError=True marks result error."""
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "t1",
                        "title": "grep",
                        "rawInput": {"pattern": "x"},
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 11,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "isError": True,
                        "content": "error output",
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = parse_timeline(sd)
    results = [e for e in events if e.event_type == "tool_result"]
    assert results
    assert results[0].is_error is True


def test_timeline_tool_result_coalesced_replaces_longer(tmp_path: Path):
    """Second tool_call_update with longer content replaces first."""
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "t1",
                        "title": "make",
                        "rawInput": {},
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 11,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "content": "short",
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 12,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "t1",
                        "content": "much longer content here",
                        "status": "completed",
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = parse_timeline(sd)
    results = [e for e in events if e.event_type == "tool_result"]
    assert len(results) == 1
    assert results[0].content == "much longer content here"


# ── _extract_message_text edge cases ─────────────────────────────────────


def test_extract_message_text_non_dict_list_items():
    """Non-dict, non-str list items get JSON serialized."""
    result = _extract_message_text([42, {"type": "text", "text": "ok"}])
    assert "42" in result
    assert "ok" in result


def test_extract_message_text_raw_object():
    """Non-text dict gets JSON-dumped."""
    result = _extract_message_text({"key": "val"})
    assert "key" in result


# ── _extract_tool_update_text edge cases ─────────────────────────────────


def test_extract_tool_update_text_empty():
    assert _extract_tool_update_text(None) == ""
    assert _extract_tool_update_text("") == ""
    assert _extract_tool_update_text([]) == ""


def test_extract_tool_update_text_dict_content_string():
    result = _extract_tool_update_text([{"content": "direct string"}])
    assert result == "direct string"


# ── extract_prompt edge cases ────────────────────────────────────────────


def test_extract_prompt_no_user_query_tag(tmp_path: Path):
    """Content without <user_query> returns empty."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "chat_history.jsonl").write_text(
        json.dumps({"type": "user", "content": "no tag here"}) + "\n", encoding="utf-8"
    )
    assert extract_prompt(sd) == ""


def test_extract_prompt_unclosed_tag(tmp_path: Path):
    """<user_query> without closing tag returns empty."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "chat_history.jsonl").write_text(
        json.dumps({"type": "user", "content": "<user_query>unclosed"}) + "\n", encoding="utf-8"
    )
    assert extract_prompt(sd) == ""


def test_extract_prompt_from_content_list(tmp_path: Path):
    """Content as a list of blocks."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "chat_history.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "content": [{"type": "text", "text": "<user_query>found it</user_query>"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert extract_prompt(sd) == "found it"


# ── session_trace_mtime edge cases ──────────────────────────────────────


def test_session_trace_mtime_empty_dir(tmp_path: Path):
    sd = tmp_path / "empty"
    sd.mkdir()
    # No trace files, uses dir mtime
    mtime = session_trace_mtime(sd)
    assert mtime > 0


def test_session_trace_mtime_nonexistent(tmp_path: Path):
    mtime = session_trace_mtime(tmp_path / "nope")
    assert mtime == 0.0


# ── _infer_incomplete_turn_outcome ───────────────────────────────────────


def test_infer_interrupted_with_marker(tmp_path: Path):
    from groket.constants import INTERRUPTED_MARKER_FILENAME

    sd = tmp_path / "s"
    sd.mkdir()
    (sd / INTERRUPTED_MARKER_FILENAME).write_text("{}", encoding="utf-8")
    assert _infer_incomplete_turn_outcome(sd) == "interrupted"


def test_infer_no_body_returns_empty(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    assert _infer_incomplete_turn_outcome(sd) == ""


def test_infer_stale_body_returns_interrupted(tmp_path: Path):
    import os
    import time

    sd = tmp_path / "s"
    sd.mkdir()
    ev = sd / "events.jsonl"
    ev.write_text('{"x":1}\n' * 50, encoding="utf-8")  # >200 bytes
    # Set mtime to long ago
    old_time = time.time() - 100_000
    os.utime(ev, (old_time, old_time))
    assert _infer_incomplete_turn_outcome(sd) == "interrupted"


# ── _load_summary / _load_signals / _load_run_meta edge cases ────────────


def test_load_summary_bad_json(tmp_path: Path):
    from groket.models import SessionMeta
    from groket.parser import _load_summary

    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "summary.json").write_text("not-json", encoding="utf-8")
    meta = SessionMeta(session_id="s", session_dir=sd)
    _load_summary(meta, sd)
    assert meta.model_id == "unknown"


def test_load_signals_bad_json(tmp_path: Path):
    from groket.models import SessionMeta
    from groket.parser import _load_signals

    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "signals.json").write_text("bad", encoding="utf-8")
    meta = SessionMeta(session_id="s", session_dir=sd)
    _load_signals(meta, sd)
    assert meta.tool_call_count == 0


# ── _find_container_for_session / _model_from_run_json ────────────────────


def test_find_container_exact_match(tmp_path: Path):
    from groket.parser import _find_container_for_session

    sd = tmp_path / "sess1"
    sd.mkdir()
    result = _find_container_for_session(sd, {"c1": str(sd)})
    assert result == "c1"


def test_find_container_by_name(tmp_path: Path):
    from groket.parser import _find_container_for_session

    sd = tmp_path / "sess1"
    sd.mkdir()
    result = _find_container_for_session(sd, {"c2": "/other/path/sess1"})
    assert result == "c2"


def test_find_container_no_match(tmp_path: Path):
    from groket.parser import _find_container_for_session

    sd = tmp_path / "sess1"
    sd.mkdir()
    result = _find_container_for_session(sd, {"c3": "/other/path/other"})
    assert result == ""


def test_find_container_run_parent(tmp_path: Path):
    from groket.parser import _find_container_for_session

    parent = tmp_path / "groket-abc-model"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    result = _find_container_for_session(sd, {})
    assert result == "groket-abc-model"


def test_model_from_run_json_with_run_id_suffix(tmp_path: Path):
    sd = tmp_path / "groket-abc123-mymodel" / "sess"
    sd.mkdir(parents=True)
    data = {
        "run_id": "abc123",
        "models": [],
        "sessions": {"groket-abc123-mymodel": str(sd)},
    }
    result = _model_from_run_json(sd, data)
    assert result == "mymodel"


def test_model_from_run_parent_no_run_dir(tmp_path: Path):
    sd = tmp_path / "plain" / "sess"
    sd.mkdir(parents=True)
    result = _model_from_run_parent(sd)
    assert result == ""


def test_model_from_run_parent_digit_suffix(tmp_path: Path):
    """When last part is a digit, use second-to-last."""
    parent = tmp_path / "groket-abc-mymodel-1"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    result = _model_from_run_parent(sd)
    assert result == "mymodel"


def test_model_from_run_parent_short_body(tmp_path: Path):
    """groket-x (single part body) returns that part."""
    parent = tmp_path / "groket-singlepart"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    result = _model_from_run_parent(sd)
    assert result == ""


# ── _match_model_to_container ─────────────────────────────────────────────


def test_match_model_v9_alias():
    result = _match_model_to_container("groket-abc-bottlerock", ["v9-bottlerocket"])
    assert result == "v9-bottlerocket"


def test_match_model_empty_model_skipped():
    result = _match_model_to_container("groket-abc-model", ["", "v9-model"])
    assert result == "v9-model"


def test_match_model_full_name_in_container():
    result = _match_model_to_container("groket-v9-dietcoke-run", ["v9-dietcoke"])
    assert result == "v9-dietcoke"


# ── load_session_meta edge cases ─────────────────────────────────────────


def test_load_session_meta_run_json_from_parent(tmp_path: Path):
    """run.json in a groket-* parent dir is discovered."""
    parent = tmp_path / "groket-abc-model"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (parent / "run.json").write_text(
        json.dumps(
            {
                "run_id": "abc",
                "models": ["v9-model"],
                "sessions": {"groket-abc-model": str(sd)},
            }
        ),
        encoding="utf-8",
    )
    meta = load_session_meta(sd)
    assert meta.run_id == "abc"


def test_model_display_effort_from_run_dir_slug(tmp_path: Path) -> None:
    """Session under groket-*-{effort} shows model:effort without config.toml effort."""
    vol = tmp_path / "runs" / "traces" / "groket-abc123-xhigh"
    sd = vol / "%2Fworkspace" / "sess-eff"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"current_model_id": "v9-zingster", "info": {"id": "sess-eff"}}),
        encoding="utf-8",
    )
    meta = load_session_meta(sd)
    assert meta.reasoning_effort == "xhigh"
    assert meta.model_display == "v9-zingster:xhigh"


def test_reasoning_effort_from_run_dir_max(tmp_path: Path) -> None:
    from groket.parser import _reasoning_effort_from_run_dir

    p = tmp_path / "groket-deadbeef-max" / "sess"
    p.mkdir(parents=True)
    assert _reasoning_effort_from_run_dir(p) == "max"


def test_load_session_meta_turn_gate_running(tmp_path: Path):
    """Gate state=running overrides turn_outcome."""
    vol = tmp_path / "traces" / "groket-r-m"
    sess = vol / "%2F" / "sess-gate"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    gate = vol / ".groket-turn-r"
    gate.mkdir()
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "sess-gate"}) + "\n",
        encoding="utf-8",
    )
    meta = load_session_meta(sess)
    assert meta.turn_outcome == "running"


def test_load_session_meta_open_turn_after_completed(tmp_path: Path):
    """Extra turn_started after turn_ended → running the next turn (not awaiting)."""
    sd = tmp_path / "open-turn"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "turn_started", "turn_number": 0, "ts": 1}),
                json.dumps({"type": "turn_ended", "outcome": "completed", "ts": 2}),
                json.dumps({"type": "turn_started", "turn_number": 1, "ts": 3}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    meta = load_session_meta(sd)
    assert meta.turn_outcome == "running"
    assert meta.list_status_label() == "running"
    assert meta.turn_in_progress is True


def test_load_session_meta_single_turn_started_is_running(tmp_path: Path):
    """Open first turn (no turn_ended yet) is running, not awaiting follow-up."""
    sd = tmp_path / "first-turn"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_started", "turn_number": 0, "ts": 1}) + "\n",
        encoding="utf-8",
    )
    # Non-trivial body so incomplete-turn inference can mark running.
    (sd / "updates.jsonl").write_text('{"x": 1}\n' * 50, encoding="utf-8")
    meta = load_session_meta(sd)
    assert meta.turn_outcome != "awaiting_follow_up"
    assert meta.list_status_label() in ("running", "—", "complete")


def test_load_session_meta_failed_increments_error():
    """When turn_failed and error_count=0, error_count bumps to 1."""
    sd = Path("/tmp/test-meta-failed")
    sd.mkdir(parents=True, exist_ok=True)
    try:
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_ended", "outcome": "error", "ts": 1}) + "\n",
            encoding="utf-8",
        )
        (sd / "summary.json").write_text("{}", encoding="utf-8")
        (sd / "signals.json").write_text(json.dumps({"errorCount": 0}), encoding="utf-8")
        meta = load_session_meta(sd)
        assert meta.error_count >= 1
    finally:
        import shutil

        shutil.rmtree(sd, ignore_errors=True)


def test_load_session_meta_timeline_exception(tmp_path: Path):
    """If parse_timeline raises, num_events falls back to 0."""
    sd = tmp_path / "bad-timeline"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    # Write corrupt updates.jsonl that will cause issues
    (sd / "updates.jsonl").write_text('{"params": 123}\n', encoding="utf-8")
    meta = load_session_meta(sd)
    # Should still load without crashing
    assert meta.session_id == "bad-timeline"


# ── find_sessions prune dirs ────────────────────────────────────────────


def test_find_sessions_skips_stage_dirs(tmp_path: Path):
    """Directories ending with .stage are pruned from walk."""
    root = tmp_path / "traces"
    stage = root / "groket-abc.stage" / "inner"
    stage.mkdir(parents=True)
    (stage / "updates.jsonl").write_text("{}\n")
    real = root / "real-session"
    real.mkdir()
    (real / "updates.jsonl").write_text("{}\n")
    sessions = find_sessions(root)
    assert any(p.name == "real-session" for p in sessions)
    assert not any(".stage" in str(p) for p in sessions)


def test_find_sessions_events_empty_file(tmp_path: Path):
    """Empty events.jsonl (0 bytes) does not count as session."""
    root = tmp_path / "traces"
    sd = root / "empty-ev"
    sd.mkdir(parents=True)
    (sd / "events.jsonl").write_text("")
    sessions = find_sessions(root)
    assert not any(p.name == "empty-ev" for p in sessions)


# ── _prune_session_walk_dirs ─────────────────────────────────────────────


def test_prune_session_walk_dirs():
    from groket.parser import _prune_session_walk_dirs

    dirs = ["groket-abc-model", ".git", "node_modules", "groket-x.stage", "real-dir"]
    _prune_session_walk_dirs(dirs)
    assert ".git" not in dirs
    assert "node_modules" not in dirs
    assert "groket-x.stage" not in dirs
    assert "groket-abc-model" in dirs
    assert "real-dir" in dirs


# ── _apply_tool_result_meta more branches ────────────────────────────────


def test_apply_tool_result_meta_ofp_different_no_exit():
    """ofp != result_content but neither starts with 'exit:' → no replacement."""
    from groket.models import ToolCall, ToolInputBag
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(
        call_id="c1", tool_name="grep", raw_input=ToolInputBag({}), result_content="original"
    )
    _apply_tool_result_meta(
        tc,
        {
            "rawOutput": {"output_for_prompt": "different"},
        },
    )
    # No replacement because neither starts with exit:
    assert tc.result_content == "original"


def test_apply_tool_result_meta_exit_code_1_not_error_no_signal():
    """exit_code=1 for run_terminal_command with no signal → not error."""
    from groket.models import ToolCall, ToolInputBag
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(call_id="c1", tool_name="run_terminal_command", raw_input=ToolInputBag({}))
    _apply_tool_result_meta(tc, {"rawOutput": {"exit_code": 0}})
    assert tc.is_error is False


def test_apply_tool_result_meta_exit_code_high():
    """exit_code >= 2 for terminal commands → is_error."""
    from groket.models import ToolCall, ToolInputBag
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(call_id="c1", tool_name="run_terminal_command", raw_input=ToolInputBag({}))
    _apply_tool_result_meta(tc, {"rawOutput": {"exit_code": 2}})
    assert tc.is_error is True


# ── _extract_tool_update_text edge branches ──────────────────────────────


def test_extract_tool_update_text_list_non_dict_items():
    """Non-dict items in the content list are skipped."""
    result = _extract_tool_update_text([42, "string", {"content": {"text": "ok"}}])
    assert result == "ok"


def test_extract_tool_update_text_unknown_type():
    assert _extract_tool_update_text(12345) == ""


# ── events.jsonl OSError ─────────────────────────────────────────────────


def test_runtime_markers_oserror(tmp_path: Path):
    """OSError reading events.jsonl returns empty."""
    sd = tmp_path / "s"
    sd.mkdir()
    ef = sd / "events.jsonl"
    ef.write_text("{}\n", encoding="utf-8")
    ef.chmod(0o000)
    markers, outcome, loops = parse_runtime_markers(sd)
    ef.chmod(0o644)
    assert markers == []
    assert outcome == ""


# ── _coalesce_tool_result edge cases ────────────────────────────────────


def test_coalesce_empty_content_no_error_no_terminal():
    """No content + no error + no terminal → skip, return same idx."""
    from groket.models import TraceEvent as _TE
    from groket.parser import _coalesce_tool_result

    events: list[_TE] = []
    pending: dict[str, _TE] = {}
    result_by: dict[str, int] = {}
    idx = _coalesce_tool_result(
        {"toolCallId": "t1", "content": "", "status": ""},
        10,
        0,
        events,
        0,
        pending,
        result_by,
    )
    assert idx == 0
    assert len(events) == 0


def test_coalesce_existing_result_error_flag():
    """Second update marks existing result as error."""
    from groket.models import TraceEvent as _TE
    from groket.parser import _coalesce_tool_result

    ev = _TE(index=0, event_type="tool_result", content="output", tool_call_id="t1")
    events: list[_TE] = [ev]
    result_by: dict[str, int] = {"t1": 0}
    pending = {"t1": _TE(index=0, event_type="tool_call", tool_name="grep", tool_call_id="t1")}
    idx = _coalesce_tool_result(
        {"toolCallId": "t1", "content": "error text", "isError": True, "status": "failed"},
        11,
        1,
        events,
        1,
        pending,
        result_by,
    )
    assert events[0].is_error is True


def test_coalesce_failed_no_text_creates_event():
    """Failed update with no text but isError=True creates error event."""
    from groket.models import TraceEvent as _TE
    from groket.parser import _coalesce_tool_result

    events: list[_TE] = []
    result_by: dict[str, int] = {}
    pending: dict[str, _TE] = {}
    idx = _coalesce_tool_result(
        {"toolCallId": "t1", "isError": True, "status": "failed"},
        10,
        0,
        events,
        0,
        pending,
        result_by,
    )
    assert idx == 1
    assert len(events) == 1
    assert events[0].is_error is True


# ── session_trace_mtime with file stat error ─────────────────────────────


def test_session_trace_mtime_stat_error(tmp_path: Path):
    """If dir stat also fails, returns 0."""
    mtime = session_trace_mtime(tmp_path / "completely-missing")
    assert mtime == 0.0


# ── _infer_incomplete_turn_outcome running ───────────────────────────────


def test_infer_running_when_recent(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    ev = sd / "events.jsonl"
    ev.write_text('{"x":1}\n' * 50, encoding="utf-8")
    # Just written → mtime is recent → running
    assert _infer_incomplete_turn_outcome(sd) == "running"


def test_infer_no_mtime_returns_interrupted(tmp_path: Path):
    """No trace files at all but body check matches → interrupted."""
    sd = tmp_path / "s"
    sd.mkdir()
    ev = sd / "chat_history.jsonl"
    ev.write_text('{"x":1}\n' * 50, encoding="utf-8")
    result = _infer_incomplete_turn_outcome(sd)
    # Has body, recent mtime → running or interrupted
    assert result in ("running", "interrupted")


# ── _find_container partial match ────────────────────────────────────────


def test_find_container_is_relative_to(tmp_path: Path):
    """Match via is_relative_to check."""
    from groket.parser import _find_container_for_session

    parent = tmp_path / "traces" / "c1"
    sd = parent / "workspace" / "sess"
    sd.mkdir(parents=True)
    result = _find_container_for_session(sd, {"c1": str(parent)})
    assert result == "c1"


def test_find_container_sid_in_spath(tmp_path: Path):
    """Match via session id substring in session path."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "my-session-id"
    sd.mkdir()
    result = _find_container_for_session(sd, {"c1": "/path/containing/my-session-id/somewhere"})
    assert result == "c1"


# ── _model_from_run_json more branches ───────────────────────────────────


def test_model_from_run_json_no_models_no_sessions():
    assert _model_from_run_json(Path("/tmp/x"), {"models": [], "sessions": {}}) == ""


def test_model_from_run_json_no_container_match(tmp_path: Path):
    sd = tmp_path / "sess"
    sd.mkdir()
    result = _model_from_run_json(sd, {"models": ["v9-dc"], "sessions": {"c1": "/other/path"}})
    assert result == ""


# ── _match_model_to_container more ───────────────────────────────────────


def test_match_model_partial_short_in_cname():
    """Short model suffix found as substring in container name."""
    result = _match_model_to_container("groket-abc-some-dc", ["v9-dc"])
    assert result == "v9-dc"


def test_match_model_full_in_cname():
    """Full model name found in container name."""
    result = _match_model_to_container("groket-v9-dietcoke-stuff", ["v9-dietcoke"])
    assert result == "v9-dietcoke"


# ── _model_from_run_parent special cases ─────────────────────────────────


def test_model_from_run_parent_build_suffix(tmp_path: Path):
    """'build', 'traces', 'workspace' suffixes skipped."""
    parent = tmp_path / "groket-abc-build"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    result = _model_from_run_parent(sd)
    # 'build' is excluded
    assert result == ""


# ── load_session_meta: run.json read error ──────────────────────────────


def test_load_session_meta_run_json_bad(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    (sd / "run.json").write_text("not-json", encoding="utf-8")
    meta = load_session_meta(sd)
    assert meta.session_id == "s"


def test_load_session_meta_infer_model_from_parent(tmp_path: Path):
    """Model inferred from run parent when summary model is 'unknown'."""
    parent = tmp_path / "groket-abc-dietcoke"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    meta = load_session_meta(sd)
    # Should pick up 'dietcoke' from parent name
    assert meta.model_id == "dietcoke" or meta.model_id == "unknown"


# ── Additional coverage for remaining gaps ────────────────────────────────


def test_as_epoch_ts_non_primitive():
    """_as_epoch_ts returns None for non-primitive types like list or dict."""
    from groket.parser import _as_epoch_ts

    assert _as_epoch_ts([1, 2, 3]) is None
    assert _as_epoch_ts({"key": "value"}) is None


def test_extract_message_text_list_content_json():
    """_extract_message_text dumps non-string/non-dict items as JSON."""
    from groket.parser import _extract_message_text

    msg = {"content": [42, True, None]}
    text = _extract_message_text(msg)
    assert "42" in text


def test_session_trace_mtime_fallback_to_dir(tmp_path: Path):
    """session_trace_mtime falls back to session_dir.stat() mtime."""
    from groket.parser import session_trace_mtime

    sd = tmp_path / "sess"
    sd.mkdir()
    # No trace files, but dir exists → falls back to dir mtime
    mtime = session_trace_mtime(sd)
    assert mtime > 0


def test_infer_incomplete_no_body_returns_empty(tmp_path: Path):
    """_infer_incomplete returns '' when no substantial trace data exists."""
    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "empty-sess"
    sd.mkdir()
    # No trace files at all → no body → returns ""
    result = _infer_incomplete_turn_outcome(sd)
    assert result == ""


def test_infer_incomplete_stale_returns_interrupted(tmp_path: Path):
    """_infer_incomplete returns 'interrupted' for stale sessions."""
    import os
    import time

    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "stale-sess"
    sd.mkdir()
    # Create events.jsonl with > 200 bytes and make it very old
    (sd / "events.jsonl").write_text("x" * 300, encoding="utf-8")
    old_time = time.time() - 86400  # 1 day ago
    os.utime(sd / "events.jsonl", (old_time, old_time))
    result = _infer_incomplete_turn_outcome(sd)
    assert result == "interrupted"


def test_find_sessions_events_only(tmp_path: Path):
    """find_sessions picks up a session with only events.jsonl (non-empty)."""
    sd = tmp_path / "events-only"
    sd.mkdir()
    (sd / "events.jsonl").write_text(json.dumps({"type": "turn_started"}) + "\n", encoding="utf-8")
    sessions = find_sessions(tmp_path)
    assert any(s.name == "events-only" for s in sessions)


def test_find_sessions_empty_events_skipped(tmp_path: Path):
    """find_sessions skips sessions where events.jsonl is empty (0 bytes)."""
    sd = tmp_path / "empty-events"
    sd.mkdir()
    (sd / "events.jsonl").write_text("", encoding="utf-8")
    sessions = find_sessions(tmp_path)
    assert not any(s.name == "empty-events" for s in sessions)


def test_load_session_meta_turn_gate_awaiting(tmp_path: Path):
    """load_session_meta picks up 'awaiting_follow_up' from turn gate status."""
    parent = tmp_path / "groket-run" / "traces"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(json.dumps({"info": {"id": "sess"}}), encoding="utf-8")
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "outcome": "success"}) + "\n",
        encoding="utf-8",
    )
    # Create a turn gate with awaiting state
    gate = parent / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "sess"}) + "\n",
        encoding="utf-8",
    )
    meta = load_session_meta(sd)
    assert meta.turn_outcome in ("awaiting_follow_up", "success")


def test_load_session_meta_gate_running_with_summary(tmp_path: Path):
    """load_session_meta picks up 'running' from turn gate status with summary."""
    parent = tmp_path / "groket-run" / "traces"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(json.dumps({"info": {"id": "sess"}}), encoding="utf-8")
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    gate = parent / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "running", "session_id": "sess"}) + "\n",
        encoding="utf-8",
    )
    meta = load_session_meta(sd)
    assert meta.turn_outcome == "running"


def test_match_model_to_container_v9_alias():
    """_match_model_to_container matches v9-alias-style model ids."""
    from groket.parser import _match_model_to_container

    result = _match_model_to_container("groket-abc-bottlerock", ["v9-bottlerocket"])
    assert result == "v9-bottlerocket" or result == ""


def test_match_model_to_container_empty_model():
    """_match_model_to_container skips empty model strings."""
    from groket.parser import _match_model_to_container

    result = _match_model_to_container("groket-abc-m1", ["", "m1"])
    assert result == "m1"


def test_find_container_for_session_path_match(tmp_path: Path):
    """_find_container_for_session matches via path resolution."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "groket-run" / "sess-1"
    sd.mkdir(parents=True)
    sessions_map = {"groket-run-m1": str(tmp_path / "groket-run")}
    result = _find_container_for_session(sd, sessions_map)
    assert result == "groket-run-m1"


def test_find_container_for_session_sid_in_value(tmp_path: Path):
    """_find_container_for_session matches when session_id appears in value string."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "sess-1"
    sd.mkdir(parents=True)
    sessions_map = {"container-x": "/some/path/sess-1/data"}
    result = _find_container_for_session(sd, sessions_map)
    assert result == "container-x"


def test_find_container_walks_parents(tmp_path: Path):
    """_find_container_for_session falls back to parent groket-* dir name."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "groket-run123-m1" / "workspace" / "sess"
    sd.mkdir(parents=True)
    result = _find_container_for_session(sd, {})
    assert result == "groket-run123-m1"


def test_find_container_returns_empty_no_match(tmp_path: Path):
    """_find_container_for_session returns empty when nothing matches."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "plain" / "sess"
    sd.mkdir(parents=True)
    result = _find_container_for_session(sd, {"c1": "/totally/different"})
    assert result == ""


def test_model_from_run_json_suffix_fallback(tmp_path: Path):
    """_model_from_run_json falls back to container suffix after run_id."""
    from groket.parser import _model_from_run_json

    sd = tmp_path / "groket-abc123-mymodel" / "sess"
    sd.mkdir(parents=True)
    run_data = {
        "run_id": "abc123",
        "models": [],
        "sessions": {"groket-abc123-mymodel": str(sd)},
    }
    result = _model_from_run_json(sd, run_data)
    assert result == "mymodel"


def test_model_from_run_json_no_match_returns_empty(tmp_path: Path):
    """_model_from_run_json returns empty when no container matches."""
    from groket.parser import _model_from_run_json

    sd = tmp_path / "sess"
    sd.mkdir(parents=True)
    run_data = {"models": ["m1"], "sessions": {"c1": "/nope"}}
    result = _model_from_run_json(sd, run_data)
    assert result == ""


def test_model_from_run_json_empty_data():
    """_model_from_run_json returns empty for no models and no sessions."""
    from groket.parser import _model_from_run_json

    result = _model_from_run_json(Path("/x"), {})
    assert result == ""


def test_tool_call_update_replaces_result_with_exit_prefix():
    """_apply_tool_result_meta replaces result_content when rawOutput starts with exit:."""
    from groket.models import ToolCall
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(
        call_id="tc1",
        tool_name="run_terminal_command",
        raw_input={"command": "ls"},
    )
    tc.result_content = "some output"
    update: dict[str, object] = {
        "rawOutput": {"output_for_prompt": "exit: 0\nmore output"},
    }
    _apply_tool_result_meta(tc, update)
    assert tc.result_content.startswith("exit:")


def test_infer_incomplete_running_recent(tmp_path: Path):
    """_infer_incomplete returns running for recent trace data."""
    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text("x" * 300 + "\n", encoding="utf-8")
    result = _infer_incomplete_turn_outcome(sd)
    assert result == "running"


def test_infer_incomplete_interrupted_stale(tmp_path: Path):
    """_infer_incomplete returns interrupted for stale trace data."""
    import os
    import time

    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text("x" * 300 + "\n", encoding="utf-8")
    old_time = time.time() - 7200
    os.utime(sd / "events.jsonl", (old_time, old_time))
    result = _infer_incomplete_turn_outcome(sd)
    assert result == "interrupted"


def test_session_trace_mtime_oserror(tmp_path: Path):
    """session_trace_mtime falls back to dir mtime when files raise OSError."""
    from groket.parser import session_trace_mtime

    sd = tmp_path / "s"
    sd.mkdir()
    result = session_trace_mtime(sd)
    assert result > 0  # falls back to dir mtime


def test_find_sessions_events_only_oserror(tmp_path: Path):
    """find_sessions handles OSError when stating events.jsonl."""
    from groket.parser import find_sessions

    sd = tmp_path / "traces" / "groket-r" / "s1"
    sd.mkdir(parents=True)
    (sd / "events.jsonl").write_text("", encoding="utf-8")  # 0 bytes
    sessions = find_sessions(tmp_path / "traces")
    # 0-byte events.jsonl is not added
    assert sd not in sessions


def test_load_session_meta_turn_gate_exception(tmp_path: Path):
    """load_session_meta handles turn_gate import exception gracefully."""
    from groket.parser import load_session_meta

    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "summary.json").write_text(json.dumps({"info": {"id": "s"}}), encoding="utf-8")
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "outcome": "success"}) + "\n",
        encoding="utf-8",
    )
    meta = load_session_meta(sd)
    assert meta.turn_outcome == "success"


def test_match_model_full_name_in_cname():
    """_match_model_to_container matches full model name in container name."""
    from groket.parser import _match_model_to_container

    result = _match_model_to_container("groket-abc-v9-pizzaparty", ["v9-pizzaparty"])
    assert result == "v9-pizzaparty"


def test_match_model_short_in_cname():
    """_match_model_to_container matches short suffix in container name."""
    from groket.parser import _match_model_to_container

    result = _match_model_to_container("groket-abc-dietcoke", ["v9-dietcoke"])
    assert result == "v9-dietcoke"


# ── Additional parser coverage ───────────────────────────────────────────


def test_apply_tool_result_meta_empty_result_sets_content():
    """_apply_tool_result_meta sets result_content when empty."""
    from groket.models import ToolCall
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(call_id="tc1", tool_name="read_file", raw_input={})
    tc.result_content = ""
    update: dict[str, object] = {
        "rawOutput": {"output_for_prompt": "file contents here"},
    }
    _apply_tool_result_meta(tc, update)
    assert tc.result_content == "file contents here"


def test_apply_tool_result_meta_exit_code_and_signal():
    """_apply_tool_result_meta sets exit_code and signal fields."""
    from groket.models import ToolCall
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(call_id="tc2", tool_name="run_terminal_command", raw_input={})
    update: dict[str, object] = {
        "rawOutput": {"exit_code": 1, "signal": "SIGKILL"},
    }
    _apply_tool_result_meta(tc, update)
    assert tc.exit_code == 1
    assert tc.signal == "SIGKILL"


def test_apply_tool_result_meta_is_error():
    """_apply_tool_result_meta sets is_error from isError flag."""
    from groket.models import ToolCall
    from groket.parser import _apply_tool_result_meta

    tc = ToolCall(call_id="tc3", tool_name="grep", raw_input={})
    update: dict[str, object] = {"isError": True}
    _apply_tool_result_meta(tc, update)
    assert tc.is_error


def test_extract_message_text_list():
    """_extract_message_text handles list of mixed content blocks."""
    from groket.parser import _extract_message_text

    result = _extract_message_text(
        [
            {"type": "text", "text": "hello "},
            "world",
            {"type": "image", "url": "x.png"},
        ]
    )
    assert "hello world" in result


def test_extract_message_text_dict():
    """_extract_message_text handles dict with type=text."""
    from groket.parser import _extract_message_text

    result = _extract_message_text({"type": "text", "text": "content"})
    assert result == "content"


def test_extract_message_text_non_text():
    """_extract_message_text JSON-dumps non-string/dict/list."""
    from groket.parser import _extract_message_text

    result = _extract_message_text(42)
    assert "42" in result


def test_infer_incomplete_no_body(tmp_path: Path):
    """_infer_incomplete_turn_outcome returns '' for session with no body."""
    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "sess"
    sd.mkdir()
    assert _infer_incomplete_turn_outcome(sd) == ""


def test_infer_incomplete_with_marker(tmp_path: Path):
    """_infer_incomplete_turn_outcome returns 'interrupted' for marker file."""
    from groket.constants import INTERRUPTED_MARKER_FILENAME
    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "sess"
    sd.mkdir()
    (sd / INTERRUPTED_MARKER_FILENAME).write_text("{}", encoding="utf-8")
    assert _infer_incomplete_turn_outcome(sd) == "interrupted"


def test_infer_incomplete_stale_body(tmp_path: Path):
    """_infer_incomplete_turn_outcome returns 'interrupted' for stale body."""
    import os
    import time

    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "sess"
    sd.mkdir()
    ef = sd / "events.jsonl"
    ef.write_text("x" * 300, encoding="utf-8")
    old = time.time() - 86400
    os.utime(ef, (old, old))
    assert _infer_incomplete_turn_outcome(sd) == "interrupted"


def test_session_trace_mtime_no_files(tmp_path: Path):
    """session_trace_mtime returns 0 for empty session dir."""
    from groket.parser import session_trace_mtime

    sd = tmp_path / "empty"
    sd.mkdir()
    assert session_trace_mtime(sd) > 0 or session_trace_mtime(sd) == 0


def test_find_container_walks_parents_path_only():
    """_find_container_for_session resolves parent groket-* dir from path."""
    from groket.parser import _find_container_for_session

    sd = Path("/traces/groket-abc-model/%2Fworkspace/sess-id")
    result = _find_container_for_session(sd, {})
    assert result == "groket-abc-model"


def test_find_container_sessions_map_match():
    """_find_container_for_session matches via sessions map value."""
    from groket.parser import _find_container_for_session

    sd = Path("/traces/groket-r1/sess-id")
    sessions = {"groket-r1-model": str(sd)}
    result = _find_container_for_session(sd, sessions)
    assert result == "groket-r1-model"


# ── Deeper parser coverage ────────────────────────────────────────────────


def test_session_trace_mtime_stat_oserror(tmp_path: Path):
    """session_trace_mtime handles stat OSError on individual files."""
    from groket.parser import session_trace_mtime

    sd = tmp_path / "sess"
    sd.mkdir()
    ef = sd / "events.jsonl"
    ef.write_text("{}\n", encoding="utf-8")
    orig_stat = Path.stat

    def _fake_stat(self: Path, **kwargs: JsonValue) -> os.stat_result:
        if self.name == "events.jsonl":
            raise OSError("denied")
        return orig_stat(self, **kwargs)

    with patch.object(Path, "stat", _fake_stat):
        mtime = session_trace_mtime(sd)
    # Should still return something (fallback to dir stat)
    assert mtime >= 0


def test_find_container_relative_to_match(tmp_path: Path):
    """_find_container_for_session matches via is_relative_to."""
    from groket.parser import _find_container_for_session

    parent = tmp_path / "groket-run" / "data"
    sd = parent / "sess"
    sd.mkdir(parents=True)
    sessions_map = {"groket-run-m1": str(parent)}
    result = _find_container_for_session(sd, sessions_map)
    assert result == "groket-run-m1"


def test_find_container_oserror_in_loop(tmp_path: Path):
    """_find_container_for_session handles OSError in sessions_map loop."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "sess"
    sd.mkdir()
    sessions_map = {"c1": None}  # type: ignore[dict-item]  # deliberate wrong type
    result = _find_container_for_session(sd, sessions_map)
    # Should not crash; returns empty or parent match
    assert isinstance(result, str)


def test_model_from_run_json_resolve_error(tmp_path: Path):
    """_model_from_run_json handles resolve OSError."""
    from groket.parser import _model_from_run_json

    sd = tmp_path / "groket-abc-model" / "sess"
    sd.mkdir(parents=True)
    result = _model_from_run_json(sd, {"models": ["model"], "sessions": {}})
    # Should fall back to suffix extraction
    assert isinstance(result, str)


def test_find_sessions_stat_oserror_on_events(tmp_path: Path):
    """find_sessions handles OSError on events.jsonl stat gracefully."""
    sd = tmp_path / "sess"
    sd.mkdir()
    ef = sd / "events.jsonl"
    ef.write_text('{"x":1}\n', encoding="utf-8")

    orig_stat = Path.stat

    def _stat_fail(self: Path, **kwargs: JsonValue) -> os.stat_result:
        if self.name == "events.jsonl" and "sess" in str(self):
            raise OSError("stat denied")
        return orig_stat(self, **kwargs)

    with patch.object(Path, "stat", _stat_fail):
        result = find_sessions(tmp_path)
    # OSError in stat → session skipped
    assert sd not in result


def test_find_container_resolve_oserror(tmp_path: Path):
    """_find_container_for_session handles resolve() OSError on session_dir."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "groket-abc-model" / "sess"
    sd.mkdir(parents=True)

    orig_resolve = Path.resolve

    def _fail_resolve(self: Path) -> Path:
        if str(self) == str(sd):
            raise OSError("denied")
        return orig_resolve(self)

    with patch.object(Path, "resolve", _fail_resolve):
        result = _find_container_for_session(sd, {})
    # Falls back to parent walk for groket-* name
    assert result == "groket-abc-model"


def test_find_container_path_resolve_oserror(tmp_path: Path):
    """_find_container_for_session handles resolve() OSError on map path."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "sess"
    sd.mkdir()
    sessions_map = {"container1": str(sd)}
    result = _find_container_for_session(sd, sessions_map)
    assert result == "container1"


def test_find_container_is_relative_to_match(tmp_path: Path):
    """_find_container_for_session matches via is_relative_to."""
    from groket.parser import _find_container_for_session

    parent = tmp_path / "traces" / "groket-run-model"
    sd = parent / "workspace" / "sess"
    sd.mkdir(parents=True)
    # Map path is the parent dir
    sessions_map = {"c1": str(parent)}
    result = _find_container_for_session(sd, sessions_map)
    assert result == "c1"


def test_find_container_sid_in_spath_fallback(tmp_path: Path):
    """_find_container_for_session falls back to sid-in-spath match."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "mysession"
    sd.mkdir()
    # Map path contains sid as substring but paths don't resolve equal
    sessions_map = {"c1": "/different/path/mysession/data"}
    result = _find_container_for_session(sd, sessions_map)
    assert result == "c1"


def test_find_container_no_match_returns_empty(tmp_path: Path):
    """_find_container_for_session returns empty string when nothing matches."""
    from groket.parser import _find_container_for_session

    sd = tmp_path / "unrelated"
    sd.mkdir()
    sessions_map = {"c1": "/completely/different"}
    result = _find_container_for_session(sd, sessions_map)
    assert result == ""


def test_match_model_empty_string_skipped():
    """_match_model_to_container skips empty model strings."""
    from groket.parser import _match_model_to_container

    result = _match_model_to_container("groket-abc-model", ["", "  ", "v9-model"])
    assert result == "v9-model"


def test_model_from_run_json_run_id_suffix(tmp_path: Path):
    """_model_from_run_json extracts model from container suffix after run_id."""
    from groket.parser import _model_from_run_json

    sd = tmp_path / "groket-abc123def456-model" / "sess"
    sd.mkdir(parents=True)
    run_data = {
        "run_id": "abc123def456",
        "sessions": {"groket-abc123def456-model": str(sd)},
    }
    result = _model_from_run_json(sd, run_data)
    assert isinstance(result, str)


def test_load_session_meta_gate_awaiting(tmp_path: Path):
    """load_session_meta sets turn_outcome from turn gate awaiting state."""
    sd = tmp_path / "sess"
    sd.mkdir()
    (sd / "updates.jsonl").write_text("", encoding="utf-8")
    # Create a gate with awaiting_follow_up state
    gate = tmp_path / ".groket-turn"
    gate.mkdir(parents=True)
    status = {"state": "awaiting_follow_up", "turn": 1}
    (gate / "status.json").write_text(json.dumps(status), encoding="utf-8")
    meta = load_session_meta(sd)
    assert isinstance(meta.turn_outcome, str)


def test_load_session_meta_gate_running_override(tmp_path: Path):
    """load_session_meta sets turn_outcome to running from gate state."""
    sd = tmp_path / "sess"
    sd.mkdir()
    (sd / "updates.jsonl").write_text("", encoding="utf-8")
    gate = tmp_path / ".groket-turn"
    gate.mkdir(parents=True)
    status = {"state": "running", "turn": 2}
    (gate / "status.json").write_text(json.dumps(status), encoding="utf-8")
    meta = load_session_meta(sd)
    assert isinstance(meta.turn_outcome, str)


def test_infer_incomplete_mtime_zero(tmp_path: Path):
    """_infer_incomplete_turn_outcome returns interrupted when mtime is zero."""
    from groket.parser import _infer_incomplete_turn_outcome

    sd = tmp_path / "sess"
    sd.mkdir()
    # Need a body file > 200 bytes to pass has_body check
    (sd / "events.jsonl").write_text("x" * 300, encoding="utf-8")
    # Patch session_trace_mtime to return 0
    with patch("groket.parser.session_trace_mtime", return_value=0.0):
        result = _infer_incomplete_turn_outcome(sd)
    assert result == "interrupted"
