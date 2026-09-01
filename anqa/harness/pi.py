"""Pi disk adapter (``~/.pi/agent/sessions/**/*.jsonl``).

One file is one session. Header row ``type=session`` holds the id.
"""

from __future__ import annotations

import tarfile
from collections.abc import Sequence
from pathlib import Path

from .. import event_types as et
from ..json_lines import json_lines
from ..models import JsonObject, SessionMeta, ToolInputBag, TraceEvent, json_mapping
from ..stamp import Stamp
from .ref import SessionRef
from .status import from_last

PI_HARNESS_ID = "pi"


def default_sessions_root() -> Path:
    """Host Pi sessions tree (resolved at call time)."""
    return Path.home() / ".pi" / "agent" / "sessions"


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                bits.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    bits.append(str(text))
        return "\n".join(bits)
    return ""


def _header(path: Path) -> JsonObject | None:
    from .jsonl_list import first_json_object

    row = first_json_object(path)
    if row is None:
        return None
    if str(row.get("type") or "") == "session":
        return row
    return None


def _session_id_from_name(path: Path) -> str:
    stem = path.stem
    if "_" in stem:
        return stem.rsplit("_", 1)[-1]
    return stem


def _jsonl_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        found = _find_file(root, parsed[1])
        if found is None:
            return root, parsed[1]
        return found, parsed[1]
    path = Path(text).expanduser()
    if path.is_file():
        header = _header(path)
        sid = str((header or {}).get("id") or _session_id_from_name(path))
        return path, sid
    return root, path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    if not root.is_dir() or not session_id:
        return None
    needle = f"_{session_id}.jsonl"
    try:
        from ..scan import find_files

        named = find_files(root, suffix=needle)
        if named:
            return named[0]
        for path in find_files(root, suffix=".jsonl"):
            if path.name.endswith(needle) or _session_id_from_name(path) == session_id:
                return path
            header = _header(path)
            if header is not None and str(header.get("id") or "") == session_id:
                return path
    except OSError:
        return None
    return None


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    from ..scan import find_files

    out: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        path = Path(raw).expanduser()
        files: list[Path] = []
        if path.is_file() and path.suffix == ".jsonl":
            files = [path]
        elif path.is_dir():
            files = find_files(path, suffix=".jsonl")
        for file in files:
            try:
                key = str(file.resolve())
            except OSError:
                key = str(file)
            if key in seen:
                continue
            seen.add(key)
            out.append(file)
    return out


def _ref_for_file(path: Path) -> SessionRef | None:
    header = _header(path)
    if header is None:
        return None
    sid = str(header.get("id") or "").strip() or _session_id_from_name(path)
    cwd = str(header.get("cwd") or "").strip()
    loc = path
    try:
        loc = path.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=PI_HARNESS_ID,
        session_id=sid,
        locator=loc,
        cwd=cwd,
    )


def _model_from_rows(rows: Sequence[JsonObject]) -> str:
    provider = ""
    model = ""
    for row in rows:
        if str(row.get("type") or "") != "model_change":
            continue
        provider = str(row.get("provider") or provider).strip()
        model = str(row.get("modelId") or model).strip()
    if provider and model:
        return f"{provider}/{model}"
    return model or provider or "unknown"


def _first_user_title(rows: Sequence[JsonObject]) -> str:
    for row in rows:
        if str(row.get("type") or "") != "message":
            continue
        msg = json_mapping(row.get("message"))
        if str(msg.get("role") or "") != "user":
            continue
        text = _text_of(msg.get("content")).strip()
        if text:
            return text.splitlines()[0][:80]
    return ""


def _turn_outcome(rows: Sequence[JsonObject]) -> str:
    """List status from the last message. Finished assistants carry stopReason."""
    last: JsonObject | None = None
    for row in rows:
        if str(row.get("type") or "") == "message":
            last = row
    if last is None:
        return ""
    msg = json_mapping(last.get("message"))
    role = str(msg.get("role") or "")
    if role == "toolResult":
        return from_last("running")
    if role == "user":
        return from_last(role)
    if role != "assistant":
        return from_last(role)
    stop = str(msg.get("stopReason") or "").strip()
    if stop.casefold().replace("_", "") == "tooluse":
        return from_last("running")
    return from_last(stop)


def _list_meta(path: Path, session_id: str) -> SessionMeta:
    """Header + tail only. Catalog must not read the full jsonl."""
    from .jsonl_list import file_list_meta, first_json_object, last_json_objects
    from .status import from_last

    header = first_json_object(path) or {}
    sid = str(header.get("id") or session_id or _session_id_from_name(path)).strip()
    created = Stamp.iso(header.get("timestamp"))
    cwd = str(header.get("cwd") or "").strip()
    version = str(header.get("version") or "").strip()
    title = ""
    model = ""
    outcome = ""
    tools = 0
    sub_n = 0
    if str(header.get("type") or "") == "message":
        msg = json_mapping(header.get("message"))
        if str(msg.get("role") or "") == "user":
            title = _text_of(msg.get("content")).splitlines()[0][:80]
    for row in last_json_objects(path):
        if str(row.get("type") or "") == "model_change":
            mid = str(row.get("modelId") or "").strip()
            if mid:
                prov = str(row.get("provider") or "").strip()
                model = f"{prov}/{mid}" if prov else mid
        if str(row.get("type") or "") != "message":
            continue
        msg = json_mapping(row.get("message"))
        role = str(msg.get("role") or "")
        if role == "user":
            if not title:
                title = _text_of(msg.get("content")).splitlines()[0][:80]
            outcome = from_last("user")
        if role == "assistant":
            stop = str(msg.get("stopReason") or "").strip()
            if stop.casefold().replace("_", "") == "tooluse":
                outcome = from_last("running")
            elif stop:
                outcome = from_last(stop)
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and str(block.get("type") or "") == "toolCall":
                        tools += 1
                        if str(block.get("name") or "") == "subagent":
                            tasks = block.get("arguments")
                            bag = json_mapping(tasks) if isinstance(tasks, dict) else {}
                            raw = bag.get("tasks")
                            if isinstance(raw, list):
                                sub_n += len(raw)
        if role == "toolResult":
            outcome = from_last("running")
    return file_list_meta(
        path,
        session_id=sid,
        harness=PI_HARNESS_ID,
        title=title,
        model_id=model,
        created_at=created,
        turn_outcome=outcome,
        tool_call_count=tools,
        harness_version=version,
        run_dir=cwd,
        has_subagents=sub_n > 0,
        subagent_count=sub_n,
    )


def _meta_from_rows(rows: Sequence[JsonObject], path: Path, session_id: str) -> SessionMeta:
    header = next((r for r in rows if str(r.get("type") or "") == "session"), {})
    sid = str(header.get("id") or session_id or _session_id_from_name(path)).strip()
    created = Stamp.iso(header.get("timestamp"))
    last_ts = created
    tools = 0
    for row in rows:
        ts = Stamp.iso(row.get("timestamp"))
        if ts:
            last_ts = ts
        msg = json_mapping(row.get("message")) if str(row.get("type") or "") == "message" else {}
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and str(block.get("type") or "") == "toolCall":
                    tools += 1
    start = Stamp.epoch(header.get("timestamp"))
    end = Stamp.epoch(last_ts)
    duration = float(max(0, (end or 0) - (start or 0))) if start and end else 0.0
    cwd = str(header.get("cwd") or "").strip()
    version = str(header.get("version") or "").strip()
    return SessionMeta(
        session_id=sid,
        session_dir=path,
        model_id=_model_from_rows(rows),
        title=_first_user_title(rows),
        created_at=created,
        updated_at=last_ts,
        duration_seconds=duration,
        tool_call_count=tools,
        run_dir=cwd,
        turn_outcome=_turn_outcome(rows),
        harness=PI_HARNESS_ID,
        harness_version=version,
    )


class PiAdapter:
    """Read-only Pi jsonl adapter."""

    id: str = PI_HARNESS_ID
    product: str = "Pi"
    supported_version: str = "0.84.4"

    def root(self) -> Path:
        """Host sessions tree."""
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
            return ref.harness == PI_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == PI_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return path.is_file() and path.suffix == ".jsonl" and _header(path) is not None

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not path.is_file() or path.suffix != ".jsonl":
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"pi session not found: {sid}")
        return _list_meta(path, sid)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        path, _sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            return []
        return _timeline_for(list(json_lines(path)))

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
            raise FileNotFoundError(f"pi session not found: {sid}")
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

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        from .grok import open_bound_archive

        return open_bound_archive(src, dest_root, self.bind_locator, harness=self.id)

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        path, _sid = _jsonl_from_ref(ref, self.root())
        return Stamp.file(path)

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

    def delete_session(self, ref: SessionRef | Path | str) -> None:
        from ..session.delete import unlink_file

        path, _sid = _jsonl_from_ref(ref, self.root())
        unlink_file(path, stop_at=self.root())


def _timeline_for(rows: Sequence[JsonObject]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    turn = 0
    for row in rows:
        if str(row.get("type") or "") != "message":
            continue
        msg = json_mapping(row.get("message"))
        role = str(msg.get("role") or "")
        ts = Stamp.epoch(row.get("timestamp") or msg.get("timestamp"))
        if role == "user":
            events.append(
                TraceEvent(
                    index=0,
                    event_type=et.TURN_STARTED,
                    timestamp=ts,
                    content=f"turn_number={turn}",
                )
            )
            events.append(
                TraceEvent(
                    index=0,
                    event_type=et.USER_MESSAGE_CHUNK,
                    timestamp=ts,
                    content=_text_of(msg.get("content")),
                )
            )
            turn += 1
            continue
        if role == "toolResult":
            events.extend(_tool_result_events(msg, ts))
            continue
        if role == "assistant":
            events.extend(_assistant_events(msg, ts))
    for i, ev in enumerate(events):
        ev.index = i
    return events


def _assistant_events(msg: JsonObject, ts: int | None) -> list[TraceEvent]:
    out: list[TraceEvent] = []
    content = msg.get("content")
    blocks = content if isinstance(content, list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        if kind == "thinking":
            out.append(
                TraceEvent(
                    index=0,
                    event_type=et.AGENT_THOUGHT_CHUNK,
                    timestamp=ts,
                    content=str(block.get("thinking") or block.get("text") or ""),
                )
            )
        elif kind == "text":
            out.append(
                TraceEvent(
                    index=0,
                    event_type=et.AGENT_MESSAGE_CHUNK,
                    timestamp=ts,
                    content=str(block.get("text") or ""),
                )
            )
        elif kind == "toolCall":
            raw = block.get("arguments")
            bag = ToolInputBag(raw if isinstance(raw, dict) else {})
            name = str(block.get("name") or "tool").strip() or "tool"
            call_id = str(block.get("id") or "")
            out.append(
                TraceEvent(
                    index=0,
                    event_type=et.TOOL_CALL,
                    timestamp=ts,
                    content=name,
                    tool_name=name,
                    tool_call_id=call_id,
                    raw_input=bag,
                )
            )
            if name == "subagent":
                out.extend(_subagent_spawn_events(bag, call_id, ts))
    return out


def _subagent_spawn_events(bag: ToolInputBag, call_id: str, ts: int | None) -> list[TraceEvent]:
    raw = bag.get("tasks")
    tasks = raw if isinstance(raw, list) else []
    out: list[TraceEvent] = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        agent = str(task.get("agent") or "worker").strip() or "worker"
        desc = str(task.get("task") or "").strip()
        sid = f"{call_id}:{i}"
        out.append(
            TraceEvent(
                index=0,
                event_type=et.SUBAGENT_SPAWNED,
                timestamp=ts,
                content=f"spawned {agent}: {desc}".strip(),
                raw_input=ToolInputBag(
                    {
                        "subagent_id": sid,
                        "subagent_type": agent,
                        "description": desc[:320],
                    }
                ),
            )
        )
    return out


def _last_assistant_text(messages: object) -> str:
    if not isinstance(messages, list):
        return ""
    text = ""
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") != "assistant":
            continue
        body = _text_of(item.get("content")).strip()
        if body:
            text = body
    return text


def _subagent_finish_events(msg: JsonObject, ts: int | None) -> list[TraceEvent]:
    call_id = str(msg.get("toolCallId") or "")
    details = json_mapping(msg.get("details"))
    results: list[JsonObject] = []
    raw = details.get("results")
    if isinstance(raw, list):
        results = [json_mapping(item) for item in raw if isinstance(item, dict)]
    if not results:
        return [
            TraceEvent(
                index=0,
                event_type=et.SUBAGENT_FINISHED,
                timestamp=ts,
                content=_text_of(msg.get("content"))[:400],
                tool_name="subagent",
                tool_call_id=call_id,
                is_error=bool(msg.get("isError")),
                raw_input=ToolInputBag(
                    {
                        "subagent_id": f"{call_id}:0",
                        "subagent_type": "worker",
                        "status": "failed" if msg.get("isError") else "completed",
                        "output": _text_of(msg.get("content")),
                    }
                ),
            )
        ]
    out: list[TraceEvent] = []
    for i, item in enumerate(results):
        agent = str(item.get("agent") or "worker").strip() or "worker"
        text = _last_assistant_text(item.get("messages")) or _text_of(item.get("task"))
        code = item.get("exitCode")
        failed = isinstance(code, int) and code != 0
        out.append(
            TraceEvent(
                index=0,
                event_type=et.SUBAGENT_FINISHED,
                timestamp=ts,
                content=text[:400],
                tool_name="subagent",
                tool_call_id=call_id,
                is_error=failed,
                raw_input=ToolInputBag(
                    {
                        "subagent_id": f"{call_id}:{i}",
                        "subagent_type": agent,
                        "status": "failed" if failed else "completed",
                        "output": text,
                    }
                ),
            )
        )
    return out


def _tool_result_events(msg: JsonObject, ts: int | None) -> list[TraceEvent]:
    name = str(msg.get("toolName") or "tool").strip() or "tool"
    if name == "subagent":
        return _subagent_finish_events(msg, ts)
    return [_tool_result_event(msg, ts)]


def _tool_result_event(msg: JsonObject, ts: int | None) -> TraceEvent:
    name = str(msg.get("toolName") or "tool").strip() or "tool"
    return TraceEvent(
        index=0,
        event_type=et.TOOL_CALL_UPDATE,
        timestamp=ts,
        content=_text_of(msg.get("content")),
        tool_name=name,
        tool_call_id=str(msg.get("toolCallId") or ""),
        is_error=bool(msg.get("isError")),
    )


__all__ = [
    "PI_HARNESS_ID",
    "PiAdapter",
    "default_sessions_root",
]
