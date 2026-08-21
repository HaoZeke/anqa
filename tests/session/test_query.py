"""Catalog query language: parse tree applied to list columns."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from groket.session.query import (
    CatalogQueryRow,
    apply_suggestion,
    finished_prefix,
    row_matches_query,
    suggest_last_token,
)


def _row(
    *,
    session_id: str = "sess-1",
    title: str = "Fix the palette",
    path: str = "/mnt/dev/_git/fubar/sess-1",
    git_repo: str = "/mnt/dev/_git/fubar",
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
        task_id="eval-a",
        error_count=error_count,
        turn_count=8,
        tool_count=12,
        event_count=40,
        duration_seconds=duration_seconds,
        updated_at=updated_at,
        has_workflows=True,
        has_findings=True,
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
    assert row_matches_query(_row(git_repo="https://github.com/x/fubar"), "in:fubar")
    assert not row_matches_query(row, "in:/mnt/dev/_git/other")
    assert not row_matches_query(_row(git_repo=""), "in:/mnt/dev/_git/fubar")


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


def test_suggest_and_apply() -> None:
    assert suggest_last_token("h") == ["has:"]
    assert suggest_last_token("has:") == [
        "has:workflows",
        "has:notes",
        "has:errors",
    ]
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
