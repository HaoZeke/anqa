"""session/list wire richness: real numEvents + structured context."""

from __future__ import annotations

import json
from pathlib import Path

from anqa.session.catalog import session_catalog_row, session_meta_from_catalog_row
from anqa.session.query import CatalogQueryRow, row_matches_query


def _write_list_fixture(
    root: Path,
    name: str,
    *,
    num_messages: int,
    ctx_pct: int,
    tokens_used: int,
    window: int,
) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": f"Session {name}",
                "num_messages": num_messages,
                "task_id": f"task-{name}",
            }
        ),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text(
        json.dumps(
            {
                "toolCallCount": 4,
                "turnCount": 7,
                "errorCount": 1,
                "sessionDurationSeconds": 99.5,
                "contextWindowUsage": ctx_pct,
                "contextTokensUsed": tokens_used,
                "contextWindowTokens": window,
            }
        ),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_list_session_catalog_newest_first(tmp_path: Path) -> None:
    """session/list catalog is always newest activity first."""
    import os
    import time

    from anqa.session.catalog import list_session_catalog

    traces = tmp_path / "sessions"
    old = _write_list_fixture(
        traces, "old-sess", num_messages=1, ctx_pct=10, tokens_used=1, window=100
    )
    time.sleep(0.05)
    new = _write_list_fixture(
        traces, "new-sess", num_messages=2, ctx_pct=20, tokens_used=2, window=100
    )
    # Bump new session mtime so sortEpoch prefers it even without ISO timestamps.
    now = time.time()
    os.utime(new / "updates.jsonl", (now + 10, now + 10))
    os.utime(old / "updates.jsonl", (now - 100, now - 100))
    rows = list_session_catalog(traces_path=traces, include_host=False)
    ids = [r["sessionId"] for r in rows]
    assert ids[0] == "new-sess"
    assert "old-sess" in ids
    assert rows[0]["sortEpoch"] >= rows[-1]["sortEpoch"]


def test_session_catalog_row_num_events_from_summary_messages(tmp_path: Path) -> None:
    """Work/eval list rows must not leave Events at 0 when summary has messages."""
    traces = tmp_path / "runs" / "traces"
    sd = _write_list_fixture(
        traces,
        "work-rich",
        num_messages=27,
        ctx_pct=40,
        tokens_used=1200,
        window=128_000,
    )
    row = session_catalog_row(sd)
    assert row is not None
    assert row["numEvents"] == 27
    assert row["durationSeconds"] == 99.5
    assert row["toolCallCount"] == 4
    assert row["turnCount"] == 7
    assert row["errorCount"] == 1
    assert row["contextWindowUsagePct"] == 40
    assert row["contextTokensUsed"] == 1200
    assert row["contextWindowTokens"] == 128_000
    assert "40" in str(row["contextUsageCompact"])
    assert (
        "128" in str(row["contextUsageCompact"]) or "k" in str(row["contextUsageCompact"]).lower()
    )


def test_session_meta_hydrate_preserves_context_fraction(tmp_path: Path) -> None:
    """Attach hydrate must rebuild full compact context, not pct-only."""
    traces = tmp_path / "runs" / "traces"
    sd = _write_list_fixture(
        traces,
        "attach-ctx",
        num_messages=12,
        ctx_pct=40,
        tokens_used=1200,
        window=128_000,
    )
    row = session_catalog_row(sd)
    assert row is not None
    meta = session_meta_from_catalog_row(row)
    assert meta is not None
    assert meta.num_events == 12
    assert meta.turn_count == 7
    assert meta.context_window_usage_pct == 40
    assert meta.context_tokens_used == 1200
    assert meta.context_window_tokens == 128_000
    compact = meta.context_usage_compact
    assert "40" in compact
    # Token fraction must survive hydrate (owner-style column).
    assert "1.2" in compact or "1200" in compact or "1k" in compact.lower()


def test_host_catalog_row_also_uses_message_proxy(tmp_path: Path) -> None:
    host = tmp_path / "host"
    sd = _write_list_fixture(
        host,
        "host-rich",
        num_messages=9,
        ctx_pct=10,
        tokens_used=100,
        window=1000,
    )
    row = session_catalog_row(sd)
    assert row is not None
    assert row["numEvents"] == 9


def test_session_catalog_row_has_presence_flags(tmp_path: Path) -> None:
    traces = tmp_path / "runs" / "traces"
    sd = _write_list_fixture(
        traces,
        "has-ents",
        num_messages=3,
        ctx_pct=15,
        tokens_used=100,
        window=1000,
    )
    signals = json.loads((sd / "signals.json").read_text(encoding="utf-8"))
    signals.update(
        {
            "toolFailureCount": 2,
            "agentLinesAdded": 8,
            "agentLinesRemoved": 1,
            "compactionCount": 1,
            "doomLoopWarnings": 1,
        }
    )
    (sd / "signals.json").write_text(json.dumps(signals), encoding="utf-8")
    (sd / "goal").mkdir()
    (sd / "goal" / "state.json").write_text('{"objective": "Ship it"}', encoding="utf-8")
    (sd / "subagents" / "child-a").mkdir(parents=True)
    (sd / "background_tasks_manifest.json").write_text('[{"task_id": "j1"}]', encoding="utf-8")
    (sd / "plan.json").write_text("{}", encoding="utf-8")
    row = session_catalog_row(sd)
    assert row is not None
    assert row["hasGoals"] is True
    assert row["goalCount"] == 1
    assert row["hasSubagents"] is True
    assert row["hasJobs"] is True
    assert row["hasTasks"] is True
    assert row["hasPlan"] is True
    assert row.get("runDir") == ""
    assert row["hasFailures"] is True
    assert row["hasDiff"] is True
    assert row["hasCompaction"] is True
    assert row["hasDoom"] is True
    meta = session_meta_from_catalog_row(row)
    assert meta is not None
    assert meta.has_goals is True
    assert meta.has_failures is True
    assert meta.has_diff is True
    assert meta.has_compaction is True
    rebuilt = CatalogQueryRow.from_meta(meta)
    assert row_matches_query(rebuilt, "has:goal")
    assert row_matches_query(rebuilt, "has:failure has:compaction has:doom")
    assert row_matches_query(CatalogQueryRow.from_wire(row), "has:goal")


def test_session_catalog_row_titles_from_goal_state(tmp_path: Path) -> None:
    """Untitled list rows take the title from ``goal/state.json``."""
    traces = tmp_path / "runs" / "traces"
    sd = traces / "goal-sess"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "goal-sess"}, "generated_title": "", "num_messages": 0}),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text("{}", encoding="utf-8")
    (sd / "goal").mkdir()
    (sd / "goal" / "state.json").write_text(
        json.dumps({"objective": "group the handbook topics"}),
        encoding="utf-8",
    )
    row = session_catalog_row(sd)
    assert row is not None
    assert row["title"] == "group the handbook topics"
    meta = session_meta_from_catalog_row(row)
    assert meta is not None
    assert meta.title == "group the handbook topics"


def test_session_catalog_row_does_not_scan_updates_for_title(tmp_path: Path) -> None:
    """List title does not walk ``updates.jsonl`` for a first ask."""
    traces = tmp_path / "runs" / "traces"
    sd = traces / "updates-only"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "updates-only"}, "generated_title": ""}),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text("{}", encoding="utf-8")
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "original first ask"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    row = session_catalog_row(sd)
    assert row is not None
    assert row["title"] == ""


def test_session_catalog_row_counts_events_when_summary_has_none(tmp_path: Path) -> None:
    """Live sessions without a summary count still get an Events column."""
    from anqa.harness.grok import parse_timeline

    traces = tmp_path / "runs" / "traces"
    sd = traces / "live-host"
    sd.mkdir(parents=True)
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "<user_query>hi</user_query>"},
                    }
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": 2,
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "hello"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    row = session_catalog_row(sd)
    assert row is not None
    n = len(parse_timeline(sd))
    assert n >= 2
    assert row["numEvents"] == 0


def test_session_catalog_row_run_dir_from_encoded_cwd(tmp_path: Path) -> None:
    host = tmp_path / "%2Fmnt%2Fdev%2F_git%2Ffubar"
    sd = _write_list_fixture(
        host,
        "sess-1",
        num_messages=1,
        ctx_pct=1,
        tokens_used=1,
        window=10,
    )
    row = session_catalog_row(sd)
    assert row is not None
    assert row["runDir"] == "/mnt/dev/_git/fubar"
    meta = session_meta_from_catalog_row(row)
    assert meta is not None
    assert meta.run_dir == "/mnt/dev/_git/fubar"
    assert row_matches_query(CatalogQueryRow.from_meta(meta), "in:/mnt/dev/_git/fubar")
