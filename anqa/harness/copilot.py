"""GitHub Copilot CLI disk adapter (``~/.copilot/session-store.db``).

Sessions are sqlite rows. Timeline is ``session-state/<id>/events.jsonl``.
"""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
from collections.abc import Sequence
from pathlib import Path

from .. import event_types as et
from ..json_lines import json_lines
from ..models import JsonObject, SessionMeta, ToolInputBag, TraceEvent, json_mapping
from ..stamp import Stamp
from .ref import SessionRef
from .status import from_last

COPILOT_HARNESS_ID = "copilot"
_TURN_SIGNALS = frozenset(
    {
        "assistant.turn_start",
        "tool.execution_start",
        "subagent.started",
        "session.shutdown",
        "assistant.turn_end",
    }
)


def default_store_root() -> Path:
    """Host Copilot config tree (resolved at call time)."""
    return Path.home() / ".copilot"


def default_db_path() -> Path:
    """Host Copilot catalog database."""
    return default_store_root() / "session-store.db"


def _connect(db: Path) -> sqlite3.Connection:
    path = Path(db).expanduser()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _assert_readable(db: Path) -> Path:
    path = Path(db).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"copilot database not found: {path}")
    return path


def _db_from_ref(ref: SessionRef | Path | str, fallback: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    text = str(ref)
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(text)
    if parsed is not None:
        return fallback, parsed[1]
    path = Path(text).expanduser()
    if path.is_file():
        return path, ""
    return fallback, path.name


def _state_dir(db: Path, session_id: str) -> Path:
    return Path(db).expanduser().resolve().parent / "session-state" / session_id


def _events_path(db: Path, session_id: str) -> Path:
    return _state_dir(db, session_id) / "events.jsonl"


def _session_row(con: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT id, cwd, repository, host_type, branch, summary, created_at, updated_at "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()


def _list_session_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        con.execute(
            "SELECT id, cwd, repository, host_type, branch, summary, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC"
        )
    )


def _last_turn_type(events: list[JsonObject]) -> str:
    last = ""
    for ev in events:
        typ = str(ev.get("type") or "").strip()
        if typ in _TURN_SIGNALS:
            last = typ
    return last


def _turn_outcome(events: list[JsonObject]) -> str:
    return from_last(_last_turn_type(events))


def _text_of(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("content") or raw.get("text") or "").strip()
    return ""


def _timeline_for(events: list[JsonObject]) -> list[TraceEvent]:
    out: list[TraceEvent] = []
    for i, row in enumerate(events):
        ev = _event_from_row(i, row)
        if ev is not None:
            out.append(ev)
    return out


def _event_from_row(index: int, row: JsonObject) -> TraceEvent | None:
    typ = str(row.get("type") or "").strip()
    data = json_mapping(row.get("data"))
    ts = Stamp.epoch(row.get("timestamp"))
    eid = str(row.get("id") or "")
    if typ in {"session.start", "assistant.turn_start"}:
        return TraceEvent(
            index=index,
            event_type=et.TURN_STARTED,
            timestamp=ts,
            content="",
            update_index=index,
        )
    if typ == "assistant.turn_end":
        return TraceEvent(
            index=index,
            event_type=et.TURN_ENDED,
            timestamp=ts,
            content="",
            update_index=index,
        )
    if typ == "session.shutdown":
        return TraceEvent(
            index=index,
            event_type=et.TURN_COMPLETED,
            timestamp=ts,
            content=str(data.get("shutdownType") or "").strip(),
            update_index=index,
        )
    if typ == "user.message":
        return TraceEvent(
            index=index,
            event_type=et.USER_MESSAGE_CHUNK,
            timestamp=ts,
            content=_text_of(data.get("content")),
            update_index=index,
        )
    if typ == "assistant.message":
        return TraceEvent(
            index=index,
            event_type=et.AGENT_MESSAGE_CHUNK,
            timestamp=ts,
            content=_text_of(data.get("content")),
            update_index=index,
        )
    if typ == "tool.execution_start":
        args = json_mapping(data.get("arguments"))
        name = str(data.get("toolName") or "").strip()
        return TraceEvent(
            index=index,
            event_type=et.TOOL_CALL,
            timestamp=ts,
            tool_name=name,
            tool_call_id=str(data.get("toolCallId") or eid),
            content="",
            raw_input=ToolInputBag(args),
            update_index=index,
        )
    if typ == "tool.execution_complete":
        ok = data.get("success") is True
        return TraceEvent(
            index=index,
            event_type=et.TOOL_CALL_UPDATE,
            timestamp=ts,
            tool_call_id=str(data.get("toolCallId") or eid),
            content=_text_of(data.get("result")),
            is_error=not ok,
            update_index=index,
        )
    if typ == "subagent.started":
        name = str(data.get("agentName") or data.get("agentDisplayName") or "").strip()
        child = str(row.get("agentId") or data.get("agentId") or "").strip()
        return TraceEvent(
            index=index,
            event_type=et.SUBAGENT_SPAWNED,
            timestamp=ts,
            content=name,
            tool_call_id=str(data.get("toolCallId") or ""),
            raw_input=ToolInputBag(
                {
                    "child_session_id": child,
                    "subagent_id": child,
                    "subagent_type": str(data.get("agentType") or ""),
                    "description": str(data.get("agentDescription") or name),
                }
            ),
            update_index=index,
        )
    if typ == "subagent.completed":
        child = str(row.get("agentId") or data.get("agentId") or "").strip()
        cancelled = data.get("cancelled") is True
        return TraceEvent(
            index=index,
            event_type=et.SUBAGENT_FINISHED,
            timestamp=ts,
            content=str(data.get("agentName") or "").strip(),
            tool_call_id=str(data.get("toolCallId") or ""),
            raw_input=ToolInputBag(
                {
                    "child_session_id": child,
                    "subagent_id": child,
                    "status": "cancelled" if cancelled else "completed",
                    "duration_ms": data.get("durationMs"),
                    "tool_calls": data.get("totalToolCalls"),
                    "tokens_used": data.get("totalTokens"),
                }
            ),
            update_index=index,
        )
    return None


def _model_from_events(events: list[JsonObject]) -> str:
    for row in reversed(events):
        typ = str(row.get("type") or "")
        data = json_mapping(row.get("data"))
        if typ in {"assistant.message", "tool.execution_start", "session.shutdown"}:
            mid = str(data.get("model") or data.get("currentModel") or "").strip()
            if mid:
                return mid
    return ""


def _version_from_events(events: list[JsonObject]) -> str:
    for row in events:
        if str(row.get("type") or "") != "session.start":
            continue
        ver = str(json_mapping(row.get("data")).get("copilotVersion") or "").strip()
        if ver:
            return ver
    return ""


def _count_tools(events: list[JsonObject]) -> int:
    return sum(1 for row in events if str(row.get("type") or "") == "tool.execution_start")


def _count_subagents(events: list[JsonObject]) -> int:
    return sum(1 for row in events if str(row.get("type") or "") == "subagent.started")


def _meta_from_row(row: sqlite3.Row, db: Path, events: list[JsonObject]) -> SessionMeta:
    sid = str(row["id"])
    created = Stamp.iso(row["created_at"])
    updated = Stamp.iso(row["updated_at"] or row["created_at"])
    duration = 0.0
    start = Stamp.epoch(row["created_at"])
    end = Stamp.epoch(row["updated_at"] or row["created_at"])
    if start is not None and end is not None:
        duration = max(0.0, float(end - start))
    cwd = str(row["cwd"] or "").strip()
    kids = _count_subagents(events)
    return SessionMeta(
        session_id=sid,
        session_dir=db,
        model_id=_model_from_events(events) or "unknown",
        title=str(row["summary"] or "").strip(),
        created_at=created,
        updated_at=updated,
        duration_seconds=duration,
        run_dir=cwd,
        num_events=len(_timeline_for(events)),
        tool_call_count=_count_tools(events),
        turn_outcome=_turn_outcome(events),
        harness=COPILOT_HARNESS_ID,
        harness_version=_version_from_events(events),
        has_subagents=kids > 0,
        subagent_count=kids,
    )


class CopilotAdapter:
    """``~/.copilot`` sqlite catalog plus ``session-state`` event logs."""

    id = COPILOT_HARNESS_ID
    product = "GitHub Copilot"
    supported_version = "1.0.82"

    def db(self) -> Path:
        return default_db_path()

    def default_host_roots(self) -> list[Path]:
        path = self.db()
        return [path] if path.is_file() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        found: list[SessionRef] = []
        seen: set[str] = set()
        for db in self._dbs_in(roots):
            for ref in self._discover_db(db):
                if ref.session_id in seen:
                    continue
                seen.add(ref.session_id)
                found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == COPILOT_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == COPILOT_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return path.is_file() and path.name == "session-store.db"

    def bind_locator(self, locator: Path) -> SessionRef | None:
        """A database file is the store, not one session."""
        _ = Path(locator)
        return None

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise FileNotFoundError("copilot session id is required")
        db = _assert_readable(db)
        with _connect(db) as con:
            row = _session_row(con, sid)
            if row is None:
                raise FileNotFoundError(f"copilot session not found: {sid}")
            events = list(json_lines(_events_path(db, sid)))
            return _meta_from_row(row, db, events)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            return []
        db = _assert_readable(db)
        return _timeline_for(list(json_lines(_events_path(db, sid))))

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        db = self.db()
        if not db.is_file():
            return None
        try:
            with _connect(db) as con:
                row = _session_row(con, sid)
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return SessionRef(
            harness=COPILOT_HARNESS_ID,
            session_id=sid,
            locator=db,
            cwd=str(row["cwd"] or "").strip(),
        )

    def watch_hints(self) -> tuple[str, ...]:
        return ("session-store.db", "session-store.db-wal", "events.jsonl")

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise RuntimeError("copilot session id is required")
        db = _assert_readable(db)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        members: list[str] = []
        packed = False
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                state = _state_dir(db, sid)
                for name in ("events.jsonl", "workspace.yaml", "session.db"):
                    src = state / name
                    if not src.is_file():
                        continue
                    arc = f"{sid}/{name}"
                    tf.add(src, arcname=arc)
                    members.append(arc)
            if not members:
                raise RuntimeError(f"copilot session has no archive files: {sid}")
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return members

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        from .grok import extract_sid_tarball

        dest = extract_sid_tarball(src, dest_root)
        db = dest / "session.db"
        events = dest / "events.jsonl"
        if not db.is_file() and not events.is_file():
            raise RuntimeError(f"archive is not a copilot session: {src}")
        sid = dest.name
        store = dest_root / "copilot.db"
        if db.is_file():
            shutil.copy2(db, store)
        else:
            store.touch()
        state = _state_dir(store, sid)
        state.mkdir(parents=True, exist_ok=True)
        if events.is_file():
            shutil.copy2(events, state / "events.jsonl")
        workspace = dest / "workspace.yaml"
        if workspace.is_file():
            shutil.copy2(workspace, state / "workspace.yaml")
        return SessionRef(harness=COPILOT_HARNESS_ID, session_id=sid, locator=store)

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        db, sid = _db_from_ref(ref, self.db())
        ev = Stamp.file(_events_path(db, sid)) if sid else (0.0, 0, 0, 0)
        store = Stamp.file(db)
        return (max(ev[0], store[0]), ev[1] + store[1], ev[2], ev[3])

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

    def _dbs_in(self, roots: Sequence[Path | str] | None) -> list[Path]:
        if roots is None:
            path = self.db()
            return [path] if path.is_file() else []
        out: list[Path] = []
        for raw in roots:
            path = Path(raw).expanduser()
            if path.is_file() and path.name == "session-store.db":
                out.append(path)
            elif path.is_dir():
                cand = path / "session-store.db"
                if cand.is_file():
                    out.append(cand)
        return out

    def _discover_db(self, db: Path) -> list[SessionRef]:
        try:
            with _connect(db) as con:
                rows = _list_session_rows(con)
        except sqlite3.Error:
            return []
        found: list[SessionRef] = []
        for row in rows:
            found.append(
                SessionRef(
                    harness=COPILOT_HARNESS_ID,
                    session_id=str(row["id"]),
                    locator=db,
                    cwd=str(row["cwd"] or "").strip(),
                )
            )
        return found


__all__ = [
    "COPILOT_HARNESS_ID",
    "CopilotAdapter",
    "default_db_path",
    "default_store_root",
]
