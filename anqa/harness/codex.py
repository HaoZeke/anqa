"""Codex disk adapter (``~/.codex/sessions/**/rollout-*.jsonl``).

One jsonl file is one conversation. Catalog path is ``codex:<session_id>``.
"""

from __future__ import annotations

import json
import re
import tarfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .. import event_types as et
from ..models import JsonObject, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from .ref import SessionRef

CODEX_HARNESS_ID = "codex"
_ROLL_ID = re.compile(
    r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)
_RUNNING_TAIL = frozenset({"task_started", "user_message"})
_COMPLETE_TAIL = frozenset({"task_complete"})
_CANCELLED_TAIL = frozenset({"turn_aborted"})
_TURN_SIGNALS = _RUNNING_TAIL | _COMPLETE_TAIL | _CANCELLED_TAIL


def default_sessions_root() -> Path:
    """Host Codex sessions tree (resolved at call time)."""
    return Path.home() / ".codex" / "sessions"


def _as_object(raw: object) -> JsonObject:
    if isinstance(raw, dict):
        return as_json_object(raw)
    return {}


def _epoch(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        val = float(raw)
        return int(val / 1000.0) if val > 1e12 else int(val)
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp())
    return None


def _iso(raw: object) -> str:
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.endswith("Z") or "+" in text[10:]:
            return text
        sec = _epoch(text)
        if sec is None:
            return text
        return datetime.fromtimestamp(sec, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sec = _epoch(raw)
    if sec is None:
        return ""
    return datetime.fromtimestamp(sec, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_stamp(path: Path) -> tuple[float, int, int, int]:
    try:
        st = Path(path).expanduser().stat()
    except OSError:
        return (0.0, 0, 0, 0)
    return (float(st.st_mtime), int(st.st_size), 0, 0)


def _iter_rows(path: Path) -> Iterator[JsonObject]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            yield as_json_object(raw)


def _session_id_from_name(path: Path) -> str:
    match = _ROLL_ID.search(path.name)
    return match.group(1) if match else path.stem


def _blocks_text(raw: object, *, kinds: frozenset[str]) -> str:
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, list):
        return ""
    bits: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") not in kinds:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            bits.append(text)
    return "\n".join(bits)


def _args_bag(raw: object) -> JsonObject:
    if isinstance(raw, dict):
        return as_json_object(raw)
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(val, dict):
            return as_json_object(val)
    return {}


def _meta_row(rows: Sequence[JsonObject]) -> JsonObject:
    for row in rows:
        if str(row.get("type") or "") == "session_meta":
            return _as_object(row.get("payload"))
    return {}


def _last_event_msg_type(rows: Sequence[JsonObject]) -> str:
    last = ""
    for row in rows:
        if str(row.get("type") or "") != "event_msg":
            continue
        typ = str(_as_object(row.get("payload")).get("type") or "").strip()
        if typ in _TURN_SIGNALS:
            last = typ
    return last


def _turn_outcome(rows: Sequence[JsonObject]) -> str:
    last = _last_event_msg_type(rows)
    if last in _RUNNING_TAIL:
        return "running"
    if last in _COMPLETE_TAIL:
        return "complete"
    if last in _CANCELLED_TAIL:
        return "cancelled"
    return ""


def _model_from_rows(rows: Sequence[JsonObject]) -> str:
    for row in reversed(rows):
        typ = str(row.get("type") or "")
        pl = _as_object(row.get("payload"))
        if typ == "turn_context":
            mid = str(pl.get("model") or "").strip()
            if mid:
                return mid
        if typ == "event_msg" and str(pl.get("type") or "") == "thread_settings_applied":
            mid = str(_as_object(pl.get("thread_settings")).get("model") or "").strip()
            if mid:
                return mid
    return ""


def _is_environment_context(text: str) -> bool:
    return text.lstrip().startswith("<environment_context>")


def _first_user_title(rows: Sequence[JsonObject]) -> str:
    for row in rows:
        if str(row.get("type") or "") != "response_item":
            continue
        pl = _as_object(row.get("payload"))
        if str(pl.get("type") or "") != "message" or str(pl.get("role") or "") != "user":
            continue
        text = _blocks_text(pl.get("content"), kinds=frozenset({"input_text"}))
        if text and not _is_environment_context(text):
            return text.splitlines()[0][:120]
    return ""


def _count_tools(rows: Sequence[JsonObject]) -> int:
    n = 0
    for row in rows:
        if str(row.get("type") or "") != "response_item":
            continue
        pt = str(_as_object(row.get("payload")).get("type") or "")
        if pt in {"custom_tool_call", "function_call"}:
            n += 1
    return n


def _count_subagents(rows: Sequence[JsonObject]) -> int:
    n = 0
    for row in rows:
        item = _subagent_item(row)
        if item is not None and str(item.get("kind") or "") == "started":
            n += 1
    return n


def _subagent_item(row: JsonObject) -> JsonObject | None:
    if str(row.get("type") or "") != "event_msg":
        return None
    pl = _as_object(row.get("payload"))
    if str(pl.get("type") or "") != "item_completed":
        return None
    item = _as_object(pl.get("item"))
    if str(item.get("type") or "") != "SubAgentActivity":
        return None
    return item


def _timeline_for(rows: Sequence[JsonObject]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for i, row in enumerate(rows):
        evs = _events_from_row(i, row)
        events.extend(evs)
    return events


def _events_from_row(index: int, row: JsonObject) -> list[TraceEvent]:
    typ = str(row.get("type") or "")
    ts = _epoch(row.get("timestamp"))
    pl = _as_object(row.get("payload"))
    if typ == "event_msg":
        return _from_event_msg(index, ts, pl)
    if typ == "response_item":
        return _from_response_item(index, ts, pl)
    return []


def _from_event_msg(index: int, ts: int | None, pl: JsonObject) -> list[TraceEvent]:
    kind = str(pl.get("type") or "")
    if kind == "task_started":
        return [
            TraceEvent(
                index=index,
                event_type=et.TURN_STARTED,
                timestamp=ts,
                content="",
                update_index=index,
            )
        ]
    if kind == "task_complete":
        return [
            TraceEvent(
                index=index,
                event_type=et.TURN_COMPLETED,
                timestamp=ts,
                content="",
                update_index=index,
            )
        ]
    if kind == "turn_aborted":
        return [
            TraceEvent(
                index=index,
                event_type=et.TURN_ENDED,
                timestamp=ts,
                content=str(pl.get("reason") or "").strip(),
                update_index=index,
            )
        ]
    item = _as_object(pl.get("item")) if kind == "item_completed" else {}
    if str(item.get("type") or "") == "SubAgentActivity":
        ev = _from_subagent_item(index, ts, item)
        return [ev] if ev is not None else []
    return []


def _from_subagent_item(index: int, ts: int | None, item: JsonObject) -> TraceEvent | None:
    kind = str(item.get("kind") or "").strip()
    child = str(item.get("agent_thread_id") or "").strip()
    path = str(item.get("agent_path") or "").strip()
    typ = path.rsplit("/", 1)[-1] if path else ""
    if kind == "started":
        return TraceEvent(
            index=index,
            event_type=et.SUBAGENT_SPAWNED,
            timestamp=ts,
            content=path or typ,
            raw_input=ToolInputBag(
                {
                    "child_session_id": child,
                    "subagent_id": child,
                    "subagent_type": typ,
                    "description": path,
                }
            ),
            update_index=index,
        )
    if kind in {"completed", "interrupted"}:
        return TraceEvent(
            index=index,
            event_type=et.SUBAGENT_FINISHED,
            timestamp=ts,
            content=path or typ,
            raw_input=ToolInputBag(
                {
                    "child_session_id": child,
                    "subagent_id": child,
                    "status": "cancelled" if kind == "interrupted" else "completed",
                }
            ),
            update_index=index,
        )
    return None


def _from_response_item(index: int, ts: int | None, pl: JsonObject) -> list[TraceEvent]:
    kind = str(pl.get("type") or "")
    if kind == "message":
        role = str(pl.get("role") or "")
        if role == "user":
            text = _blocks_text(pl.get("content"), kinds=frozenset({"input_text"}))
            if _is_environment_context(text):
                return []
            return [
                TraceEvent(
                    index=index,
                    event_type=et.USER_MESSAGE_CHUNK,
                    timestamp=ts,
                    content=text,
                    update_index=index,
                )
            ]
        if role == "assistant":
            text = _blocks_text(pl.get("content"), kinds=frozenset({"output_text"}))
            return [
                TraceEvent(
                    index=index,
                    event_type=et.AGENT_MESSAGE_CHUNK,
                    timestamp=ts,
                    content=text,
                    update_index=index,
                )
            ]
        return []
    if kind == "custom_tool_call":
        name = str(pl.get("name") or "").strip()
        raw = pl.get("input")
        bag = _args_bag(raw)
        if not bag and isinstance(raw, str) and raw.strip():
            bag = as_json_object({"command": raw})
        return [
            TraceEvent(
                index=index,
                event_type=et.TOOL_CALL,
                timestamp=ts,
                tool_name=name,
                tool_call_id=str(pl.get("call_id") or pl.get("id") or ""),
                raw_input=ToolInputBag(bag),
                update_index=index,
            )
        ]
    if kind == "custom_tool_call_output":
        return [
            TraceEvent(
                index=index,
                event_type=et.TOOL_CALL_UPDATE,
                timestamp=ts,
                tool_call_id=str(pl.get("call_id") or pl.get("id") or ""),
                content=_blocks_text(
                    pl.get("output"), kinds=frozenset({"input_text", "output_text"})
                ),
                update_index=index,
            )
        ]
    if kind == "function_call":
        name = str(pl.get("name") or "").strip()
        return [
            TraceEvent(
                index=index,
                event_type=et.TOOL_CALL,
                timestamp=ts,
                tool_name=name,
                tool_call_id=str(pl.get("call_id") or pl.get("id") or ""),
                raw_input=ToolInputBag(_args_bag(pl.get("arguments"))),
                update_index=index,
            )
        ]
    if kind == "function_call_output":
        return [
            TraceEvent(
                index=index,
                event_type=et.TOOL_CALL_UPDATE,
                timestamp=ts,
                tool_call_id=str(pl.get("call_id") or pl.get("id") or ""),
                content=_blocks_text(
                    pl.get("output"), kinds=frozenset({"input_text", "output_text"})
                ),
                update_index=index,
            )
        ]
    return []


def _meta_from_rows(rows: Sequence[JsonObject], path: Path, sid: str) -> SessionMeta:
    header = _meta_row(rows)
    sid = str(header.get("session_id") or header.get("id") or sid).strip() or sid
    created = _iso(header.get("timestamp") or (rows[0].get("timestamp") if rows else ""))
    last_ts = ""
    for row in rows:
        last_ts = _iso(row.get("timestamp")) or last_ts
    start = _epoch(header.get("timestamp") or (rows[0].get("timestamp") if rows else None))
    end = _epoch(rows[-1].get("timestamp")) if rows else None
    duration = float(max(0, (end or 0) - (start or 0))) if start and end else 0.0
    kids = _count_subagents(rows)
    return SessionMeta(
        session_id=sid,
        session_dir=path,
        model_id=_model_from_rows(rows) or "unknown",
        title=_first_user_title(rows),
        created_at=created,
        updated_at=last_ts or created,
        duration_seconds=duration,
        run_dir=str(header.get("cwd") or "").strip(),
        num_events=len(_timeline_for(rows)),
        tool_call_count=_count_tools(rows),
        turn_outcome=_turn_outcome(rows),
        harness=CODEX_HARNESS_ID,
        harness_version=str(header.get("cli_version") or "").strip(),
        has_subagents=kids > 0,
        subagent_count=kids,
    )


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    out: list[Path] = []
    for raw in roots:
        path = Path(raw).expanduser()
        if path.is_file() and path.name.startswith("rollout-") and path.suffix == ".jsonl":
            out.append(path)
            continue
        if not path.is_dir():
            continue
        out.extend(sorted(path.rglob("rollout-*.jsonl")))
    return out


def _ref_for_file(path: Path) -> SessionRef | None:
    if not path.is_file():
        return None
    rows = list(_iter_rows(path))
    header = _meta_row(rows)
    sid = str(header.get("session_id") or header.get("id") or _session_id_from_name(path)).strip()
    if not sid:
        return None
    return SessionRef(
        harness=CODEX_HARNESS_ID,
        session_id=sid,
        locator=path,
        cwd=str(header.get("cwd") or "").strip(),
    )


def _jsonl_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        found = _find_file(root, parsed[1])
        return (found or Path(), parsed[1])
    path = Path(text).expanduser()
    if path.is_file():
        return path, _session_id_from_name(path)
    return Path(), path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    for path in _collect_jsonl([root]):
        if sid in path.name:
            header = _meta_row(list(_iter_rows(path)))
            hid = str(header.get("session_id") or header.get("id") or _session_id_from_name(path))
            if hid == sid:
                return path
    return None


class CodexAdapter:
    """Read-only Codex rollout jsonl adapter."""

    id = CODEX_HARNESS_ID
    product = "Codex"
    supported_version = "0.151.0"

    def root(self) -> Path:
        return default_sessions_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for file in _collect_jsonl(scan):
            ref = _ref_for_file(file)
            if ref is None or ref.session_id in seen:
                continue
            seen.add(ref.session_id)
            found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == CODEX_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == CODEX_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return path.is_file() and path.name.startswith("rollout-") and path.suffix == ".jsonl"

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not self.looks_like(path):
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"codex session not found: {sid}")
        rows = list(_iter_rows(path))
        if not rows:
            raise FileNotFoundError(f"codex session not found: {sid}")
        return _meta_from_rows(rows, path, sid)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        path, _sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            return []
        return _timeline_for(list(_iter_rows(path)))

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        found = _find_file(self.root(), sid)
        if found is None:
            return None
        return _ref_for_file(found)

    def watch_hints(self) -> tuple[str, ...]:
        return (".jsonl",)

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"codex session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        name = f"{sid}/{path.name}"
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                tf.add(path, arcname=name)
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return [name]

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        path, _sid = _jsonl_from_ref(ref, self.root())
        return _file_stamp(path)

    def trace_mtime(self, ref: SessionRef | Path | str) -> float:
        return self.timeline_stamp(ref)[0]

    def updates_size(self, ref: SessionRef | Path | str) -> int:
        return int(self.timeline_stamp(ref)[1])

    def scheduler_state(self, state: JsonObject) -> JsonObject | None:
        return None

    def reported_completion_ids(self, state: JsonObject) -> set[str]:
        return set()

    def list_turn_outcome(self, ref: SessionRef | Path | str) -> str:
        try:
            return (self.load_meta(ref).turn_outcome or "").strip()
        except FileNotFoundError:
            return ""


__all__ = [
    "CODEX_HARNESS_ID",
    "CodexAdapter",
    "default_sessions_root",
]
