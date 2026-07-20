"""Turn filter dropdown rebuilds when a follow-up turn appears live."""

from __future__ import annotations

from pathlib import Path

from groket.models import TraceEvent
from groket.ui.screens.browser import BrowserScreen


def _ev(index: int, event_type: str, content: str = "", **kw) -> TraceEvent:
    return TraceEvent(
        index=index,
        timestamp=float(1000 + index),
        event_type=event_type,
        content=content,
        **kw,
    )


def test_rebuild_turn_select_discovers_follow_up_mid_batch(tmp_path: Path) -> None:
    """turn_started + later tool events in one load must still show multi-turn UI."""
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "agent_message_chunk", "reply"),
        _ev(3, "turn_ended", "outcome=completed"),
    ]
    screen._last_turn_segment_count = 1
    screen._turn_segments = []  # pretend already segmented once
    screen._turn_filter = "all"

    # Capture set_options / display without mounting.
    calls: list[object] = []

    class _Sel:
        display = False
        value = "all"

        def set_options(self, options):
            calls.append(list(options))

    sel = _Sel()
    screen.query_one = lambda _q, _t=None: sel  # type: ignore[method-assign]

    # Follow-up arrived with turn_started buried under newer tool rows.
    screen.timeline = [
        *screen.timeline,
        _ev(4, "turn_started", "turn_number=1"),
        _ev(5, "user_message_chunk", "follow up please"),
        _ev(6, "tool_call", "bash"),
        _ev(7, "tool_call_update", "ok"),
    ]
    screen._rebuild_turn_select()
    assert sel.display is True
    assert screen._last_turn_segment_count == 2
    assert calls, "set_options should run when becoming multi-turn"
    values = [v for _, v in calls[-1]]
    assert "0" in values and "1" in values and "all" in values


def test_rebuild_turn_select_skips_mid_turn_when_already_multi(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "turn_ended", "outcome=completed"),
        _ev(2, "turn_started", "turn_number=1"),
        _ev(3, "tool_call", "bash"),
    ]
    screen._last_turn_segment_count = 2
    screen._turn_segments = [object(), object()]  # non-None
    screen._turn_filter = "all"
    calls: list[object] = []

    class _Sel:
        display = True
        value = "all"

        def set_options(self, options):
            calls.append(options)

    screen.query_one = lambda _q, _t=None: _Sel()  # type: ignore[method-assign]
    screen.timeline.append(_ev(4, "tool_call_update", "more"))
    screen._rebuild_turn_select()
    assert calls == []
    assert screen._last_turn_segment_count == 2
