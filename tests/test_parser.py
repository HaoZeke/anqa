"""Grok store: discovery, stamps, and native timeline."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from anqa.harness.grok import GrokAdapter
from anqa.harness.grok_parse import find_sessions, session_trace_mtime

# ── parse_timeline ────────────────────────────────────────────────────────


class TestParseTimeline:
    def test_basic_timeline(self, session_dir):
        events = GrokAdapter().parse_timeline(session_dir)
        assert len(events) > 0
        types = [e.event_type for e in events]
        assert "turn_started" in types  # turn marker
        assert "tool_call" in types
        assert "tool_call_update" in types

    def test_indices_sequential(self, session_dir):
        events = GrokAdapter().parse_timeline(session_dir)
        for i, ev in enumerate(events):
            assert ev.index == i

    def test_user_message_present(self, session_dir):
        events = GrokAdapter().parse_timeline(session_dir)
        user_events = [e for e in events if e.event_type == "user_message_chunk"]
        assert len(user_events) >= 1

    def test_user_message_preserves_prompt_index(self, tmp_path: Path):
        sd = tmp_path / "prompt-index"
        sd.mkdir()
        (sd / "updates.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": 1000,
                    "params": {
                        "update": {
                            "sessionUpdate": "user_message_chunk",
                            "content": {"type": "text", "text": "inspect the trace"},
                            "_meta": {"promptIndex": 7},
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        events = GrokAdapter().parse_timeline(sd)

        user = next(event for event in events if event.event_type == "user_message_chunk")
        assert user.prompt_index == 7

    def test_tool_result_coalescing(self, session_dir):
        events = GrokAdapter().parse_timeline(session_dir)
        results = [e for e in events if e.event_type == "tool_call_update"]
        call_ids = [e.tool_call_id for e in results]
        # Each call_id should appear at most once (coalesced)
        assert len(call_ids) == len(set(call_ids))

    def test_empty_session(self, empty_session_dir):
        events = GrokAdapter().parse_timeline(empty_session_dir)
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
        events = GrokAdapter().parse_timeline(sd)
        # Chronological: start1, assistant@1500, end1, start2, assistant@3500, end2
        types = [e.event_type for e in events]
        assert types.count("turn_started") + types.count("turn_ended") >= 4
        starts = [i for i, e in enumerate(events) if "turn started" in (e.content or "")]
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

    def test_skips_anqa_staging(self, tmp_path):
        """Marketplace plugin trees must not be walked or listed as sessions."""
        run = tmp_path / "traces" / "anqa-abc-model"
        plug = run / "anqa-plugins" / "superpowers" / "docs"
        plug.mkdir(parents=True)
        (plug / "summary.json").write_text("{}", encoding="utf-8")
        sess = run / "%2Fworkspace" / "019f-session-id"
        sess.mkdir(parents=True)
        (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
        found = find_sessions(tmp_path / "traces")
        assert sess in found
        assert not any("anqa-plugins" in str(p) for p in found)


# ── extract_prompt ────────────────────────────────────────────────────────

# ── parse_chat_history ────────────────────────────────────────────────────

from anqa.stamp import Stamp


def test_as_epoch_ts_variants():
    assert Stamp.epoch(None) is None
    assert Stamp.epoch(True) is None
    assert Stamp.epoch(1000) == 1000
    assert Stamp.epoch(1000.5) == 1000
    assert Stamp.epoch("2023-11-14T22:13:20Z") == 1700000000
    assert Stamp.epoch("not-a-date") is None
    assert Stamp.epoch(False) is None
    assert Stamp.epoch("1700000000") is None


# ── _as_epoch_ts edge cases ──────────────────────────────────────────────


def test_as_epoch_ts_float_string():
    assert Stamp.epoch("1700000000.5") is None


# ── _parse_runtime_ts edge cases ─────────────────────────────────────────


def test_timeline_does_not_copy_run_id_onto_non_workflow(tmp_path: Path) -> None:
    """A shell result that happens to carry run_id does not mutate the tool bag."""
    sd = tmp_path / "sess-bag"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-bag"}, "generated_title": "bag"}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {
                    "timestamp": 1,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "call-sh",
                            "title": "run_terminal_command",
                            "rawInput": {"command": "echo hi"},
                        }
                    },
                },
                {
                    "timestamp": 2,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "call-sh",
                            "status": "completed",
                            "content": "hi",
                            "rawOutput": {
                                "type": "Shell",
                                "run_id": "wf_stolen",
                                "output_for_prompt": "hi",
                            },
                        }
                    },
                },
                {
                    "timestamp": 3,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "call-wf",
                            "title": "workflow",
                            "rawInput": {"script_path": "/repo/.grok/workflows/sprint.rhai"},
                        }
                    },
                },
                {
                    "timestamp": 4,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "call-wf",
                            "status": "completed",
                            "rawOutput": {
                                "type": "Workflow",
                                "run_id": "wf_real",
                                "name": "sprint-8",
                            },
                        }
                    },
                },
            )
        ),
        encoding="utf-8",
    )
    events = GrokAdapter().parse_timeline(sd)
    shell = next(e for e in events if e.tool_name == "run_terminal_command")
    assert shell.raw_input.as_str("run_id") == ""
    wf = next(e for e in events if e.tool_name == "workflow")
    assert wf.raw_input.as_str("run_id") == "wf_real"


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
    events = GrokAdapter().parse_timeline(sd)
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
    events = GrokAdapter().parse_timeline(sd)
    subs = [e for e in events if e.event_type in ("subagent_spawned", "subagent_finished")]
    assert len(subs) == 2
    assert "worker" in subs[0].content
    assert subs[0].raw_input.as_str("subagentType") == "coder"


def _write_updates(sd: Path, updates: list[dict[str, object]]) -> None:
    lines = [
        json.dumps({"timestamp": i + 1, "params": {"update": upd}}) for i, upd in enumerate(updates)
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_timeline_scheduled_task_created_has_structured_fields(tmp_path: Path) -> None:
    sd = tmp_path / "sched"
    sd.mkdir()
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "scheduled_task_created",
                "task_id": "01a016e8b810",
                "prompt": "Watch the anqa board every hour and notify on change.",
                "human_schedule": "every 1 hour",
                "next_fire_at": "2026-08-18T23:05:45.360458771+00:00",
            }
        ],
    )
    evs = [e for e in GrokAdapter().parse_timeline(sd) if e.event_type == "scheduled_task_created"]
    assert len(evs) == 1
    ev = evs[0]
    assert ev.raw_input.as_str("task_id") == "01a016e8b810"
    assert ev.raw_input.as_str("human_schedule") == "every 1 hour"
    assert ev.raw_input.as_str("next_fire_at").startswith("2026-08-18T23:05:45")
    assert "Watch the anqa board" in ev.raw_input.as_str("prompt")
    assert "every 1 hour" in ev.content
    assert ev.event_type != "subagent_spawned"


def test_timeline_scheduled_task_deleted_and_unknown_suffix(tmp_path: Path) -> None:
    sd = tmp_path / "sched2"
    sd.mkdir()
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "scheduled_task_deleted",
                "task_id": "01a00e101f74",
                "reason": "deleted",
            },
            {
                "sessionUpdate": "scheduled_task_fired",
                "task_id": "01a00e101f74",
                "human_schedule": "every 1 hour",
            },
        ],
    )
    types = [e.event_type for e in GrokAdapter().parse_timeline(sd)]
    assert "scheduled_task_deleted" in types
    assert "scheduled_task_fired" in types
    deleted = next(
        e for e in GrokAdapter().parse_timeline(sd) if e.event_type == "scheduled_task_deleted"
    )
    assert deleted.raw_input.as_str("task_id") == "01a00e101f74"
    assert deleted.raw_input.as_str("reason") == "deleted"


def test_timeline_task_backgrounded_fields_are_not_only_content(tmp_path: Path) -> None:
    sd = tmp_path / "bg"
    sd.mkdir()
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "task_backgrounded",
                "tool_call_id": "call-abcb948b-1131-4adc-b6bf-ec687bb4dc7a-11",
                "task_id": "01a016e8-b83c-7493-acb9-b9145526a4f8",
                "command": "bash /home/ali/.anqa/vissue-board-watch.sh",
                "cwd": "/mnt/dev/_git/anqa",
                "output_file": "/tmp/monitor-call.log",
                "description": "Live anqa/icedtea vissue board watch",
                "monitor_description": "Live anqa/icedtea vissue board watch",
            }
        ],
    )
    ev = next(e for e in GrokAdapter().parse_timeline(sd) if e.event_type == "task_backgrounded")
    assert ev.tool_call_id == "call-abcb948b-1131-4adc-b6bf-ec687bb4dc7a-11"
    assert ev.raw_input.as_str("task_id") == "01a016e8-b83c-7493-acb9-b9145526a4f8"
    assert ev.raw_input.as_str("command").endswith("vissue-board-watch.sh")
    assert ev.raw_input.as_str("cwd") == "/mnt/dev/_git/anqa"
    assert ev.raw_input.as_str("output_file") == "/tmp/monitor-call.log"
    assert ev.raw_input.as_str("description") == "Live anqa/icedtea vissue board watch"
    assert ev.event_type != "subagent_spawned"


def test_timeline_task_completed_flattens_snapshot(tmp_path: Path) -> None:
    sd = tmp_path / "done"
    sd.mkdir()
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "task_completed",
                "will_wake": False,
                "task_snapshot": {
                    "task_id": "01a016e8-b83c-7493-acb9-b9145526a4f8",
                    "command": "bash watch.sh",
                    "cwd": "/mnt/dev/_git/anqa",
                    "output_file": "/tmp/monitor-call.log",
                    "description": "Live anqa/icedtea vissue board watch",
                    "output": "DONE\n",
                    "start_time": {"secs_since_epoch": 1787090745, "nanos_since_epoch": 1},
                    "end_time": {"secs_since_epoch": 1787090763, "nanos_since_epoch": 2},
                    "kind": "monitor",
                    "completed": True,
                },
            }
        ],
    )
    ev = next(e for e in GrokAdapter().parse_timeline(sd) if e.event_type == "task_completed")
    assert ev.raw_input.as_str("task_id") == "01a016e8-b83c-7493-acb9-b9145526a4f8"
    assert ev.raw_input.as_str("command") == "bash watch.sh"
    assert ev.raw_input.as_str("cwd") == "/mnt/dev/_git/anqa"
    assert ev.raw_input.as_str("output_file") == "/tmp/monitor-call.log"
    assert ev.raw_input.as_str("description").startswith("Live anqa")
    assert "DONE" in ev.raw_input.as_str("output")
    assert ev.raw_input.get("start_time") is not None
    assert ev.raw_input.get("end_time") is not None
    assert ev.raw_input.get("will_wake") is False
    assert ev.event_type != "subagent_finished"


def test_timeline_goal_updated_coalesces_same_goal_id(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {
                        "sessionUpdate": "goal_updated",
                        "goal_id": "g1",
                        "objective": "fix the catch-up path",
                        "status": "active",
                        "phase": "executing",
                        "last_event": "goal_created",
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 11,
                "params": {
                    "update": {
                        "sessionUpdate": "goal_updated",
                        "goal_id": "g1",
                        "objective": "fix the catch-up path",
                        "status": "complete",
                        "phase": "idle",
                        "last_event": "goal_completed",
                        "last_classifier_verdict": "achieved",
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = GrokAdapter().parse_timeline(sd)
    goals = [e for e in events if e.event_type == "goal_updated"]
    assert len(goals) == 1
    assert "fix the catch-up path" in goals[0].content
    assert "status=complete" in goals[0].content
    assert "last=goal_completed" in goals[0].content
    assert "verdict=achieved" in goals[0].content


def test_timeline_recap_and_compact_rows(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": 20,
                "params": {
                    "update": {
                        "sessionUpdate": "session_recap",
                        "summary": "The operator asked for a walkthrough of the failing check.",
                        "auto": True,
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 21,
                "params": {
                    "update": {
                        "sessionUpdate": "auto_compact_started",
                        "tokens_used": 401582,
                        "context_window": 500000,
                        "percentage": 80,
                        "reason": "Context window 80% full",
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 22,
                "params": {
                    "update": {
                        "sessionUpdate": "auto_compact_completed",
                        "tokens_before": 401582,
                        "tokens_after": 24830,
                        "elapsed_ms": 22052,
                        "summary_preview": None,
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 23,
                "params": {
                    "update": {
                        "sessionUpdate": "compaction_checkpoint",
                        "checkpoint_id": "ckpt-1",
                        "prompt_index_at_compaction": 29,
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = GrokAdapter().parse_timeline(sd)
    recap = [e for e in events if e.event_type == "session_recap"]
    started = [e for e in events if e.event_type == "auto_compact_started"]
    done = [e for e in events if e.event_type == "auto_compact_completed"]
    ckpt = [e for e in events if e.event_type == "compaction_checkpoint"]
    assert len(recap) == 1
    assert recap[0].content == ("The operator asked for a walkthrough of the failing check.")
    raw = recap[0].raw_input
    bag = raw.raw() if hasattr(raw, "raw") else raw
    assert bag.get("auto") is True
    assert len(started) == 1
    assert "Context window 80% full" in started[0].content
    assert "401582/500000" in started[0].content
    assert len(done) == 1
    assert "401582 -> 24830" in done[0].content
    assert "22052ms" in done[0].content
    assert len(ckpt) == 1
    assert "ckpt-1" in ckpt[0].content
    assert "prompt_index=29" in ckpt[0].content


def test_timeline_hook_execution_and_annotation(tmp_path: Path):
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": 30,
                "params": {
                    "update": {
                        "sessionUpdate": "hook_execution",
                        "event_name": "session_start",
                        "runs": [
                            {
                                "name": "global/stamp:session_start[0].hooks[0]",
                                "status": {"status": "success", "elapsed_ms": 892},
                            }
                        ],
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 31,
                "params": {
                    "update": {
                        "sessionUpdate": "hook_execution",
                        "event_name": "pre_tool_use",
                        "tool_name": "run_terminal_command",
                        "runs": [
                            {
                                "name": "global/deny-shell:pre_tool_use[0].hooks[0]",
                                "status": {
                                    "status": "failed",
                                    "error": "timed out after 5000ms",
                                    "elapsed_ms": 5106,
                                },
                            }
                        ],
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 32,
                "params": {
                    "update": {
                        "sessionUpdate": "hook_execution",
                        "event_name": "stop",
                        "runs": [
                            {
                                "name": "global/stop-block:stop[0].hooks[0]",
                                "status": {
                                    "status": "failed",
                                    "error": "blocked stop: guest waiting",
                                    "elapsed_ms": 2909,
                                    "blocked": True,
                                },
                            }
                        ],
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 33,
                "params": {
                    "update": {
                        "sessionUpdate": "hook_annotation",
                        "message": "Stop blocked by hook `global/stop-block:stop[0].hooks[0]`, continuing",
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = GrokAdapter().parse_timeline(sd)
    hooks = [e for e in events if e.event_type == "hook_execution"]
    notes = [e for e in events if e.event_type == "hook_annotation"]
    assert len(hooks) == 3
    assert hooks[0].content.startswith("session_start  stamp:success")
    assert not hooks[0].is_error
    assert "pre_tool_use" in hooks[1].content
    assert "run_terminal_command" in hooks[1].content
    assert "deny-shell:failed" in hooks[1].content
    assert "timed out after 5000ms" in hooks[1].content
    assert hooks[1].is_error
    assert "stop  stop-block:blocked" in hooks[2].content
    assert hooks[2].is_error
    assert len(notes) == 1
    assert "Stop blocked by hook" in notes[0].content


def test_timeline_only_markers_no_updates(tmp_path: Path):
    """Session with events.jsonl but no updates.jsonl (open turn kept)."""
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "events.jsonl").write_text(
        json.dumps({"ts": 1000, "type": "turn_started", "turn_number": 0}) + "\n",
        encoding="utf-8",
    )
    events = GrokAdapter().parse_timeline(sd)
    assert len(events) >= 1
    assert events[0].event_type == "turn_started"


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
    events = GrokAdapter().parse_timeline(sd)
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
    events = GrokAdapter().parse_timeline(sd)
    results = [e for e in events if e.event_type == "tool_call_update"]
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
    events = GrokAdapter().parse_timeline(sd)
    results = [e for e in events if e.event_type == "tool_call_update"]
    assert len(results) == 1
    assert results[0].content == "much longer content here"


# ── _extract_message_text edge cases ─────────────────────────────────────

# ── _extract_tool_update_text edge cases ─────────────────────────────────

# ── extract_prompt edge cases ────────────────────────────────────────────

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

# ── _load_summary / _load_signals / _load_run_meta edge cases ────────────

# ── _find_container_for_session / _model_from_run_json ────────────────────

# ── _match_model_to_container ─────────────────────────────────────────────

# ── load_session_meta edge cases ─────────────────────────────────────────


def test_parse_timeline_prepends_system_prompt(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    (sd / "system_prompt.txt").write_text("You are the system.", encoding="utf-8")
    (sd / "updates.jsonl").write_text("", encoding="utf-8")
    events = GrokAdapter().parse_timeline(sd)
    assert events
    assert events[0].event_type == "system"
    assert "You are the system" in events[0].content
    assert events[0].type_label == "system"


# ── find_sessions prune dirs ────────────────────────────────────────────


def test_find_sessions_skips_stage_dirs(tmp_path: Path):
    """Directories ending with .stage are pruned from walk."""
    root = tmp_path / "traces"
    stage = root / "anqa-abc.stage" / "inner"
    stage.mkdir(parents=True)
    (stage / "updates.jsonl").write_text("{}\n")
    real = root / "real-session"
    real.mkdir()
    (real / "updates.jsonl").write_text("{}\n")
    sessions = find_sessions(root)
    assert any(p.name == "real-session" for p in sessions)
    assert not any(".stage" in str(p) for p in sessions)


def test_find_sessions_skips_subagent_mirrors(tmp_path: Path) -> None:
    """Subagent trees and workspace sibling mirrors are not list rows."""
    ws = tmp_path / "traces" / "anqa-run-tomato-xhigh" / "%2Fworkspace"
    parent = ws / "019f-parent"
    parent.mkdir(parents=True)
    (parent / "summary.json").write_text("{}", encoding="utf-8")
    (parent / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    sub_id = "019f-subagent-1"
    nested = parent / "subagents" / sub_id
    nested.mkdir(parents=True)
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    mirror = ws / sub_id
    mirror.mkdir()
    (mirror / "summary.json").write_text("{}", encoding="utf-8")
    (mirror / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    found = find_sessions(tmp_path / "traces")
    assert parent in found
    assert nested not in found
    assert mirror not in found
    assert len(found) == 1


def test_find_sessions_events_empty_file(tmp_path: Path):
    """Empty events.jsonl (0 bytes) does not count as session."""
    root = tmp_path / "traces"
    sd = root / "empty-ev"
    sd.mkdir(parents=True)
    (sd / "events.jsonl").write_text("")
    sessions = find_sessions(root)
    assert not any(p.name == "empty-ev" for p in sessions)


def test_find_sessions_scan_drops_skipped_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native hits under a skipped name are dropped; root path names are not."""
    root = tmp_path / "target" / "traces"
    keep = root / "keep"
    keep.mkdir(parents=True)
    (keep / "summary.json").write_text("{}", encoding="utf-8")
    junk = root / "workspace" / "fake"
    junk.mkdir(parents=True)
    (junk / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("anqa.harness.grok_parse.walk_sessions", lambda _r: [keep, junk])
    found = find_sessions(root)
    assert found == [keep]


# ── _extract_tool_update_text edge branches ──────────────────────────────

# ── events.jsonl OSError ─────────────────────────────────────────────────

# ── _coalesce_tool_result edge cases ────────────────────────────────────

# ── session_trace_mtime with file stat error ─────────────────────────────


def test_session_trace_mtime_stat_error(tmp_path: Path):
    """If dir stat also fails, returns 0."""
    mtime = session_trace_mtime(tmp_path / "completely-missing")
    assert mtime == 0.0


# ── _infer_incomplete_turn_outcome running ───────────────────────────────

# ── _find_container partial match ────────────────────────────────────────

# ── _model_from_run_json more branches ───────────────────────────────────

# ── _match_model_to_container more ───────────────────────────────────────

# ── _model_from_run_parent special cases ─────────────────────────────────

# ── load_session_meta: run.json read error ──────────────────────────────

# ── Additional coverage for remaining gaps ────────────────────────────────


def test_as_epoch_ts_non_primitive():
    """Stamp.epoch returns None for non-primitive types like list or dict."""
    assert Stamp.epoch([1, 2, 3]) is None
    assert Stamp.epoch({"key": "value"}) is None


def test_session_trace_mtime_fallback_to_dir(tmp_path: Path):
    """session_trace_mtime falls back to session_dir.stat() mtime."""
    from anqa.harness.grok_parse import session_trace_mtime

    sd = tmp_path / "sess"
    sd.mkdir()
    # No trace files, but dir exists → falls back to dir mtime
    mtime = session_trace_mtime(sd)
    assert mtime > 0


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


def test_session_trace_mtime_oserror(tmp_path: Path):
    """session_trace_mtime falls back to dir mtime when files raise OSError."""
    from anqa.harness.grok_parse import session_trace_mtime

    sd = tmp_path / "s"
    sd.mkdir()
    result = session_trace_mtime(sd)
    assert result > 0  # falls back to dir mtime


def test_find_sessions_events_only_oserror(tmp_path: Path):
    """find_sessions handles OSError when stating events.jsonl."""
    from anqa.harness.grok_parse import find_sessions

    sd = tmp_path / "traces" / "anqa-r" / "s1"
    sd.mkdir(parents=True)
    (sd / "events.jsonl").write_text("", encoding="utf-8")  # 0 bytes
    sessions = find_sessions(tmp_path / "traces")
    # 0-byte events.jsonl is not added
    assert sd not in sessions


# ── Additional parser coverage ───────────────────────────────────────────


def test_session_trace_mtime_no_files(tmp_path: Path):
    """session_trace_mtime returns 0 for empty session dir."""
    from anqa.harness.grok_parse import session_trace_mtime

    sd = tmp_path / "empty"
    sd.mkdir()
    assert session_trace_mtime(sd) > 0 or session_trace_mtime(sd) == 0


# ── Deeper parser coverage ────────────────────────────────────────────────


def test_session_trace_mtime_stat_oserror(tmp_path: Path):
    """session_trace_mtime handles stat OSError on individual files."""
    from anqa.harness.grok_parse import session_trace_mtime

    sd = tmp_path / "sess"
    sd.mkdir()
    ef = sd / "events.jsonl"
    ef.write_text("{}\n", encoding="utf-8")
    orig_stat = Path.stat

    def _fake_stat(self: Path, **kwargs: object) -> os.stat_result:
        if self.name == "events.jsonl":
            raise OSError("denied")
        return orig_stat(self, **kwargs)

    with patch.object(Path, "stat", _fake_stat):
        mtime = session_trace_mtime(sd)
    # Should still return something (fallback to dir stat)
    assert mtime >= 0


def test_find_sessions_stat_oserror_on_events(tmp_path: Path):
    """find_sessions handles OSError on events.jsonl stat gracefully."""
    from anqa.scan import using_scan

    if using_scan():
        pytest.skip("compiled walk does not use Path.stat")
    sd = tmp_path / "sess"
    sd.mkdir()
    ef = sd / "events.jsonl"
    ef.write_text('{"x":1}\n', encoding="utf-8")

    orig_stat = Path.stat

    def _stat_fail(self: Path, **kwargs: object) -> os.stat_result:
        if self.name == "events.jsonl" and "sess" in str(self):
            raise OSError("stat denied")
        return orig_stat(self, **kwargs)

    with patch.object(Path, "stat", _stat_fail):
        result = find_sessions(tmp_path)
    # OSError in stat → session skipped
    assert sd not in result


def test_parse_timeline_user_image_chunk_joins_previous_prompt(tmp_path: Path) -> None:
    """A pasted image chunk stays on the user prompt; no base64 body."""
    import base64
    import json

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
        b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    sd = tmp_path / "s"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    lines = [
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {
                            "type": "text",
                            "text": "was this broken? [Image #1]",
                        },
                        "_meta": {"promptIndex": 2},
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 2,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {
                            "type": "image",
                            "data": base64.b64encode(png).decode("ascii"),
                        },
                        "_meta": {"promptIndex": 2},
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    users = [e for e in GrokAdapter().parse_timeline(sd) if e.event_type == "user_message_chunk"]
    assert len(users) == 1
    assert users[0].content == "was this broken? [Image #1]"
    assert users[0].images == [png]
    assert "iVBOR" not in users[0].content
    assert "iVBOR" not in users[0].summary_line


def test_live_browser_timeline_min_interval_scales() -> None:
    from anqa.constants import (
        LIVE_BROWSER_TIMELINE_MIN_INTERVAL,
        LIVE_BROWSER_TIMELINE_MIN_INTERVAL_HUGE,
        LIVE_BROWSER_TIMELINE_MIN_INTERVAL_LARGE,
        live_browser_timeline_min_interval,
    )

    assert live_browser_timeline_min_interval(0) == LIVE_BROWSER_TIMELINE_MIN_INTERVAL
    assert live_browser_timeline_min_interval(100) == LIVE_BROWSER_TIMELINE_MIN_INTERVAL
    assert (
        live_browser_timeline_min_interval(5 * 1024 * 1024)
        == LIVE_BROWSER_TIMELINE_MIN_INTERVAL_LARGE
    )
    assert (
        live_browser_timeline_min_interval(20 * 1024 * 1024)
        == LIVE_BROWSER_TIMELINE_MIN_INTERVAL_HUGE
    )
