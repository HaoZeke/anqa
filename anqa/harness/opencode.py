"""OpenCode disk adapter (SQLite ``opencode.db``).

Sessions are rows, not directories. Never read ``account`` / ``credential``.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from .. import event_types as et
from ..models import (
    JsonObject,
    JsonValue,
    SessionMeta,
    ToolInputBag,
    TraceEvent,
    as_json_object,
    json_mapping,
)
from ..stamp import Stamp
from .ref import SessionRef
from .status import from_last

OPENCODE_HARNESS_ID = "opencode"


def default_db_path() -> Path:
    """Host OpenCode database path (resolved at call time)."""
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _connect(db: Path) -> sqlite3.Connection:
    path = Path(db).expanduser()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _assert_readable(db: Path) -> Path:
    path = Path(db).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"opencode database not found: {path}")
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


def _model_id(raw: JsonValue) -> str:
    bag = json_mapping(raw)
    mid = str(bag.get("id") or bag.get("modelID") or "").strip()
    provider = str(bag.get("providerID") or bag.get("provider") or "").strip()
    if mid and provider:
        return f"{provider}/{mid}"
    return mid or provider or "unknown"


def _session_row(con: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT id, parent_id, directory, title, model, version, time_created, time_updated, "
        "time_archived, tokens_input, tokens_output, tokens_reasoning, cost "
        "FROM session WHERE id = ?",
        (session_id,),
    ).fetchone()


def _list_session_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        con.execute(
            "SELECT id, parent_id, directory, title, model, version, time_created, time_updated, "
            "time_archived, tokens_input, tokens_output, tokens_reasoning, cost "
            "FROM session ORDER BY time_updated DESC"
        )
    )


def _is_child(row: sqlite3.Row) -> bool:
    parent = row["parent_id"]
    return bool(parent) and str(parent).strip() not in {"", "None"}


def _meta_from_row(row: sqlite3.Row, db: Path) -> SessionMeta:
    sid = str(row["id"])
    created = Stamp.iso(row["time_created"])
    updated = Stamp.iso(row["time_updated"] or row["time_created"])
    duration = 0.0
    start = row["time_created"]
    end = row["time_updated"] or row["time_created"]
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        duration = max(0.0, (float(end) - float(start)) / 1000.0)
    cwd = str(row["directory"] or "").strip()
    return SessionMeta(
        session_id=sid,
        session_dir=db,
        model_id=_model_id(row["model"]),
        title=str(row["title"] or "").strip(),
        created_at=created,
        updated_at=updated,
        duration_seconds=duration,
        run_dir=cwd,
        turn_outcome="",
        harness=OPENCODE_HARNESS_ID,
        harness_version=str(row["version"] or "").strip() if "version" in row.keys() else "",
    )


def _part_live_token(con: sqlite3.Connection, session_id: str) -> str:
    last_part = con.execute(
        "SELECT data FROM part WHERE session_id = ? ORDER BY time_created DESC, id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if last_part is None:
        return ""
    pdata = json_mapping(last_part["data"])
    return from_last(str(json_mapping(pdata.get("state")).get("status") or "").strip())


def _turn_outcome(con: sqlite3.Connection, session_id: str, row: sqlite3.Row) -> str:
    """List status from the last store-written part status or finished assistant."""
    archived = row["time_archived"] if "time_archived" in row.keys() else None
    if archived not in (None, 0, ""):
        return from_last("complete")
    live = _part_live_token(con, session_id)
    if live:
        return live
    last_msg = con.execute(
        "SELECT data FROM message WHERE session_id = ? ORDER BY time_created DESC, id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if last_msg is None:
        return ""
    data = json_mapping(last_msg["data"])
    role = str(data.get("role") or "")
    if role == "assistant" and json_mapping(data.get("time")).get("completed") not in (
        None,
        0,
        "",
    ):
        return from_last("complete")
    return from_last(role)


def _count_parts(con: sqlite3.Connection, session_id: str) -> int:
    raw = con.execute(
        "SELECT COUNT(*) FROM part WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(raw[0]) if raw is not None else 0


def _count_tools(con: sqlite3.Connection, session_id: str) -> int:
    raw = con.execute(
        "SELECT COUNT(*) FROM part WHERE session_id = ? AND json_extract(data, '$.type') = 'tool'",
        (session_id,),
    ).fetchone()
    return int(raw[0]) if raw is not None else 0


def _row_mapping(row: sqlite3.Row) -> JsonObject:
    return as_json_object({str(key): cast(JsonValue, row[key]) for key in row.keys()})


class OpenCodeAdapter:
    """Read-only OpenCode sqlite adapter."""

    id: str = OPENCODE_HARNESS_ID
    product: str = "OpenCode"
    supported_version: str = "1.18.25"

    def db(self) -> Path:
        """Host database path."""
        return default_db_path()

    def default_host_roots(self) -> list[Path]:
        path = self.db()
        return [path] if path.is_file() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        dbs = self._dbs_in(roots)
        found: list[SessionRef] = []
        seen: set[str] = set()
        for db in dbs:
            for ref in self._discover_db(db):
                if ref.session_id in seen:
                    continue
                seen.add(ref.session_id)
                found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == OPENCODE_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == OPENCODE_HARNESS_ID
        path = Path(str(ref)).expanduser()
        return path.is_file() and path.name == "opencode.db"

    def bind_locator(self, locator: Path) -> SessionRef | None:
        """A database file is the store, not one session."""
        _ = Path(locator)
        return None

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise FileNotFoundError("opencode session id is required")
        db = _assert_readable(db)
        with _connect(db) as con:
            row = _session_row(con, sid)
            if row is None:
                raise FileNotFoundError(f"opencode session not found: {sid}")
            meta = _meta_from_row(row, db)
            meta.turn_outcome = _turn_outcome(con, sid, row)
            meta.num_events = _count_parts(con, sid)
            meta.tool_call_count = _count_tools(con, sid)
            kids = con.execute(
                "SELECT COUNT(*) FROM session WHERE parent_id = ?",
                (sid,),
            ).fetchone()
            n_kids = int(kids[0]) if kids is not None else 0
            meta.subagent_count = n_kids
            meta.has_subagents = n_kids > 0
            return meta

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            return []
        db = _assert_readable(db)
        with _connect(db) as con:
            return _timeline_for(con, sid)

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
            harness=OPENCODE_HARNESS_ID,
            session_id=sid,
            locator=db,
            cwd=str(row["directory"] or "").strip(),
        )

    def watch_hints(self) -> tuple[str, ...]:
        return ("opencode.db", "opencode.db-wal")

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise RuntimeError("opencode session id is required")
        db = _assert_readable(db)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        try:
            payload = _session_export(db, sid)
            blob = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            name = f"{sid}/session.json"
            with tarfile.open(tmp, "w:gz") as tf:
                info = tarfile.TarInfo(name=name)
                info.size = len(blob)
                tf.addfile(info, io.BytesIO(blob))
            tmp.replace(dest)
            packed = True
        except (OSError, tarfile.TarError, sqlite3.Error) as exc:
            raise RuntimeError(f"failed to pack session archive: {exc}") from exc
        finally:
            if not packed:
                tmp.unlink(missing_ok=True)
        return [name]

    def open_archive(self, src: Path, dest_root: Path) -> SessionRef:
        from .grok import extract_sid_tarball

        dest = extract_sid_tarball(src, dest_root)
        payload = dest / "session.json"
        if not payload.is_file():
            raise RuntimeError(f"archive is not an opencode session: {src}")
        db = dest_root / "opencode.db"
        _session_import(db, payload)
        return SessionRef(
            harness=OPENCODE_HARNESS_ID,
            session_id=dest.name,
            locator=db,
        )

    def load_detail(self, ref: SessionRef | Path | str) -> SessionMeta:
        return self.load_meta(ref)

    def timeline_stamp(self, ref: SessionRef | Path | str) -> tuple[float, int, int, int]:
        db, _sid = _db_from_ref(ref, self.db())
        return Stamp.file(db)

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
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            raise FileNotFoundError("opencode session id is required")
        db = _assert_readable(db)
        con = sqlite3.connect(str(db))
        try:
            kids = [
                str(row[0])
                for row in con.execute("SELECT id FROM session WHERE parent_id = ?", (sid,))
            ]
            for child in kids:
                con.execute("DELETE FROM part WHERE session_id = ?", (child,))
                con.execute("DELETE FROM message WHERE session_id = ?", (child,))
                con.execute("DELETE FROM session WHERE id = ?", (child,))
            con.execute("DELETE FROM part WHERE session_id = ?", (sid,))
            con.execute("DELETE FROM message WHERE session_id = ?", (sid,))
            con.execute("DELETE FROM session WHERE id = ?", (sid,))
            con.commit()
        finally:
            con.close()

    def _dbs_in(self, roots: Sequence[Path | str] | None) -> list[Path]:
        if roots is None:
            path = self.db()
            return [path] if path.is_file() else []
        out: list[Path] = []
        for raw in roots:
            path = Path(raw).expanduser()
            if path.is_file() and path.name == "opencode.db":
                out.append(path)
            elif path.is_dir():
                cand = path / "opencode.db"
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
            if _is_child(row):
                continue
            found.append(
                SessionRef(
                    harness=OPENCODE_HARNESS_ID,
                    session_id=str(row["id"]),
                    locator=db,
                    cwd=str(row["directory"] or "").strip(),
                )
            )
        return found


def _session_import(db: Path, payload_path: Path) -> None:
    """Write an exported OpenCode session.json into *db*."""
    raw = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"opencode archive payload is not an object: {payload_path}")
    session = raw.get("session")
    if not isinstance(session, dict) or not str(session.get("id") or "").strip():
        raise RuntimeError(f"opencode archive missing session id: {payload_path}")
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS session ("
            "id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT, "
            "model TEXT, version TEXT, time_created INTEGER, time_updated INTEGER, "
            "time_archived INTEGER, tokens_input INTEGER, tokens_output INTEGER, "
            "tokens_reasoning INTEGER, cost REAL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS message ("
            "id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
            "time_updated INTEGER, data TEXT)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS part ("
            "id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
            "time_created INTEGER, time_updated INTEGER, data TEXT)"
        )
        cols = (
            "id",
            "parent_id",
            "directory",
            "title",
            "model",
            "version",
            "time_created",
            "time_updated",
            "time_archived",
            "tokens_input",
            "tokens_output",
            "tokens_reasoning",
            "cost",
        )
        con.execute(
            f"INSERT OR REPLACE INTO session ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            tuple(session.get(col) for col in cols),
        )
        for msg in raw.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            data = msg.get("data")
            if not isinstance(data, str):
                data = json.dumps(data, ensure_ascii=False)
            con.execute(
                "INSERT OR REPLACE INTO message "
                "(id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
                (
                    msg.get("id"),
                    msg.get("session_id"),
                    msg.get("time_created"),
                    msg.get("time_updated"),
                    data,
                ),
            )
        for part in raw.get("parts") or []:
            if not isinstance(part, dict):
                continue
            data = part.get("data")
            if not isinstance(data, str):
                data = json.dumps(data, ensure_ascii=False)
            con.execute(
                "INSERT OR REPLACE INTO part "
                "(id, message_id, session_id, time_created, time_updated, data) "
                "VALUES (?,?,?,?,?,?)",
                (
                    part.get("id"),
                    part.get("message_id"),
                    part.get("session_id"),
                    part.get("time_created"),
                    part.get("time_updated"),
                    data,
                ),
            )
        con.commit()
    finally:
        con.close()


def _session_export(db: Path, session_id: str) -> JsonObject:
    with _connect(db) as con:
        row = _session_row(con, session_id)
        if row is None:
            raise FileNotFoundError(f"opencode session not found: {session_id}")
        messages = [
            _row_mapping(item)
            for item in con.execute(
                "SELECT id, session_id, time_created, time_updated, data "
                "FROM message WHERE session_id = ? ORDER BY time_created ASC, id ASC",
                (session_id,),
            )
        ]
        parts = [
            _row_mapping(item)
            for item in con.execute(
                "SELECT id, message_id, session_id, time_created, time_updated, data "
                "FROM part WHERE session_id = ? ORDER BY time_created ASC, id ASC",
                (session_id,),
            )
        ]
    return as_json_object(
        {
            "session": _row_mapping(row),
            "messages": messages,
            "parts": parts,
        }
    )


def _timeline_for(con: sqlite3.Connection, session_id: str) -> list[TraceEvent]:
    messages = list(
        con.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? "
            "ORDER BY time_created ASC, id ASC",
            (session_id,),
        )
    )
    parts = list(
        con.execute(
            "SELECT id, message_id, time_created, data FROM part WHERE session_id = ? "
            "ORDER BY time_created ASC, id ASC",
            (session_id,),
        )
    )
    by_msg: dict[str, list[sqlite3.Row]] = {}
    for part in parts:
        by_msg.setdefault(str(part["message_id"]), []).append(part)
    events: list[TraceEvent] = []
    turn = 0
    for msg in messages:
        _append_message(events, msg, by_msg.get(str(msg["id"]), []), turn)
        data = json_mapping(msg["data"])
        if str(data.get("role") or "") == "user":
            turn += 1
    for i, ev in enumerate(events):
        ev.index = i
    return events


def _append_message(
    events: list[TraceEvent],
    msg: sqlite3.Row,
    parts: list[sqlite3.Row],
    turn: int,
) -> None:
    data = json_mapping(msg["data"])
    role = str(data.get("role") or "")
    ts = Stamp.epoch(msg["time_created"])
    if role == "user":
        events.append(
            TraceEvent(
                index=0,
                event_type=et.TURN_STARTED,
                timestamp=ts,
                content=f"turn_number={turn}",
            )
        )
        text = _parts_text(parts) or str(data.get("content") or "").strip()
        events.append(
            TraceEvent(
                index=0,
                event_type=et.USER_MESSAGE_CHUNK,
                timestamp=ts,
                content=text,
            )
        )
        return
    for part in parts:
        _append_part(events, part)


def _parts_text(parts: list[sqlite3.Row]) -> str:
    bits: list[str] = []
    for part in parts:
        data = json_mapping(part["data"])
        if str(data.get("type") or "") != "text":
            continue
        text = str(data.get("text") or "").strip()
        if text:
            bits.append(text)
    return "\n".join(bits)


def _append_part(events: list[TraceEvent], part: sqlite3.Row) -> None:
    data = json_mapping(part["data"])
    kind = str(data.get("type") or "")
    ts = Stamp.epoch(part["time_created"])
    if kind == "text":
        events.append(
            TraceEvent(
                index=0,
                event_type=et.AGENT_MESSAGE_CHUNK,
                timestamp=ts,
                content=str(data.get("text") or ""),
            )
        )
        return
    if kind == "reasoning":
        events.append(
            TraceEvent(
                index=0,
                event_type=et.AGENT_THOUGHT_CHUNK,
                timestamp=ts,
                content=str(data.get("text") or ""),
            )
        )
        return
    if kind == "tool":
        events.extend(_tool_events(data, ts))


def _task_child_id(data: JsonObject, state: JsonObject, inn: JsonObject) -> str:
    meta = json_mapping(state.get("metadata"))
    if not meta:
        meta = json_mapping(data.get("metadata"))
    child = str(meta.get("sessionId") or "").strip()
    if child:
        return child
    if str(inn.get("subagent_type") or "").strip():
        out = str(state.get("output") or "")
        marker = '<task id="'
        if marker in out:
            rest = out.split(marker, 1)[1]
            return rest.split('"', 1)[0].strip()
    return ""


def _tool_events(data: JsonObject, ts: int | None) -> list[TraceEvent]:
    name = str(data.get("tool") or "tool").strip() or "tool"
    call_id = str(data.get("callID") or data.get("call_id") or "").strip()
    state = json_mapping(data.get("state"))
    raw_in = state.get("input")
    inn = json_mapping(raw_in)
    bag = ToolInputBag(inn)
    status = str(state.get("status") or "").strip().lower()
    failed = status in {"error", "failed"} or bool(state.get("error"))
    out = str(state.get("output") or "")
    events = [
        TraceEvent(
            index=0,
            event_type=et.TOOL_CALL,
            timestamp=ts,
            content=name,
            tool_name=name,
            tool_call_id=call_id,
            raw_input=bag,
            is_error=failed,
        ),
        TraceEvent(
            index=0,
            event_type=et.TOOL_CALL_UPDATE,
            timestamp=ts,
            content=out,
            tool_name=name,
            tool_call_id=call_id,
            raw_input=bag,
            is_error=failed,
        ),
    ]
    child = _task_child_id(data, state, inn)
    if name == "task" and child:
        events.extend(_task_bookends(child, inn, state, ts, failed, out))
    return events


def _task_bookends(
    child: str,
    inn: JsonObject,
    state: JsonObject,
    ts: int | None,
    failed: bool,
    output: str,
) -> list[TraceEvent]:
    typ = str(inn.get("subagent_type") or "").strip()
    desc = str(inn.get("description") or state.get("title") or "").strip()
    spawn_bag = ToolInputBag(
        {
            "child_session_id": child,
            "subagent_type": typ,
            "description": desc,
        }
    )
    times = json_mapping(state.get("time"))
    start, end = times.get("start"), times.get("end")
    duration_ms: int | None = None
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        duration_ms = max(0, int(end) - int(start))
    finish_raw = as_json_object(
        {
            "child_session_id": child,
            "subagent_type": typ,
            "description": desc,
            "status": "failed" if failed else "completed",
            "output": output[:2000],
        }
    )
    if duration_ms is not None:
        finish_raw["duration_ms"] = duration_ms
    spawn = TraceEvent(
        index=0,
        event_type=et.SUBAGENT_SPAWNED,
        timestamp=ts,
        content=f"spawned {typ}: {desc}".strip(),
        raw_input=spawn_bag,
        is_error=failed,
    )
    finish = TraceEvent(
        index=0,
        event_type=et.SUBAGENT_FINISHED,
        timestamp=ts,
        content=output[:400],
        raw_input=ToolInputBag(finish_raw),
        is_error=failed,
    )
    return [spawn, finish]


__all__ = [
    "OPENCODE_HARNESS_ID",
    "OpenCodeAdapter",
    "default_db_path",
]
