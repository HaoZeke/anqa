"""Classify subagent sessions, resolve child paths, and build turn-linked runs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject, JsonValue, ToolInputBag, TraceEvent, as_json_object
from .turns import TurnSegment

_KIND_KEYS = ("session_kind", "sessionKind")
_SPAWN_CHILD_KEYS = ("child_session_id", "childSessionId")
_SPAWN_ID_KEYS = ("subagent_id", "subagentId")
_SPAWN_PROMPT_KEYS = ("parent_prompt_id", "parentPromptId")
_SPAWN_TYPE_KEYS = ("subagent_type", "subagentType")
_SPAWN_DESC_KEYS = ("description",)
_FINISH_STATUS_KEYS = ("status",)
_FINISH_DUR_KEYS = ("duration_ms", "durationMs")
_FINISH_TOOLS_KEYS = ("tool_calls", "toolCalls")
_FINISH_TURNS_KEYS = ("turns",)
_FINISH_TOKENS_KEYS = ("tokens_used", "tokensUsed")
_FINISH_OUTPUT_KEYS = ("output",)


def is_subagent_kind(kind: str) -> bool:
    """True when *kind* is a harness subagent (not an operator fork)."""
    return (kind or "").strip().lower().startswith("subagent")


def compact_child_chrome(kind: str, turn_count: int) -> bool:
    """True when a subagent session has exactly one operator turn.

    Hide the Turns pane and the Events turn picker. If a child later has
    more than one turn, the full chrome comes back.
    """
    return is_subagent_kind(kind) and turn_count == 1


def read_session_kind(path: Path) -> str:
    """Return ``session_kind`` from *path*/``summary.json``, or empty."""
    sj = path / "summary.json"
    if not sj.is_file():
        return ""
    try:
        raw = json.loads(sj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(raw, dict):
        return ""
    return _first_str(raw, _KIND_KEYS)


def is_nested_subagent_stub(path: Path) -> bool:
    """True when *path* is ``<parent-session>/subagents/<child-id>``."""
    return path.parent.name == "subagents"


def is_subagent_session_dir(path: Path) -> bool:
    """True when *path* is a nested stub or a subagent-kind session."""
    if is_nested_subagent_stub(path):
        return True
    return is_subagent_kind(read_session_kind(path))


def nested_child_ids(sessions: list[Path]) -> set[str]:
    """Basenames listed under any *sessions* entry's ``subagents/``."""
    ids: set[str] = set()
    for session in sessions:
        sub_root = session / "subagents"
        if not sub_root.is_dir():
            continue
        try:
            with os.scandir(sub_root) as it:
                for ent in it:
                    if ent.is_dir(follow_symlinks=False):
                        ids.add(ent.name)
        except OSError:
            continue
    return ids


def drop_subagent_sessions(sessions: list[Path]) -> list[Path]:
    """Keep primary sessions only (path, kind, or known nested child id)."""
    if not sessions:
        return sessions
    child_ids = nested_child_ids(sessions)
    kept: list[Path] = []
    for session in sessions:
        if is_nested_subagent_stub(session):
            continue
        if is_subagent_kind(read_session_kind(session)):
            continue
        if session.name in child_ids:
            continue
        kept.append(session)
    return kept


def is_full_session_mirror(path: Path) -> bool:
    """True when *path* has a full child timeline (not only a nested stub)."""
    if not path.is_dir():
        return False
    return (path / "updates.jsonl").is_file() or (path / "summary.json").is_file()


def resolve_child_session_path(
    parent_dir: Path,
    child_session_id: str,
    *,
    search_roots: list[Path] | None = None,
) -> Path | None:
    """Return a full child session dir, preferring a mirror over a nested stub.

    :param parent_dir: Parent session directory.
    :param child_session_id: Child session basename.
    :param search_roots: Extra roots (token dir, host/work sessions). Token
        parent and sessions root are always probed.
    :returns: Path with ``updates.jsonl`` or ``summary.json``, or None.
    """
    cid = (child_session_id or "").strip()
    if not cid:
        return None
    sibling = parent_dir.parent / cid
    if is_full_session_mirror(sibling):
        return sibling
    roots: list[Path] = []
    token = parent_dir.parent
    sessions_root = token.parent
    for root in (token, sessions_root, *(search_roots or ())):
        if root not in roots:
            roots.append(root)
    for root in roots:
        hit = _named_session_under(root, cid)
        if hit is not None and is_full_session_mirror(hit):
            return hit
    return None


def _named_session_under(root: Path, name: str) -> Path | None:
    if not root.is_dir():
        return None
    direct = root / name
    if is_full_session_mirror(direct):
        return direct
    try:
        with os.scandir(root) as it:
            for ent in it:
                if not ent.is_dir(follow_symlinks=False):
                    continue
                cand = Path(ent.path) / name
                if is_full_session_mirror(cand):
                    return cand
    except OSError:
        return None
    return None


@dataclass
class SubagentRun:
    """One subagent run linked to a parent turn."""

    subagent_id: str
    child_session_id: str
    child_path: Path | None
    subagent_type: str
    description: str
    status: str
    parent_turn_index: int | None
    parent_prompt_id: str
    spawn_event_index: int | None
    finish_event_index: int | None
    duration_ms: int | None
    tool_calls: int | None
    turns: int | None
    tokens_used: int | None
    output_preview: str

    @property
    def openable(self) -> bool:
        return self.child_path is not None and is_full_session_mirror(self.child_path)


def spawn_fields(update: Mapping[str, JsonValue]) -> dict[str, str]:
    """Pick spawn identity fields from a Grok ``subagent_spawned`` update."""
    return {
        "child_session_id": _first_str(update, _SPAWN_CHILD_KEYS),
        "subagent_id": _first_str(update, _SPAWN_ID_KEYS),
        "parent_prompt_id": _first_str(update, _SPAWN_PROMPT_KEYS),
        "subagent_type": _first_str(update, _SPAWN_TYPE_KEYS),
        "description": _first_str(update, _SPAWN_DESC_KEYS),
    }


def finish_fields(update: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Pick finish stats from a Grok ``subagent_finished`` update."""
    return {
        "child_session_id": _first_str(update, _SPAWN_CHILD_KEYS),
        "subagent_id": _first_str(update, _SPAWN_ID_KEYS),
        "status": _first_str(update, _FINISH_STATUS_KEYS),
        "duration_ms": _first_int(update, _FINISH_DUR_KEYS),
        "tool_calls": _first_int(update, _FINISH_TOOLS_KEYS),
        "turns": _first_int(update, _FINISH_TURNS_KEYS),
        "tokens_used": _first_int(update, _FINISH_TOKENS_KEYS),
        "output": _first_str(update, _FINISH_OUTPUT_KEYS),
    }


def normalize_run_status(raw: str, *, finished: bool) -> str:
    """Map harness status to ``running`` | ``completed`` | ``cancelled`` | ``failed``."""
    s = (raw or "").strip().lower()
    if "cancel" in s:
        return "cancelled"
    if any(tok in s for tok in ("fail", "error", "abort")):
        return "failed"
    if finished or s in {"done", "complete", "completed", "success", "ok"}:
        return "completed"
    return "running"


def subagent_runs_for_session(
    parent_dir: Path,
    events: list[TraceEvent],
    segments: list[TurnSegment],
    turn_by_index: Mapping[int, int],
    *,
    search_roots: list[Path] | None = None,
) -> list[SubagentRun]:
    """Build turn-linked runs from spawn/finish events and ``subagents/*/meta.json``."""
    by_key: dict[str, SubagentRun] = {}
    for ev in events:
        if ev.event_type == "subagent_spawned":
            _apply_spawn(by_key, ev, parent_dir, search_roots)
        elif ev.event_type == "subagent_finished":
            _apply_finish(by_key, ev, parent_dir, search_roots)
    _merge_meta_dirs(by_key, parent_dir, search_roots)
    for run in by_key.values():
        _link_turn(run, segments, turn_by_index)
    return sorted(
        by_key.values(),
        key=lambda r: (
            r.parent_turn_index if r.parent_turn_index is not None else 10_000,
            r.spawn_event_index if r.spawn_event_index is not None else 10_000,
            r.child_session_id or r.subagent_id,
        ),
    )


def subagent_run_mapping(run: SubagentRun) -> JsonObject:
    """Wire mapping for one run (turns / get / overview)."""
    path = str(run.child_path) if run.child_path is not None else ""
    return {
        "subagentId": run.subagent_id,
        "childSessionId": run.child_session_id,
        "childPath": path,
        "openable": run.openable,
        "subagentType": run.subagent_type,
        "description": run.description,
        "status": run.status,
        "turnIndex": run.parent_turn_index,
        "parentPromptId": run.parent_prompt_id,
        "spawnEventIndex": run.spawn_event_index,
        "finishEventIndex": run.finish_event_index,
        "durationMs": run.duration_ms,
        "toolCalls": run.tool_calls,
        "turns": run.turns,
        "tokensUsed": run.tokens_used,
        "outputPreview": run.output_preview,
    }


def event_subagent_fields(event: TraceEvent) -> JsonObject:
    """Child id / stats for a spawn or finish timeline row."""
    bag = event.raw_input.raw() if isinstance(event.raw_input, ToolInputBag) else {}
    if not isinstance(bag, dict):
        return {}
    out: JsonObject = {}
    child = _first_str(bag, _SPAWN_CHILD_KEYS)
    if child:
        out["childSessionId"] = child
    sid = _first_str(bag, _SPAWN_ID_KEYS)
    if sid:
        out["subagentId"] = sid
    typ = _first_str(bag, _SPAWN_TYPE_KEYS)
    if typ:
        out["subagentType"] = typ
    st = _first_str(bag, _FINISH_STATUS_KEYS)
    if st:
        out["subagentStatus"] = normalize_run_status(
            st, finished=event.event_type == "subagent_finished"
        )
    dur = _first_int(bag, _FINISH_DUR_KEYS)
    if dur is not None:
        out["durationMs"] = dur
    tools = _first_int(bag, _FINISH_TOOLS_KEYS)
    if tools is not None:
        out["toolCalls"] = tools
    tokens = _first_int(bag, _FINISH_TOKENS_KEYS)
    if tokens is not None:
        out["tokensUsed"] = tokens
    return out


def _run_key(child_id: str, sub_id: str) -> str:
    return child_id or sub_id


def _empty_run(*, child_id: str, sub_id: str) -> SubagentRun:
    return SubagentRun(
        subagent_id=sub_id,
        child_session_id=child_id,
        child_path=None,
        subagent_type="",
        description="",
        status="running",
        parent_turn_index=None,
        parent_prompt_id="",
        spawn_event_index=None,
        finish_event_index=None,
        duration_ms=None,
        tool_calls=None,
        turns=None,
        tokens_used=None,
        output_preview="",
    )


def _bag(event: TraceEvent) -> dict[str, JsonValue]:
    raw = event.raw_input.raw() if isinstance(event.raw_input, ToolInputBag) else {}
    return raw if isinstance(raw, dict) else {}


def _apply_spawn(
    by_key: dict[str, SubagentRun],
    ev: TraceEvent,
    parent_dir: Path,
    search_roots: list[Path] | None,
) -> None:
    bag = _bag(ev)
    child = _first_str(bag, _SPAWN_CHILD_KEYS)
    sub_id = _first_str(bag, _SPAWN_ID_KEYS)
    key = _run_key(child, sub_id)
    if not key:
        return
    run = by_key.get(key) or _empty_run(child_id=child, sub_id=sub_id)
    run.child_session_id = child or run.child_session_id
    run.subagent_id = sub_id or run.subagent_id
    run.subagent_type = _first_str(bag, _SPAWN_TYPE_KEYS) or run.subagent_type
    run.description = _first_str(bag, _SPAWN_DESC_KEYS) or run.description
    run.parent_prompt_id = _first_str(bag, _SPAWN_PROMPT_KEYS) or run.parent_prompt_id
    run.spawn_event_index = ev.index
    if run.child_path is None and run.child_session_id:
        run.child_path = resolve_child_session_path(
            parent_dir, run.child_session_id, search_roots=search_roots
        )
    by_key[key] = run


def _apply_finish(
    by_key: dict[str, SubagentRun],
    ev: TraceEvent,
    parent_dir: Path,
    search_roots: list[Path] | None,
) -> None:
    bag = _bag(ev)
    child = _first_str(bag, _SPAWN_CHILD_KEYS)
    sub_id = _first_str(bag, _SPAWN_ID_KEYS)
    key = _run_key(child, sub_id)
    if not key:
        return
    run = by_key.get(key) or _empty_run(child_id=child, sub_id=sub_id)
    run.child_session_id = child or run.child_session_id
    run.subagent_id = sub_id or run.subagent_id
    raw_st = _first_str(bag, _FINISH_STATUS_KEYS)
    run.status = normalize_run_status(raw_st, finished=True)
    run.duration_ms = _first_int(bag, _FINISH_DUR_KEYS)
    run.tool_calls = _first_int(bag, _FINISH_TOOLS_KEYS)
    run.turns = _first_int(bag, _FINISH_TURNS_KEYS)
    run.tokens_used = _first_int(bag, _FINISH_TOKENS_KEYS)
    out = _first_str(bag, _FINISH_OUTPUT_KEYS)
    if out:
        run.output_preview = out[:240]
    run.finish_event_index = ev.index
    if run.child_path is None and run.child_session_id:
        run.child_path = resolve_child_session_path(
            parent_dir, run.child_session_id, search_roots=search_roots
        )
    by_key[key] = run


def _merge_meta_dirs(
    by_key: dict[str, SubagentRun],
    parent_dir: Path,
    search_roots: list[Path] | None,
) -> None:
    sub_root = parent_dir / "subagents"
    if not sub_root.is_dir():
        return
    try:
        names = [ent.name for ent in os.scandir(sub_root) if ent.is_dir(follow_symlinks=False)]
    except OSError:
        return
    for name in names:
        meta = _read_meta(sub_root / name / "meta.json")
        child = _first_str(meta, _SPAWN_CHILD_KEYS) or name
        sub_id = _first_str(meta, _SPAWN_ID_KEYS) or name
        key = _run_key(child, sub_id)
        run = by_key.get(key) or _empty_run(child_id=child, sub_id=sub_id)
        run.child_session_id = child or run.child_session_id
        run.subagent_id = sub_id or run.subagent_id
        run.subagent_type = _first_str(meta, _SPAWN_TYPE_KEYS) or run.subagent_type
        run.description = _first_str(meta, _SPAWN_DESC_KEYS) or run.description
        if run.finish_event_index is None:
            raw_st = _first_str(meta, _FINISH_STATUS_KEYS)
            if raw_st:
                run.status = normalize_run_status(raw_st, finished=raw_st.lower() != "running")
        if run.child_path is None:
            run.child_path = resolve_child_session_path(
                parent_dir, run.child_session_id, search_roots=search_roots
            )
        by_key[key] = run


def _read_meta(path: Path) -> dict[str, JsonValue]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _link_turn(
    run: SubagentRun,
    segments: list[TurnSegment],
    turn_by_index: Mapping[int, int],
) -> None:
    needle = (run.parent_prompt_id or "").strip()
    if needle:
        for seg in segments:
            if seg.prompt_index is not None and str(seg.prompt_index) == needle:
                run.parent_turn_index = seg.turn_index
                return
    if run.spawn_event_index is not None:
        run.parent_turn_index = turn_by_index.get(run.spawn_event_index)


def _first_str(data: Mapping[str, JsonValue], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = data.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _first_int(data: Mapping[str, JsonValue], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        val = data.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return val
        if isinstance(val, float) and val.is_integer():
            return int(val)
        if isinstance(val, str) and val.strip().isdigit():
            return int(val.strip())
    return None


def as_run_list(value: JsonValue) -> list[JsonObject]:
    """Coerce a wire ``subagentRuns`` value to mappings."""
    if not isinstance(value, list):
        return []
    out: list[JsonObject] = []
    for item in value:
        if isinstance(item, dict):
            out.append(as_json_object(item))
    return out
