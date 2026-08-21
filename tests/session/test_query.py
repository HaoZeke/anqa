"""Catalog query language: parse tree applied to list columns."""

from __future__ import annotations

from pathlib import Path

from groket.session.query import (
    CatalogQueryRow,
    apply_suggestion,
    finished_prefix,
    row_matches_query,
    suggest_last_token,
    toggle_is_host,
)


def _row(
    *,
    session_id: str = "sess-1",
    title: str = "Fix the palette",
    path: str = "/mnt/dev/_git/fubar/sess-1",
    origin: str = "work",
    error_count: int = 3,
) -> CatalogQueryRow:
    return CatalogQueryRow(
        session_id=session_id,
        title=title,
        model="grok-4",
        status="complete",
        outcome="success",
        origin=origin,
        path=path,
        task_id="eval-a",
        error_count=error_count,
        turn_count=8,
        tool_count=12,
        event_count=40,
        updated_at="2026-08-10T12:00:00+00:00",
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
    assert row_matches_query(row, "in:~/_dev/_git/fubar") is False
    home = Path("/mnt/dev/_git/fubar/sess-1")
    assert row_matches_query(_row(path=str(home)), f"in:{home.parent}")
    assert row_matches_query(row, "in:/mnt/dev/_git/fubar")
    assert not row_matches_query(row, "in:/mnt/dev/_git/other")


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


def test_forgiving_unknown_and_incomplete() -> None:
    row = _row()
    assert finished_prefix("has:workflows AND has:") == "has:workflows"
    assert row_matches_query(row, "has:workflows AND has:")
    assert row_matches_query(row, "palette AND ((")
    assert row_matches_query(row, "unknown:zzz") is False
    assert row_matches_query(_row(title="unknown:zzz"), "unknown:zzz")


def test_suggest_and_apply_and_toggle_host() -> None:
    assert suggest_last_token("h") == ["has:"]
    assert suggest_last_token("has:") == [
        "has:workflows",
        "has:notes",
        "has:findings",
        "has:errors",
    ]
    assert suggest_last_token("is:ho") == ["is:host"]
    assert suggest_last_token("model:gr", models=["grok-4", "other"]) == ["model:grok-4"]
    assert apply_suggestion("has:", "has:workflows") == "has:workflows "
    assert toggle_is_host("") == "is:host"
    assert toggle_is_host("is:host") == ""
    assert toggle_is_host("has:notes") == "has:notes is:host"
    assert toggle_is_host("has:notes is:host") == "has:notes"
