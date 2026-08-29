"""Antigravity disk adapter (``~/.gemini/antigravity-cli``).

One conversation is ``conversations/<uuid>.db``. The readable timeline is
``brain/<uuid>/.system_generated/logs/transcript.jsonl``.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from .. import event_types as et
from ..models import JsonObject, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from .ref import SessionRef

ANTIGRAVITY_HARNESS_ID = "antigravity"
_USER_REQUEST = "USER_REQUEST"
_USER_PLAN = "PLAN"


def default_store_root() -> Path:
    """Host Antigravity tree (resolved at call time)."""
    return Path.home() / ".gemini" / "antigravity-cli"


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
        return raw.strip()
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


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db).expanduser()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _looks_like_conversation_db(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".db":
        return False
    if path.name in {"conversation_summaries.db"}:
        return False
    try:
        with _connect(path) as con:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'trajectory_meta'"
            ).fetchone()
    except sqlite3.Error:
        return False
    return rows is not None


def _tag_body(text: str, tag: str) -> str:
    open_tag = f"<{tag}>"
    close_tag = f"</{tag}>"
    start = text.find(open_tag)
    if start < 0:
        return ""
    start += len(open_tag)
    end = text.find(close_tag, start)
    if end < 0:
        return text[start:].strip()
    return text[start:end].strip()


def _iter_jsonl(path: Path) -> Iterator[JsonObject]:
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


def _transcript_path(root: Path, session_id: str) -> Path:
    base = root / "brain" / session_id / ".system_generated" / "logs"
    primary = base / "transcript.jsonl"
    if primary.is_file():
        return primary
    return base / "transcript_full.jsonl"


def _summaries_db(root: Path) -> Path:
    return root / "conversation_summaries.db"


def _load_summary(root: Path, session_id: str) -> JsonObject:
    db = _summaries_db(root)
    if not db.is_file():
        return {}
    try:
        with _connect(db) as con:
            row = con.execute(
                "SELECT conversation_id, title, preview, step_count, last_modified_time, "
                "workspace_uris, status, source, project_id, agent_name, "
                "parent_conversation_id, nesting_depth, not_fully_idle, killed, "
                "last_user_input_time FROM conversation_summaries WHERE conversation_id = ?",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return {}
    if row is None:
        return {}
    return {str(k): row[k] for k in row.keys()}


def _child_ids(root: Path, session_id: str) -> list[str]:
    db = _summaries_db(root)
    if not db.is_file():
        return []
    try:
        with _connect(db) as con:
            rows = con.execute(
                "SELECT conversation_id FROM conversation_summaries WHERE parent_conversation_id = ?",
                (session_id,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows if r[0]]


def _parent_id(root: Path, session_id: str) -> str:
    return str(_load_summary(root, session_id).get("parent_conversation_id") or "").strip()


def _cwd_from_uris(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        val = json.loads(text)
    except json.JSONDecodeError:
        val = [text]
    if not isinstance(val, list):
        return ""
    for item in val:
        uri = str(item or "").strip()
        if uri.startswith("file://"):
            return urlparse(uri).path or ""
        if uri.startswith("/"):
            return uri
    return ""


def _cwd_from_last_conversations(root: Path, session_id: str) -> str:
    path = root / "cache" / "last_conversations.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key, val in data.items():
        if str(val) == session_id:
            return str(key)
    return ""


def _conversation_db(root: Path, session_id: str) -> Path:
    return root / "conversations" / f"{session_id}.db"


def _paths_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        return _conversation_db(root, parsed[1]), parsed[1]
    path = Path(text).expanduser()
    if path.is_file() and path.suffix == ".db":
        return path, path.stem
    return _conversation_db(root, path.name), path.name


def _first_user_title(rows: Sequence[JsonObject], summary: JsonObject) -> str:
    title = str(summary.get("title") or "").strip()
    if title:
        return title[:80]
    for row in rows:
        if str(row.get("type") or "") != "USER_INPUT":
            continue
        text = _tag_body(str(row.get("content") or ""), _USER_REQUEST)
        if text:
            return text.splitlines()[0][:80]
    return ""


def _turn_outcome(rows: Sequence[JsonObject], summary: JsonObject) -> str:
    if summary.get("killed"):
        return "cancelled"
    if summary.get("not_fully_idle"):
        return "running"
    last: JsonObject | None = None
    for row in rows:
        typ = str(row.get("type") or "")
        if typ in {"USER_INPUT", "PLANNER_RESPONSE", "GENERIC", "SYSTEM_MESSAGE"}:
            last = row
    if last is None:
        return ""
    status = str(last.get("status") or "").strip().upper()
    if status in {"PENDING", "RUNNING", "IN_PROGRESS", "ACTIVE"}:
        return "running"
    if str(last.get("type") or "") == "USER_INPUT":
        return "running"
    if status in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
        return "cancelled"
    return "complete"


def _count_tools(rows: Sequence[JsonObject]) -> int:
    n = 0
    for row in rows:
        calls = row.get("tool_calls")
        if isinstance(calls, list):
            n += len(calls)
    return n


def _meta_for(root: Path, db: Path, session_id: str) -> SessionMeta:
    rows = list(_iter_jsonl(_transcript_path(root, session_id)))
    summary = _load_summary(root, session_id)
    created = ""
    updated = _iso(summary.get("last_modified_time") or summary.get("last_user_input_time"))
    if rows:
        created = _iso(rows[0].get("created_at"))
        last = _iso(rows[-1].get("created_at"))
        if last:
            updated = last
        if not created:
            created = updated
    start = _epoch(created)
    end = _epoch(updated)
    duration = float(max(0, (end or 0) - (start or 0))) if start and end else 0.0
    cwd = _cwd_from_uris(summary.get("workspace_uris")) or _cwd_from_last_conversations(
        root, session_id
    )
    kids = _child_ids(root, session_id)
    return SessionMeta(
        session_id=session_id,
        session_dir=db,
        model_id=str(summary.get("agent_name") or "").strip() or "unknown",
        title=_first_user_title(rows, summary),
        created_at=created,
        updated_at=updated,
        duration_seconds=duration,
        tool_call_count=_count_tools(rows),
        run_dir=cwd,
        turn_outcome=_turn_outcome(rows, summary),
        harness=ANTIGRAVITY_HARNESS_ID,
        has_subagents=bool(kids),
        subagent_count=len(kids),
    )


def _timeline_for(rows: Sequence[JsonObject]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    turn = 0
    last_tool = ""
    for row in rows:
        typ = str(row.get("type") or "")
        ts = _epoch(row.get("created_at"))
        if typ == "USER_INPUT":
            events.extend(_user_events(row, ts, turn))
            if _tag_body(str(row.get("content") or ""), _USER_REQUEST):
                turn += 1
            continue
        if typ == "PLANNER_RESPONSE":
            more, last_tool = _planner_events(row, ts, last_tool)
            events.extend(more)
            continue
        if typ == "GENERIC":
            events.append(_generic_event(row, ts, last_tool))
            continue
        if typ == "SYSTEM_MESSAGE":
            text = str(row.get("content") or "")
            if text.strip():
                events.append(
                    TraceEvent(
                        index=0,
                        event_type=et.SYSTEM,
                        timestamp=ts,
                        content=text,
                    )
                )
    for i, ev in enumerate(events):
        ev.index = i
    return events


def _user_events(row: JsonObject, ts: int | None, turn: int) -> list[TraceEvent]:
    raw = str(row.get("content") or "")
    request = _tag_body(raw, _USER_REQUEST)
    out: list[TraceEvent] = []
    if request:
        out.append(
            TraceEvent(
                index=0,
                event_type=et.TURN_STARTED,
                timestamp=ts,
                content=f"turn_number={turn}",
            )
        )
        out.append(
            TraceEvent(
                index=0,
                event_type=et.USER_MESSAGE_CHUNK,
                timestamp=ts,
                content=request,
            )
        )
    plan = _tag_body(raw, _USER_PLAN)
    if plan:
        out.append(
            TraceEvent(
                index=0,
                event_type=et.PLAN,
                timestamp=ts,
                content=plan,
            )
        )
    return out


def _planner_events(
    row: JsonObject, ts: int | None, last_tool: str
) -> tuple[list[TraceEvent], str]:
    out: list[TraceEvent] = []
    thinking = row.get("thinking")
    if isinstance(thinking, str) and thinking.strip():
        out.append(
            TraceEvent(
                index=0,
                event_type=et.AGENT_THOUGHT_CHUNK,
                timestamp=ts,
                content=thinking,
            )
        )
    text = row.get("content")
    if isinstance(text, str) and text.strip():
        out.append(
            TraceEvent(
                index=0,
                event_type=et.AGENT_MESSAGE_CHUNK,
                timestamp=ts,
                content=text,
            )
        )
    calls = row.get("tool_calls")
    if isinstance(calls, list):
        for item in calls:
            if not isinstance(item, dict):
                continue
            ev = _tool_call_event(as_json_object(item), ts)
            out.append(ev)
            last_tool = ev.tool_name or last_tool
    return out, last_tool


def _tool_call_event(call: JsonObject, ts: int | None) -> TraceEvent:
    name = str(call.get("name") or "tool").strip() or "tool"
    raw = call.get("args")
    bag = ToolInputBag(raw if isinstance(raw, dict) else {})
    return TraceEvent(
        index=0,
        event_type=et.TOOL_CALL,
        timestamp=ts,
        content=name,
        tool_name=name,
        raw_input=bag,
    )


def _generic_event(row: JsonObject, ts: int | None, last_tool: str) -> TraceEvent:
    return TraceEvent(
        index=0,
        event_type=et.TOOL_CALL_UPDATE,
        timestamp=ts,
        content=str(row.get("content") or ""),
        tool_name=last_tool or "tool",
    )


class AntigravityAdapter:
    """Read-only Antigravity conversation adapter."""

    id: str = ANTIGRAVITY_HARNESS_ID
    product: str = "Antigravity"
    supported_version: str = "1.1.22"

    def root(self) -> Path:
        """Host store tree."""
        return default_store_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for raw in scan:
            root = Path(raw).expanduser()
            conv = root / "conversations" if (root / "conversations").is_dir() else root
            try:
                files = list(conv.glob("*.db"))
            except OSError:
                files = []
            for db in files:
                ref = self.bind_locator(db)
                if ref is None or ref.session_id in seen:
                    continue
                if _parent_id(self._store_root_for(db), ref.session_id):
                    continue
                seen.add(ref.session_id)
                found.append(ref)
        return found

    def _store_root_for(self, locator: Path) -> Path:
        path = Path(locator).expanduser()
        if path.parent.name == "conversations":
            return path.parent.parent
        return self.root()

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == ANTIGRAVITY_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == ANTIGRAVITY_HARNESS_ID
        return _looks_like_conversation_db(Path(str(ref)).expanduser())

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not _looks_like_conversation_db(path):
            return None
        sid = path.stem
        root = self._store_root_for(path)
        loc = path
        try:
            loc = path.resolve()
        except OSError:
            pass
        cwd = _cwd_from_uris(_load_summary(root, sid).get("workspace_uris"))
        if not cwd:
            cwd = _cwd_from_last_conversations(root, sid)
        return SessionRef(
            harness=ANTIGRAVITY_HARNESS_ID,
            session_id=sid,
            locator=loc,
            cwd=cwd,
        )

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        db, sid = _paths_from_ref(ref, self.root())
        if not db.is_file():
            raise FileNotFoundError(f"antigravity session not found: {sid}")
        root = self._store_root_for(db)
        meta = _meta_for(root, db, sid)
        meta.num_events = len(list(_iter_jsonl(_transcript_path(root, sid))))
        return meta

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        db, sid = _paths_from_ref(ref, self.root())
        if not sid:
            return []
        root = self._store_root_for(db) if db.is_file() else self.root()
        return _timeline_for(list(_iter_jsonl(_transcript_path(root, sid))))

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        db = _conversation_db(self.root(), sid)
        if not db.is_file():
            return None
        return self.bind_locator(db)

    def watch_hints(self) -> tuple[str, ...]:
        return (".db", ".db-wal")

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        db, sid = _paths_from_ref(ref, self.root())
        if not db.is_file():
            raise FileNotFoundError(f"antigravity session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        members: list[str] = []
        root = self._store_root_for(db)
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                name = f"{sid}/{db.name}"
                tf.add(db, arcname=name)
                members.append(name)
                transcript = _transcript_path(root, sid)
                if transcript.is_file():
                    tname = f"{sid}/transcript.jsonl"
                    tf.add(transcript, arcname=tname)
                    members.append(tname)
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return members

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        db, sid = _paths_from_ref(ref, self.root())
        stamp = _file_stamp(db)
        root = self._store_root_for(db) if db.is_file() else self.root()
        tstamp = _file_stamp(_transcript_path(root, sid))
        if tstamp[0] > stamp[0]:
            return tstamp
        return stamp

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
    "ANTIGRAVITY_HARNESS_ID",
    "AntigravityAdapter",
    "default_store_root",
]
