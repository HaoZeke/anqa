"""Timeline turn segmentation for multi-turn stats/summary/report."""

from __future__ import annotations

from groket.models import TraceEvent
from groket.session.turns import (
    format_turns_plain,
    segment_timeline_turns,
    turn_summary_rows,
)


def _ev(index: int, etype: str, content: str = "", **kw) -> TraceEvent:
    return TraceEvent(
        index=index,
        event_type=etype,
        content=content,
        timestamp=kw.get("ts", 1_000_000 + index * 10),
        tool_name=kw.get("tool", ""),
        is_error=kw.get("err", False),
    )


def test_no_markers_single_open_segment():
    tl = [_ev(0, "user", "hi"), _ev(1, "assistant", "yo")]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].open is True
    assert segs[0].event_count == 2
    assert segs[0].turn_index == 0


def test_two_turns_with_markers():
    tl = [
        _ev(0, "session", "turn started  turn_number=0"),
        _ev(1, "user", "first"),
        _ev(2, "tool_call", tool="grep"),
        _ev(3, "session", "turn ended  outcome=success"),
        _ev(4, "session", "turn started  turn_number=1"),
        _ev(5, "user", "second"),
        _ev(6, "tool_call", tool="bash", err=True),
        _ev(7, "session", "turn ended  outcome=success"),
    ]
    # fix tool_name kw - TraceEvent uses tool_name
    tl[2] = TraceEvent(index=2, event_type="tool_call", tool_name="grep", timestamp=1_000_020)
    tl[6] = TraceEvent(
        index=6, event_type="tool_call", tool_name="bash", is_error=True, timestamp=1_000_060
    )
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].outcome == "success"
    assert segs[0].tool_call_count == 1
    assert segs[1].tool_error_count == 1
    assert segs[1].user_count == 1
    rows = turn_summary_rows(segs)
    assert rows[0]["tools"] == 1
    assert rows[0]["turn"] == 0
    assert "turn" in format_turns_plain(segs).lower()


def test_open_second_turn():
    tl = [
        _ev(0, "session", "turn started  turn_number=0"),
        _ev(1, "session", "turn ended  outcome=success"),
        _ev(2, "session", "turn started  turn_number=1"),
        _ev(3, "assistant", "still going"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].open is False
    assert segs[1].open is True


def test_empty_timeline_returns_empty():
    assert segment_timeline_turns([]) == []


def test_turn_label_with_outcome():
    seg = segment_timeline_turns(
        [
            _ev(0, "session", "turn started  turn_number=0"),
            _ev(1, "session", "turn ended  outcome=success"),
        ]
    )
    assert "success" in seg[0].label
    assert "0" in seg[0].label


def test_turn_label_plain_number():
    """When no markers, label uses turn_index 0."""
    tl = [_ev(0, "user", "hi")]
    seg = segment_timeline_turns(tl)[0]
    assert "0" in seg.label


def test_duration_seconds_large_delta_treated_as_ms():
    """Timestamps with absurd seconds delta should be treated as milliseconds."""
    seg = segment_timeline_turns(
        [_ev(0, "user", "hi", ts=0), _ev(1, "assistant", "bye", ts=86_400 * 365 + 1)]
    )
    dur = seg[0].duration_seconds()
    assert dur is not None
    # delta > 86400*365 triggers ms conversion
    assert dur == (86_400 * 365 + 1) / 1000.0


def test_duration_seconds_from_durations_map():
    """When fewer than 2 timestamps available, use the durations dict fallback."""
    tl = [_ev(0, "user", "hi")]
    tl[0].timestamp = None
    seg = segment_timeline_turns(tl)[0]
    # No timestamps, no durations map → None
    assert seg.duration_seconds() is None
    # With durations map for the event
    assert seg.duration_seconds(durations={0: 2.5}) == 2.5


def test_duration_seconds_durations_map_no_match():
    """Durations map present but no matching indices → None."""
    tl = [_ev(0, "user", "hi")]
    tl[0].timestamp = None
    seg = segment_timeline_turns(tl)[0]
    assert seg.duration_seconds(durations={99: 1.0}) is None


def test_turn_number_from_event_no_match():
    """Turn number regex returns None when content has no turn_number=N."""
    tl = [
        _ev(0, "session", "turn started"),
        _ev(1, "session", "turn ended  outcome=unknown"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    # turn_number assigned from sequential 0-based index
    assert segs[0].turn_number == 0
    assert segs[0].turn_index == 0


def test_turn_ended_before_started_creates_segment():
    """A turn_ended appearing before any turn_started produces a segment."""
    tl = [_ev(0, "session", "turn ended  outcome=error")]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].outcome == "error"
    assert segs[0].open is False
    assert segs[0].turn_index == 0


def test_preamble_events_before_first_start():
    """Events before first turn_started form preamble turn 0."""
    tl = [
        _ev(0, "user", "preamble question"),
        _ev(1, "session", "turn started  turn_number=0"),
        _ev(2, "assistant", "reply"),
        _ev(3, "session", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].event_count == 1  # preamble
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[1].turn_number == 0  # harness number
    assert segs[1].outcome == "success"


def test_previous_turn_closed_on_new_start():
    """Turn started while previous is open → close previous."""
    tl = [
        _ev(0, "session", "turn started  turn_number=0"),
        _ev(1, "tool_call"),
        _ev(2, "session", "turn started  turn_number=1"),
        _ev(3, "assistant", "done"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].open is False
    assert segs[0].outcome == "unknown"
    assert segs[1].open is True


def test_error_event_count():
    tl = [_ev(0, "session_error", "boom", err=True), _ev(1, "user", "hi")]
    seg = segment_timeline_turns(tl)[0]
    assert seg.error_event_count == 1


def test_format_turns_plain_empty():
    assert format_turns_plain([]) == "(no turns)"


def test_format_turns_plain_with_duration():
    tl = [
        _ev(0, "session", "turn started  turn_number=1"),
        _ev(1, "session", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    text = format_turns_plain(segs, durations={0: 1.0, 1: 2.0})
    assert "turn" in text.lower()


def test_turn_summary_rows_structure():
    tl = [_ev(0, "user", "hi"), _ev(1, "assistant", "bye")]
    segs = segment_timeline_turns(tl)
    rows = turn_summary_rows(segs)
    assert len(rows) == 1
    row = rows[0]
    assert "turn" in row
    assert "label" in row
    assert "events" in row
    assert row["users"] == 1
    assert row["assistants"] == 1


def test_first_last_index_empty():
    from groket.session.turns import TurnSegment

    seg = TurnSegment(turn_index=1, turn_number=1, events=[])
    assert seg.first_index is None
    assert seg.last_index is None


def test_turn_label_no_outcome_no_open():
    """Closed turn without outcome → plain label."""
    from groket.session.turns import TurnSegment

    seg = TurnSegment(turn_index=3, turn_number=3, open=False, outcome="")
    assert seg.label == "turn 3"


def test_harness_zero_based_preserved():
    """Harness turn_number=0 is not renumbered to 1."""
    tl = [
        _ev(0, "session", "turn started  turn_number=0"),
        _ev(1, "session", "turn ended  outcome=success"),
        _ev(2, "session", "turn started  turn_number=1"),
        _ev(3, "session", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_index for s in segs] == [0, 1]
    assert [s.turn_number for s in segs] == [0, 1]
