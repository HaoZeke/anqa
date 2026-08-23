"""Catalog query language: parse tree applied to list columns."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from groket.integrations.control_contract import catalog_query_values
from groket.session.query import (
    HAS_VALUES,
    CatalogQueryRow,
    QuerySpan,
    apply_suggestion,
    catalog_has_goals,
    catalog_has_jobs,
    catalog_has_plan,
    catalog_has_schedules,
    catalog_has_subagents,
    catalog_has_tasks,
    finished_prefix,
    highlight_query_spans,
    row_matches_query,
    suggest_last_token,
)

HAS_TOKENS = (
    "workflows",
    "notes",
    "goals",
    "subagents",
    "tasks",
    "jobs",
    "schedules",
    "plan",
    "errors",
    "failures",
    "diff",
    "git",
    "context",
    "compaction",
    "doom",
)


def _row(
    *,
    session_id: str = "sess-1",
    title: str = "Fix the palette",
    path: str = "/mnt/dev/_git/fubar/sess-1",
    git_repo: str = "/mnt/dev/_git/fubar",
    run_dir: str = "/mnt/dev/_git/fubar",
    origin: str = "work",
    error_count: int = 3,
    duration_seconds: int = 0,
    updated_at: str = "2026-08-10T12:00:00+00:00",
) -> CatalogQueryRow:
    return CatalogQueryRow(
        session_id=session_id,
        title=title,
        model="grok-4",
        status="complete",
        outcome="success",
        origin=origin,
        path=path,
        git_repo=git_repo,
        run_dir=run_dir,
        task_id="eval-a",
        error_count=error_count,
        turn_count=8,
        tool_count=12,
        event_count=40,
        duration_seconds=duration_seconds,
        updated_at=updated_at,
        has_workflows=True,
        has_notes=False,
        has_goals=False,
        has_subagents=False,
        has_jobs=False,
        has_schedules=False,
        has_plan=False,
        has_failures=False,
        has_diff=False,
        has_compaction=False,
        has_doom=False,
        has_context=False,
    )


def test_bare_words_match_title_and_id_not_path() -> None:
    row = _row()
    assert row_matches_query(row, "palette")
    assert row_matches_query(row, "SESS-1")
    assert not row_matches_query(row, "_git/fubar")
    assert not row_matches_query(row, "grok-4")


def test_implicit_and_and_or() -> None:
    row = _row()
    assert row_matches_query(row, "has:workflows is:eval")
    assert row_matches_query(row, "has:workflows AND errors:>2")
    assert row_matches_query(row, "(is:host OR is:eval) AND errors:>0")
    assert not row_matches_query(row, "is:host AND has:workflows")


def test_has_and_numeric_and_in_path() -> None:
    row = _row()
    assert row_matches_query(row, "has:workflows AND errors:>20") is False
    assert row_matches_query(row, "has:workflows AND errors:>2")
    assert row_matches_query(row, "errors:>=3")
    assert not row_matches_query(row, "errors:>3")
    assert row_matches_query(row, "in:/mnt/dev/_git/fubar")
    assert not row_matches_query(
        _row(git_repo="https://github.com/x/fubar", run_dir=""),
        "in:fubar",
    )
    assert not row_matches_query(row, "in:/mnt/dev/_git/other")
    assert not row_matches_query(_row(run_dir=""), "in:/mnt/dev/_git/fubar")


def test_suggest_in_uses_run_directories() -> None:
    assert suggest_last_token("in:", paths=["/mnt/dev/_git/fubar", "/mnt/dev/_git/fubar"]) == [
        "in:/mnt/dev/_git/fubar"
    ]
    assert suggest_last_token("in:/mnt", paths=["/mnt/dev/_git/fubar"]) == [
        "in:/mnt/dev/_git/fubar"
    ]


def test_is_host_eval_and_not() -> None:
    work = _row(origin="work")
    host = _row(origin="host", title="Host session")
    assert row_matches_query(work, "is:eval")
    assert not row_matches_query(work, "is:host")
    assert row_matches_query(host, "is:host")
    assert row_matches_query(work, "NOT is:host")
    assert row_matches_query(work, "-is:host")


def test_model_task_dates() -> None:
    row = _row()
    assert row_matches_query(row, "model:grok")
    assert row_matches_query(row, "task:eval-a")
    assert row_matches_query(row, "after:2026-08-01")
    assert not row_matches_query(row, "before:2026-08-01")
    assert row_matches_query(row, "before:2026-08-21")


def test_duration_compare() -> None:
    row = _row(duration_seconds=4000)
    assert row_matches_query(row, "duration:>1h")
    assert row_matches_query(row, "duration:>30m")
    assert not row_matches_query(row, "duration:>2h")
    assert row_matches_query(row, "duration:>=4000")
    assert not row_matches_query(_row(duration_seconds=60), "duration:>1h")


def test_incomplete_after_does_not_hide_rows() -> None:
    row = _row()
    assert row_matches_query(row, "after:2026-")
    assert row_matches_query(row, "after:2026-08")
    assert row_matches_query(row, "before:2026-")


def test_human_after_before() -> None:
    now = datetime.now(tz=UTC)
    recent = _row(updated_at=(now - timedelta(hours=2)).isoformat())
    old = _row(updated_at=(now - timedelta(days=20)).isoformat())
    assert row_matches_query(recent, "after:yesterday")
    assert row_matches_query(recent, "after:2d")
    assert row_matches_query(recent, "after:2 days ago")
    assert not row_matches_query(old, "after:yesterday")
    assert not row_matches_query(old, "after:2d")
    assert row_matches_query(old, "before:yesterday")
    assert row_matches_query(old, "before:2 days ago")


def test_forgiving_unknown_and_incomplete() -> None:
    row = _row()
    assert finished_prefix("has:workflows AND has:") == "has:workflows"
    assert row_matches_query(row, "has:workflows AND has:")
    assert row_matches_query(row, "palette AND ((")
    assert row_matches_query(row, "unknown:zzz") is False
    assert row_matches_query(_row(title="unknown:zzz"), "unknown:zzz")


def test_catalog_query_help_lists_schema_tokens() -> None:
    from groket.integrations.control_contract import catalog_query_help_plain

    text = catalog_query_help_plain()
    assert "Bare words match title, id, and label" in text
    assert "is: running" in text
    assert "cancelled" in text
    assert "has: workflows" in text
    assert "doom" in text
    assert "in: Directory the session was run in" in text
    assert "duration:" in text
    assert ">=" in text
    assert "OR" in text
    assert "\n" in text
    for line in text.splitlines():
        assert len(line) <= 72, line


def test_has_tokens_match_published_schema() -> None:
    assert HAS_VALUES == catalog_query_values("has")
    assert HAS_VALUES == HAS_TOKENS
    assert "findings" not in HAS_VALUES
    assert suggest_last_token("has:") == [f"has:{name}" for name in HAS_TOKENS]


def test_highlight_query_spans_uses_schema_only() -> None:
    def kinds(query: str) -> list[tuple[str, str]]:
        return [(query[s.start : s.end], s.kind) for s in highlight_query_spans(query)]

    assert highlight_query_spans("") == ()
    assert highlight_query_spans("palette") == ()
    assert kinds("has:goals") == [("has:", "field"), ("goals", "value")]
    assert kinds("has:gooals") == [("has:", "field"), ("gooals", "unknown")]
    assert kinds("has:g") == [("has:", "field"), ("g", "unknown")]
    assert kinds("AND NOT has:goals") == [
        ("AND", "operator"),
        ("NOT", "operator"),
        ("has:", "field"),
        ("goals", "value"),
    ]
    assert not row_matches_query(_row(title="palette"), "palette and has:notes")
    assert row_matches_query(_row(title="palette"), "palette AND NOT has:notes")
    assert not row_matches_query(_row(title="palette"), "palette AND has:notes")
    assert kinds("and not has:goals") == [
        ("has:", "field"),
        ("goals", "value"),
    ]
    assert kinds("aNd nOt has:goals") == [
        ("has:", "field"),
        ("goals", "value"),
    ]
    assert kinds("-has:notes") == [("-", "operator"), ("has:", "field"), ("notes", "value")]
    assert kinds("after:24h") == [("after:", "field"), ("24h", "value")]
    assert kinds('after:"24 hours ago"') == [("after:", "field"), ('"24 hours ago"', "value")]
    assert kinds("duration:>20 minutes") == [
        ("duration:", "field"),
        (">20 minutes", "value"),
    ]
    assert kinds("errors:>2") == [("errors:", "field"), (">2", "value")]
    assert kinds("is:canceled") == [("is:", "field"), ("canceled", "value")]
    assert kinds("after: 24 hours ago AND NOT has:goals") == [
        ("after:", "field"),
        ("24 hours ago", "value"),
        ("AND", "operator"),
        ("NOT", "operator"),
        ("has:", "field"),
        ("goals", "value"),
    ]
    assert highlight_query_spans("has:") == (QuerySpan(0, 4, "field"),)
    assert kinds("foo-has:goals") == []


@pytest.mark.parametrize(
    ("query", "ops"),
    [
        ("palette AND has:notes", ["AND"]),
        ("palette OR has:notes", ["OR"]),
        ("NOT has:notes", ["NOT"]),
        ("palette AND NOT has:notes", ["AND", "NOT"]),
        ("palette and has:notes", []),
        ("palette or has:notes", []),
        ("not has:notes", []),
        ("palette aNd has:notes", []),
        ("palette AnD NOT has:notes", ["NOT"]),
    ],
)
def test_highlight_operators_are_uppercase_only(query: str, ops: list[str]) -> None:
    painted = [query[s.start : s.end] for s in highlight_query_spans(query) if s.kind == "operator"]
    assert painted == ops


@pytest.mark.parametrize(
    ("query", "want"),
    [
        ("palette AND NOT has:notes", True),
        ("palette and not has:notes", False),
        ("palette aNd NOT has:notes", False),
        ("missing OR has:notes", False),
        ("missing OR NOT has:notes", True),
    ],
)
def test_match_operators_are_uppercase_only(query: str, want: bool) -> None:
    row = _row(title="palette")
    assert row_matches_query(row, query) is want


def test_suggest_and_apply() -> None:
    assert suggest_last_token("") == []
    assert suggest_last_token("   ") == []
    assert suggest_last_token("h") == ["has:"]
    assert suggest_last_token("has:") == [f"has:{name}" for name in HAS_TOKENS]
    assert suggest_last_token("has:g") == ["has:goals", "has:git"]
    assert suggest_last_token("has:sub") == ["has:subagents"]
    assert suggest_last_token("has:ta") == ["has:tasks"]
    assert suggest_last_token("is:ho") == ["is:host"]
    assert suggest_last_token("dur") == ["duration:"]
    assert suggest_last_token("duration:") == [
        "duration:>=",
        "duration:<=",
        "duration:>",
        "duration:<",
        "duration:=",
    ]
    assert suggest_last_token("model:gr", models=["grok-4", "other"]) == ["model:grok-4"]
    assert apply_suggestion("has:", "has:workflows") == "has:workflows "


def test_has_presence_tokens_match_row_flags() -> None:
    empty = _row(error_count=0, git_repo="")
    assert not row_matches_query(empty, "has:errors")
    assert not row_matches_query(empty, "has:goals")
    assert not row_matches_query(empty, "has:subagents")
    assert not row_matches_query(empty, "has:tasks")
    assert not row_matches_query(empty, "has:git")
    full = CatalogQueryRow(
        session_id="sess-1",
        title="Fix the palette",
        origin="work",
        git_repo="/mnt/dev/_git/fubar",
        error_count=1,
        has_workflows=True,
        has_notes=True,
        has_goals=True,
        has_subagents=True,
        has_jobs=True,
        has_schedules=True,
        has_plan=True,
        has_failures=True,
        has_diff=True,
        has_compaction=True,
        has_doom=True,
        has_context=True,
    )
    for name in HAS_TOKENS:
        assert row_matches_query(full, f"has:{name}"), name
    jobs_only = CatalogQueryRow(has_jobs=True)
    schedules_only = CatalogQueryRow(has_schedules=True)
    assert row_matches_query(jobs_only, "has:jobs")
    assert row_matches_query(jobs_only, "has:tasks")
    assert not row_matches_query(jobs_only, "has:schedules")
    assert row_matches_query(schedules_only, "has:schedules")
    assert row_matches_query(schedules_only, "has:tasks")
    assert not row_matches_query(schedules_only, "has:jobs")
    assert not row_matches_query(CatalogQueryRow(git_repo=""), "has:git")
    assert row_matches_query(CatalogQueryRow(git_repo="/tmp/repo"), "has:git")
    assert not row_matches_query(CatalogQueryRow(has_context=False), "has:context")
    assert row_matches_query(CatalogQueryRow(has_context=True), "has:context")


def test_catalog_has_disk_entities(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert catalog_has_goals(empty) is False
    assert catalog_has_subagents(empty) is False
    assert catalog_has_jobs(empty) is False
    assert catalog_has_schedules(empty) is False
    assert catalog_has_tasks(empty) is False
    assert catalog_has_plan(empty) is False

    goal = tmp_path / "goal-sess"
    goal.mkdir()
    (goal / "goal").mkdir()
    (goal / "goal" / "state.json").write_text("{}", encoding="utf-8")
    assert catalog_has_goals(goal)

    kids = tmp_path / "kids"
    kids.mkdir()
    (kids / "subagents" / "child-1").mkdir(parents=True)
    assert catalog_has_subagents(kids)

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "background_tasks_manifest.json").write_text('[{"task_id": "j1"}]', encoding="utf-8")
    assert catalog_has_jobs(jobs)
    assert catalog_has_tasks(jobs)

    term = tmp_path / "term"
    term.mkdir()
    (term / "terminal").mkdir()
    (term / "terminal" / "call-bg.log").write_text("ok\n", encoding="utf-8")
    assert catalog_has_jobs(term)

    sched = tmp_path / "sched"
    sched.mkdir()
    (sched / "resources_state.json").write_text(
        ('{"state":{"grok_build.Scheduler":{"tasks":[{"id":"s1","intervalSecs":60}]}}}'),
        encoding="utf-8",
    )
    assert catalog_has_schedules(sched)
    assert catalog_has_tasks(sched)
    assert not catalog_has_jobs(sched)

    planned = tmp_path / "planned"
    planned.mkdir()
    (planned / "plan.json").write_text("{}", encoding="utf-8")
    assert catalog_has_plan(planned)
    mode = tmp_path / "plan-mode"
    mode.mkdir()
    (mode / "plan_mode.json").write_text("{}", encoding="utf-8")
    assert catalog_has_plan(mode)
