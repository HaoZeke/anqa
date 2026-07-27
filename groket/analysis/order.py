"""Order findings for Findings / Report surfaces."""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Severity, TraceEvent
from ..session.turns import (
    event_display_turn_map,
    segment_timeline_turns,
)
from .base import Finding

# Sort key sentinel: unlinked findings after every turn-linked row.
_UNLINKED = 10**9


def _tool_call_event_index(timeline: Sequence[TraceEvent]) -> dict[str, int]:
    by_tcid: dict[str, int] = {}
    for ev in timeline:
        tcid = (ev.tool_call_id or "").strip()
        if tcid and tcid not in by_tcid:
            by_tcid[tcid] = int(ev.index)
    return by_tcid


def _anchor_event_indices(
    finding: Finding,
    tool_call_to_index: dict[str, int],
) -> list[int]:
    """Event indices that locate *finding* on the timeline (earliest first)."""
    indices = list(finding.all_event_indices)
    if indices:
        return indices
    for tcid in finding.all_tool_call_ids:
        idx = tool_call_to_index.get(tcid)
        if idx is not None:
            indices.append(idx)
    return indices


def sort_findings_by_turn(
    findings: Sequence[Finding],
    timeline: Sequence[TraceEvent],
) -> list[Finding]:
    """Stable chronological order: turn → earliest evidence → severity → title.

    Prefer harness turn numbers from timeline markers. Findings with no
    event/tool anchors sort last (still ordered by severity within that group).

    :param findings: Findings from one or more plugins.
    :param timeline: Session timeline used to map evidence → turns.
    :returns: New list; input order is not mutated.
    """
    if not findings:
        return []
    segments = segment_timeline_turns(list(timeline))
    turn_by_event = event_display_turn_map(segments)
    tool_call_to_index = _tool_call_event_index(timeline)

    def sort_key(f: Finding) -> tuple[int, int, Severity, str, str]:
        anchors = _anchor_event_indices(f, tool_call_to_index)
        turns = [turn_by_event[i] for i in anchors if i in turn_by_event]
        turn = min(turns) if turns else _UNLINKED
        earliest = min(anchors) if anchors else _UNLINKED
        return (turn, earliest, f.severity, f.title or "", f.id)

    return sorted(findings, key=sort_key)
