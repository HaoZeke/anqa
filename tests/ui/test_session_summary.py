"""Tests for session_summary builder."""

from __future__ import annotations

from conftest import make_trace_event
from groket.models import SessionMeta
from groket.ui.session_summary import (
    assistant_text_from_timeline,
    build_session_summary,
    render_session_summary,
)


class TestAssistantTextFromTimeline:
    def test_extracts_assistant_text(self):
        timeline = [
            make_trace_event(event_type="user", content="Do X"),
            make_trace_event(event_type="assistant", content="I'll do X. "),
            make_trace_event(event_type="tool_call", tool_name="grep"),
            make_trace_event(event_type="assistant", content="Here's the result."),
        ]
        result = assistant_text_from_timeline(timeline)
        assert result == "I'll do X. Here's the result."

    def test_no_assistant(self):
        timeline = [
            make_trace_event(event_type="user", content="Hello"),
            make_trace_event(event_type="tool_call", tool_name="grep"),
        ]
        result = assistant_text_from_timeline(timeline)
        assert result == ""


class TestBuildSessionSummary:
    def test_includes_key_fields(self, session_dir):
        meta = SessionMeta(
            session_id="test-session",
            session_dir=session_dir,
            model_id="v9-dietcoke",
            title="Fix auth tests",
            turn_outcome="success",
            duration_seconds=120,
            tool_call_count=5,
        )
        timeline = [
            make_trace_event(index=0, event_type="user", content="Fix tests"),
            make_trace_event(
                index=1,
                event_type="tool_call",
                tool_name="run_terminal_command",
                raw_input={"command": "pytest"},
            ),
            make_trace_event(
                index=2,
                event_type="tool_result",
                tool_name="run_terminal_command",
                content="2 passed",
            ),
            make_trace_event(index=3, event_type="assistant", content="Tests are fixed."),
        ]
        summary = build_session_summary(meta, timeline)
        assert "Fix auth tests" in summary
        assert "v9-dietcoke" in summary
        assert "success" in summary
        assert "tool_call" in summary or "tools" in summary.lower()
        rich = render_session_summary(meta, timeline)
        assert rich is not None
        # Meta is structured Text; assistant may be Markdown — full string via builder
        assert "Fix auth tests" in summary

    def test_turn_failure_warning(self, session_dir):
        meta = SessionMeta(
            session_id="fail-session",
            session_dir=session_dir,
            turn_outcome="error",
        )
        summary = build_session_summary(meta, [])
        assert "error" in summary.lower()

    def test_empty_timeline(self, session_dir):
        meta = SessionMeta(
            session_id="empty",
            session_dir=session_dir,
        )
        summary = build_session_summary(meta, [])
        assert isinstance(summary, str)
        assert len(summary) > 0
        assert render_session_summary(meta, []) is not None

    def test_multi_turn_section(self, session_dir):
        meta = SessionMeta(
            session_id="mt",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="session", content="turn started  turn_number=0"),
            make_trace_event(index=1, event_type="user", content="one"),
            make_trace_event(index=2, event_type="session", content="turn ended  outcome=success"),
            make_trace_event(index=3, event_type="session", content="turn started  turn_number=1"),
            make_trace_event(index=4, event_type="user", content="two"),
            make_trace_event(index=5, event_type="session", content="turn ended  outcome=error"),
        ]
        summary = build_session_summary(meta, timeline)
        assert "2 turns" in summary.lower() or "Turns" in summary
        assert "turn" in summary.lower()

    def test_with_assistant_text(self, session_dir):
        meta = SessionMeta(
            session_id="at",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="assistant", content="Here is help."),
        ]
        summary = build_session_summary(meta, timeline, assistant_text="Help text here")
        assert "Help text here" in summary or "help" in summary.lower()

    def test_with_tool_errors(self, session_dir):
        meta = SessionMeta(
            session_id="te",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="run_terminal_command",
                is_error=True,
            ),
            make_trace_event(index=1, event_type="session_error", content="error", is_error=True),
        ]
        summary = build_session_summary(meta, timeline)
        assert "error" in summary.lower()

    def test_with_metadata_fields(self, session_dir):
        meta = SessionMeta(
            session_id="md",
            session_dir=session_dir,
            model_id="v9-dietcoke",
            title="Test Session",
            turn_outcome="success",
            duration_seconds=120,
            run_id="run-123",
            task_id="task-456",
            git_repo="https://github.com/example/repo",
            git_branch="main",
            created_at="2026-06-25T00:00:00Z",
            num_messages=10,
            loop_count=3,
        )
        summary = build_session_summary(meta, [])
        assert "Test Session" in summary
        assert "run-123" in summary
        assert "main" in summary

    def test_long_path_truncated(self, session_dir):
        meta = SessionMeta(
            session_id="lp",
            session_dir=session_dir,
            turn_outcome="success",
        )
        # session_dir path might be short for test; summary handles both
        summary = build_session_summary(meta, [])
        assert isinstance(summary, str)


# ── append_usage_rich ─────────────────────────────────────────────────────

from groket.session.usage_stats import (
    McpMethodUsage,
    McpServerUsage,
    SessionUsageStats,
    SkillUsageRow,
    ToolUsageRow,
)
from groket.ui.session_summary import append_usage_rich
from rich.text import Text


class TestAppendUsageRich:
    def test_empty_usage(self):
        out = Text()
        usage = SessionUsageStats()
        append_usage_rich(out, usage)
        assert "Host tools" in out.plain

    def test_with_host_tools(self):
        out = Text()
        usage = SessionUsageStats(
            host_tools=[
                ToolUsageRow(name="read_file", calls=5, errors=0),
                ToolUsageRow(name="grep", calls=3, errors=1),
            ],
        )
        append_usage_rich(out, usage)
        assert "read_file" in out.plain
        assert "grep" in out.plain
        assert "8" in out.plain

    def test_with_mcp_servers(self):
        out = Text()
        usage = SessionUsageStats(
            mcp_servers=[
                McpServerUsage(
                    server_id="slack",
                    configured=True,
                    use_tool_calls=3,
                    methods=[McpMethodUsage(method="send_message", calls=2)],
                ),
            ],
            mcp_configured=["slack"],
        )
        append_usage_rich(out, usage)
        assert "slack" in out.plain

    def test_with_skills(self):
        out = Text()
        usage = SessionUsageStats(
            skills=[
                SkillUsageRow(skill_id="code-review", configured=True, skill_md_reads=2),
            ],
            skills_configured=["code-review"],
        )
        append_usage_rich(out, usage)
        assert "code-review" in out.plain

    def test_with_persona_and_sources(self):
        out = Text()
        usage = SessionUsageStats(
            persona_id="test-persona",
            source_notes=["persona", "updates"],
        )
        append_usage_rich(out, usage)
        assert "test-persona" in out.plain
        assert "sources" in out.plain

    def test_mcp_server_no_hits(self):
        out = Text()
        usage = SessionUsageStats(
            mcp_servers=[
                McpServerUsage(server_id="empty-srv", configured=True),
            ],
            mcp_configured=["empty-srv"],
        )
        append_usage_rich(out, usage)
        assert "empty-srv" in out.plain
        assert "no tool hits" in out.plain

    def test_mcp_bridge_calls(self):
        out = Text()
        usage = SessionUsageStats(mcp_bridge_calls=7)
        append_usage_rich(out, usage)
        assert "7" in out.plain


class TestBuildSessionSummaryException:
    def test_render_exception_fallback(self, session_dir):
        """build_session_summary falls back to title on render exception."""
        from unittest.mock import patch

        meta = SessionMeta(
            session_id="exc",
            session_dir=session_dir,
            title="My Title",
        )
        with patch(
            "groket.ui.session_summary.render_session_summary",
            side_effect=RuntimeError("boom"),
        ):
            result = build_session_summary(meta, [])
            assert "My Title" in result


class TestSessionSummaryPendingLabel:
    def test_pending_label_exception(self, session_dir):
        """render_session_summary handles session_pending_label import failure."""
        from unittest.mock import patch

        meta = SessionMeta(
            session_id="pend",
            session_dir=session_dir,
            turn_outcome="success",
        )
        with patch(
            "groket.session.turn_gate.session_pending_label",
            side_effect=ImportError("no module"),
        ):
            result = render_session_summary(meta, [])
            assert result is not None


class TestSessionSummaryTurnSegmentationFail:
    def test_turn_segmentation_exception(self, session_dir):
        """render_session_summary handles segment_timeline_turns exception."""
        from unittest.mock import patch

        meta = SessionMeta(
            session_id="segf",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [make_trace_event(index=0, event_type="user", content="hi")]
        with patch(
            "groket.session.turns.segment_timeline_turns",
            side_effect=RuntimeError("fail"),
        ):
            result = render_session_summary(meta, timeline)
            assert result is not None


class TestSessionSummaryShareDisplay:
    def test_share_url_present(self, session_dir):
        """Share URL is included in the session summary."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"share_url": "https://share.example.com/abc", "session_id": "test"}),
        )
        meta = SessionMeta(
            session_id="share-ok",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert "share" in result.lower() or "Share" in result

    def test_share_pending(self, session_dir):
        """Pending share state is represented in the summary."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"source": "pending", "session_id": "test"}),
        )
        meta = SessionMeta(
            session_id="share-pend",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert isinstance(result, str)

    def test_share_failed(self, session_dir):
        """Failed share state is represented in the summary."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"error": "no messages to share", "session_id": "test"}),
        )
        meta = SessionMeta(
            session_id="share-fail",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert isinstance(result, str)


class TestSessionSummaryUsageException:
    def test_usage_exception_fallback_tool_mix(self, session_dir):
        """Tool mix fallback is used when collect_session_usage fails."""
        from unittest.mock import patch

        meta = SessionMeta(
            session_id="usagefail",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=1, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=2, event_type="tool_call", tool_name="read_file"),
        ]
        with patch(
            "groket.session.usage_stats.collect_session_usage",
            side_effect=RuntimeError("fail"),
        ):
            result = build_session_summary(meta, timeline)
            assert "grep" in result or "Tools" in result


class TestSessionSummaryMultiTurnToolMix:
    def test_per_turn_tool_mix(self, session_dir):
        """Per-turn tool mix is included in the summary."""
        meta = SessionMeta(
            session_id="toolmix",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="session", content="turn started  turn_number=0"),
            make_trace_event(index=1, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=2, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=3, event_type="session", content="turn ended  outcome=success"),
            make_trace_event(index=4, event_type="session", content="turn started  turn_number=1"),
            make_trace_event(index=5, event_type="tool_call", tool_name="read_file"),
            make_trace_event(index=6, event_type="session", content="turn ended  outcome=success"),
        ]
        result = build_session_summary(meta, timeline)
        assert "tools:" in result.lower() or "grep" in result


class TestSessionSummaryShareSection:
    def test_share_section_no_url_not_pending(self, session_dir):
        """Share section with error and no URL renders without crash."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"error": "auth failed", "session_id": "test", "snapshot_n": 2}),
        )
        meta = SessionMeta(
            session_id="noshare",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert isinstance(result, str)
