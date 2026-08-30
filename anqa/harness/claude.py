"""Claude Code disk adapter (``~/.claude/projects/**/<uuid>.jsonl``).

One file is one parent session. Children live under
``<uuid>/subagents/*.jsonl`` and stay off the catalog list.
"""

from __future__ import annotations

import json
import tarfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .. import event_types as et
from ..models import JsonObject, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from .ref import SessionRef

CLAUDE_HARNESS_ID = "claude"
_CHROME_TYPES = frozenset(
    {
        "queue-operation",
        "last-prompt",
        "atis-latch",
        "ai-title",
        "attachment",
        "file-history-snapshot",
        "progress",
        "system",
        "mode",
    }
)
_AGENT_TOOLS = frozenset({"Agent", "Task"})


def default_projects_root() -> Path:
    """Host Claude projects tree (resolved at call time)."""
    return Path.home() / ".claude" / "projects"


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
        if text.endswith("Z") or "+" in text[10:] or text.endswith("z"):
            return text.replace("z", "Z")
        sec = _epoch(text)
        if sec is None:
            return text
        return datetime.fromtimestamp(sec, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sec = _epoch(raw)
    if sec is None:
        return ""
    return datetime.fromtimestamp(sec, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                bits.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("thinking") or item.get("content")
                if isinstance(text, str) and text:
                    bits.append(text)
                elif isinstance(text, list):
                    nested = _text_of(text)
                    if nested:
                        bits.append(nested)
        return "\n".join(bits)
    return ""


def _iter_rows(path: Path) -> Iterator[JsonObject]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            yield as_json_object(val)


def _is_child_file(path: Path) -> bool:
    return path.parent.name == "subagents"


def _session_id_from_name(path: Path) -> str:
    stem = path.stem
    if stem.startswith("agent-"):
        return stem.removeprefix("agent-")
    return stem


def _row_session_id(row: JsonObject, path: Path) -> str:
    sid = str(row.get("sessionId") or "").strip()
    agent = str(row.get("agentId") or "").strip()
    if _is_child_file(path) and agent:
        return agent
    return sid or _session_id_from_name(path)


def _first_row(path: Path) -> JsonObject | None:
    for row in _iter_rows(path):
        return row
    return None


def _looks_like_claude_file(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".jsonl":
        return False
    row = _first_row(path)
    if row is None:
        return False
    if not str(row.get("sessionId") or "").strip():
        return False
    typ = str(row.get("type") or "")
    if typ == "session":
        return False
    return bool(typ) or isinstance(row.get("message"), dict)


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
        row = _first_row(path)
        sid = _row_session_id(row or {}, path)
        return path, sid
    return root, path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    if not root.is_dir() or not session_id:
        return None
    needle = f"{session_id}.jsonl"
    child_name = f"agent-{session_id}.jsonl"
    try:
        for path in root.rglob("*.jsonl"):
            if path.name == needle or path.name == child_name:
                return path
            if _session_id_from_name(path) == session_id:
                return path
            row = _first_row(path)
            if row is None:
                continue
            if str(row.get("agentId") or "") == session_id:
                return path
            if not _is_child_file(path) and str(row.get("sessionId") or "") == session_id:
                return path
    except OSError:
        return None
    return None


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        path = Path(raw).expanduser()
        files: list[Path] = []
        if path.is_file() and path.suffix == ".jsonl":
            files = [path]
        elif path.is_dir():
            try:
                files = list(path.rglob("*.jsonl"))
            except OSError:
                files = []
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
    if not _looks_like_claude_file(path):
        return None
    row = _first_row(path) or {}
    cwd = ""
    sid = _row_session_id(row, path)
    for item in _iter_rows(path):
        if not cwd:
            cwd = str(item.get("cwd") or "").strip()
        if not sid:
            sid = _row_session_id(item, path)
        if cwd and sid:
            break
    if not sid:
        return None
    loc = path
    try:
        loc = path.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=CLAUDE_HARNESS_ID,
        session_id=sid,
        locator=loc,
        cwd=cwd,
    )


def _content_blocks(msg: JsonObject) -> list[JsonObject]:
    content = msg.get("content")
    if isinstance(content, list):
        return [as_json_object(b) for b in content if isinstance(b, dict)]
    return []


def _is_tool_result_user(msg: JsonObject) -> bool:
    blocks = _content_blocks(msg)
    if not blocks:
        return False
    return all(str(b.get("type") or "") == "tool_result" for b in blocks)


def _title_from_rows(rows: Sequence[JsonObject]) -> str:
    for row in rows:
        if str(row.get("type") or "") == "ai-title":
            title = str(row.get("aiTitle") or "").strip()
            if title:
                return title[:80]
    for row in rows:
        if str(row.get("type") or "") != "user":
            continue
        msg = _as_object(row.get("message"))
        if _is_tool_result_user(msg):
            continue
        text = _text_of(msg.get("content")).strip()
        if text:
            return text.splitlines()[0][:80]
    return ""


def _turn_outcome(rows: Sequence[JsonObject]) -> str:
    """List status from the last user/assistant row. Chrome rows are ignored."""
    last: JsonObject | None = None
    for row in rows:
        typ = str(row.get("type") or "")
        if typ in _CHROME_TYPES:
            continue
        if typ in {"user", "assistant"}:
            last = row
    if last is None:
        return ""
    typ = str(last.get("type") or "")
    msg = _as_object(last.get("message"))
    if typ == "user":
        return "running"
    stop = str(msg.get("stop_reason") or "").strip()
    if stop in {"end_turn", "stop_sequence"}:
        return "complete"
    if stop in {"max_tokens", "refusal", "error"}:
        return "cancelled"
    return "running"


def _file_stamp(path: Path) -> tuple[float, int, int, int]:
    try:
        st = Path(path).expanduser().stat()
    except OSError:
        return (0.0, 0, 0, 0)
    return (float(st.st_mtime), int(st.st_size), 0, 0)


def _child_dir(path: Path, session_id: str) -> Path:
    return path.parent / session_id / "subagents"


def _count_children(path: Path, session_id: str) -> int:
    folder = _child_dir(path, session_id)
    if not folder.is_dir():
        return 0
    try:
        return sum(1 for item in folder.iterdir() if item.is_file() and item.suffix == ".jsonl")
    except OSError:
        return 0


def _meta_from_rows(rows: Sequence[JsonObject], path: Path, session_id: str) -> SessionMeta:
    sid = session_id or _session_id_from_name(path)
    created = ""
    last_ts = ""
    version = ""
    model = ""
    cwd = ""
    branch = ""
    tools = 0
    for row in rows:
        ts = _iso(row.get("timestamp"))
        if ts:
            last_ts = ts
            if not created:
                created = ts
        if not sid:
            sid = str(row.get("sessionId") or sid).strip()
        if not version:
            version = str(row.get("version") or "").strip()
        if not cwd:
            cwd = str(row.get("cwd") or "").strip()
        if not branch:
            branch = str(row.get("gitBranch") or "").strip()
        msg = _as_object(row.get("message"))
        if not model:
            model = str(msg.get("model") or "").strip()
        for block in _content_blocks(msg):
            if str(block.get("type") or "") == "tool_use":
                tools += 1
    start = _epoch(created)
    end = _epoch(last_ts)
    duration = float(max(0, (end or 0) - (start or 0))) if start and end else 0.0
    children = _count_children(path, sid)
    return SessionMeta(
        session_id=sid,
        session_dir=path,
        model_id=model or "unknown",
        title=_title_from_rows(rows),
        created_at=created,
        updated_at=last_ts,
        duration_seconds=duration,
        tool_call_count=tools,
        run_dir=cwd,
        git_branch=branch,
        turn_outcome=_turn_outcome(rows),
        harness=CLAUDE_HARNESS_ID,
        harness_version=version,
        has_subagents=children > 0,
        subagent_count=children,
    )


def _agent_children(rows: Sequence[JsonObject]) -> dict[str, tuple[str, str]]:
    """Map Agent/Task tool_use id → (agentId, agentType)."""
    found: dict[str, tuple[str, str]] = {}
    for row in rows:
        if str(row.get("type") or "") != "user":
            continue
        tur = _as_object(row.get("toolUseResult"))
        agent = str(tur.get("agentId") or "").strip()
        if not agent:
            continue
        typ = str(tur.get("agentType") or "").strip()
        call_id = ""
        for block in _content_blocks(_as_object(row.get("message"))):
            if str(block.get("type") or "") == "tool_result":
                call_id = str(block.get("tool_use_id") or "").strip()
                break
        if call_id:
            found[call_id] = (agent, typ)
    return found


def _tool_names(rows: Sequence[JsonObject]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in rows:
        if str(row.get("type") or "") != "assistant":
            continue
        for block in _content_blocks(_as_object(row.get("message"))):
            if str(block.get("type") or "") != "tool_use":
                continue
            call_id = str(block.get("id") or "").strip()
            name = str(block.get("name") or "").strip()
            if call_id and name:
                names[call_id] = name
    return names


def _timeline_for(rows: Sequence[JsonObject]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    children = _agent_children(rows)
    names = _tool_names(rows)
    turn = 0
    for row in rows:
        typ = str(row.get("type") or "")
        ts = _epoch(row.get("timestamp"))
        if typ == "user":
            events.extend(_user_events(row, ts, turn, names))
            msg = _as_object(row.get("message"))
            if not _is_tool_result_user(msg) and _text_of(msg.get("content")).strip():
                turn += 1
            continue
        if typ == "assistant":
            events.extend(_assistant_events(row, ts, children))
    for i, ev in enumerate(events):
        ev.index = i
    return events


def _user_events(
    row: JsonObject,
    ts: int | None,
    turn: int,
    names: dict[str, str],
) -> list[TraceEvent]:
    msg = _as_object(row.get("message"))
    if _is_tool_result_user(msg):
        return [_tool_result_event(row, ts, names)]
    text = _text_of(msg.get("content"))
    if not text.strip():
        return []
    return [
        TraceEvent(
            index=0,
            event_type=et.TURN_STARTED,
            timestamp=ts,
            content=f"turn_number={turn}",
        ),
        TraceEvent(
            index=0,
            event_type=et.USER_MESSAGE_CHUNK,
            timestamp=ts,
            content=text,
        ),
    ]


def _assistant_events(
    row: JsonObject,
    ts: int | None,
    children: dict[str, tuple[str, str]],
) -> list[TraceEvent]:
    out: list[TraceEvent] = []
    for block in _content_blocks(_as_object(row.get("message"))):
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
        elif kind == "tool_use":
            out.extend(_tool_use_events(block, ts, children))
    return out


def _tool_use_events(
    block: JsonObject,
    ts: int | None,
    children: dict[str, tuple[str, str]],
) -> list[TraceEvent]:
    raw = block.get("input")
    bag = ToolInputBag(raw if isinstance(raw, dict) else {})
    name = str(block.get("name") or "tool").strip() or "tool"
    call_id = str(block.get("id") or "")
    events = [
        TraceEvent(
            index=0,
            event_type=et.TOOL_CALL,
            timestamp=ts,
            content=name,
            tool_name=name,
            tool_call_id=call_id,
            raw_input=bag,
        )
    ]
    if name in _AGENT_TOOLS:
        child, typ = children.get(call_id, ("", str(bag.as_str("subagent_type") or "")))
        if not child:
            child = str(bag.as_str("agentId") or "").strip()
        if child:
            desc = str(bag.as_str("description") or "").strip()
            events.append(
                TraceEvent(
                    index=0,
                    event_type=et.SUBAGENT_SPAWNED,
                    timestamp=ts,
                    content=f"spawned {typ}: {desc}".strip(),
                    raw_input=ToolInputBag(
                        {
                            "child_session_id": child,
                            "subagent_type": typ,
                            "description": desc,
                        }
                    ),
                )
            )
    return events


def _tool_result_event(
    row: JsonObject,
    ts: int | None,
    names: dict[str, str],
) -> TraceEvent:
    msg = _as_object(row.get("message"))
    call_id = ""
    text = ""
    for block in _content_blocks(msg):
        if str(block.get("type") or "") != "tool_result":
            continue
        call_id = str(block.get("tool_use_id") or call_id)
        text = _text_of(block.get("content"))
    tur = _as_object(row.get("toolUseResult"))
    if not text:
        text = _text_of(tur.get("content") or tur.get("stdout") or tur)
    child = str(tur.get("agentId") or "").strip()
    typ = str(tur.get("agentType") or "").strip()
    name = names.get(call_id, "tool")
    if child:
        return TraceEvent(
            index=0,
            event_type=et.SUBAGENT_FINISHED,
            timestamp=ts,
            content=text[:400],
            tool_name=name,
            tool_call_id=call_id,
            raw_input=ToolInputBag(
                {
                    "child_session_id": child,
                    "subagent_type": typ,
                    "status": str(tur.get("status") or "completed"),
                    "output": text[:2000],
                }
            ),
        )
    return TraceEvent(
        index=0,
        event_type=et.TOOL_CALL_UPDATE,
        timestamp=ts,
        content=text,
        tool_name=name,
        tool_call_id=call_id,
    )


class ClaudeAdapter:
    """Read-only Claude Code jsonl adapter."""

    id: str = CLAUDE_HARNESS_ID
    product: str = "Claude Code"
    supported_version: str = "2.1.251"

    def root(self) -> Path:
        """Host projects tree."""
        return default_projects_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for file in _collect_jsonl(scan):
            if _is_child_file(file):
                continue
            ref = _ref_for_file(file)
            if ref is None or ref.session_id in seen:
                continue
            seen.add(ref.session_id)
            found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == CLAUDE_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == CLAUDE_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return _looks_like_claude_file(path)

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not path.is_file() or path.suffix != ".jsonl":
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"claude session not found: {sid}")
        rows = list(_iter_rows(path))
        if not rows:
            raise FileNotFoundError(f"claude session not found: {sid}")
        meta = _meta_from_rows(rows, path, sid)
        meta.num_events = len(rows)
        return meta

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
            raise FileNotFoundError(f"claude session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        members: list[str] = []
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                name = f"{sid}/{path.name}"
                tf.add(path, arcname=name)
                members.append(name)
                folder = _child_dir(path, sid)
                if folder.is_dir():
                    for child in sorted(folder.glob("*.jsonl")):
                        child_name = f"{sid}/subagents/{child.name}"
                        tf.add(child, arcname=child_name)
                        members.append(child_name)
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return members

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        from .grok import open_bound_archive

        return open_bound_archive(src, dest_root, self.bind_locator, harness=self.id)

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
    "CLAUDE_HARNESS_ID",
    "ClaudeAdapter",
    "default_projects_root",
]
