"""OpenCode disk adapter (SQLite ``opencode.db``).

Sessions are rows, not directories. Never read ``account`` / ``credential``.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

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


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


@dataclass
class _StoreRow:
    id: str
    time_created: JsonValue
    data: JsonObject
    message_id: str = ""


@dataclass
class _Payload:
    info: JsonObject
    messages: list[_StoreRow] = field(default_factory=list)
    parts: list[_StoreRow] = field(default_factory=list)


def _has_events(con: sqlite3.Connection, session_id: str) -> bool:
    if not _table_exists(con, "event"):
        return False
    row = con.execute(
        "SELECT 1 FROM event WHERE aggregate_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    return row is not None


def _payload_from_tables(con: sqlite3.Connection, session_id: str, row: sqlite3.Row) -> _Payload:
    messages = [
        _StoreRow(str(item["id"]), item["time_created"], json_mapping(item["data"]))
        for item in con.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? "
            "ORDER BY time_created ASC, id ASC",
            (session_id,),
        )
    ]
    parts = [
        _StoreRow(
            str(item["id"]),
            item["time_created"],
            json_mapping(item["data"]),
            str(item["message_id"]),
        )
        for item in con.execute(
            "SELECT id, message_id, time_created, data FROM part WHERE session_id = ? "
            "ORDER BY time_created ASC, id ASC",
            (session_id,),
        )
    ]
    info = as_json_object(
        {
            "id": row["id"],
            "parentID": row["parent_id"],
            "directory": row["directory"],
            "title": row["title"],
            "model": row["model"],
            "version": row["version"] if "version" in row.keys() else "",
            "time": {"created": row["time_created"], "updated": row["time_updated"]},
            "time_archived": row["time_archived"] if "time_archived" in row.keys() else None,
        }
    )
    return _Payload(info=info, messages=messages, parts=parts)


def _payload_from_events(con: sqlite3.Connection, session_id: str) -> _Payload:
    info: JsonObject = {}
    messages: dict[str, _StoreRow] = {}
    msg_order: list[str] = []
    parts: dict[str, _StoreRow] = {}
    part_order: list[str] = []
    for typ, raw in con.execute(
        "SELECT type, data FROM event WHERE aggregate_id = ? ORDER BY seq ASC, id ASC",
        (session_id,),
    ):
        payload = json_mapping(raw)
        kind = str(typ or "")
        if kind.startswith("session."):
            extra = payload.get("info")
            if isinstance(extra, dict):
                info = as_json_object(extra)
            continue
        if "part" in kind:
            part = payload.get("part")
            if not isinstance(part, dict) or not part.get("id"):
                continue
            pid = str(part["id"])
            if pid not in parts:
                part_order.append(pid)
            times = json_mapping(part.get("time"))
            parts[pid] = _StoreRow(
                pid,
                times.get("created"),
                as_json_object(part),
                str(part.get("messageID") or ""),
            )
            continue
        if kind.startswith("message."):
            extra = payload.get("info")
            if not isinstance(extra, dict) or not extra.get("id"):
                continue
            mid = str(extra["id"])
            if mid not in messages:
                msg_order.append(mid)
            times = json_mapping(extra.get("time"))
            messages[mid] = _StoreRow(mid, times.get("created"), as_json_object(extra))
    if not info.get("id"):
        info["id"] = session_id
    return _Payload(
        info=info,
        messages=[messages[key] for key in msg_order],
        parts=[parts[key] for key in part_order],
    )


def _load_payload(con: sqlite3.Connection, session_id: str) -> _Payload | None:
    if _has_events(con, session_id):
        return _payload_from_events(con, session_id)
    if not _table_exists(con, "session"):
        return None
    row = _session_row(con, session_id)
    if row is None:
        return None
    return _payload_from_tables(con, session_id, row)


def _discover_event_refs(con: sqlite3.Connection, db: Path) -> list[SessionRef]:
    if not _table_exists(con, "event"):
        return []
    found: list[SessionRef] = []
    seen: set[str] = set()
    for (raw,) in con.execute("SELECT data FROM event WHERE type LIKE 'session.created%'"):
        info = json_mapping(json_mapping(raw).get("info"))
        sid = str(info.get("id") or "").strip()
        if not sid or sid in seen:
            continue
        if str(info.get("parentID") or "").strip() not in {"", "None"}:
            continue
        seen.add(sid)
        found.append(
            SessionRef(
                harness=OPENCODE_HARNESS_ID,
                session_id=sid,
                locator=db,
                cwd=str(info.get("directory") or "").strip(),
            )
        )
    return found


def _child_ids(con: sqlite3.Connection, session_id: str) -> list[str]:
    found: list[str] = []
    if _table_exists(con, "session"):
        found.extend(
            str(row[0])
            for row in con.execute("SELECT id FROM session WHERE parent_id = ?", (session_id,))
        )
    if _table_exists(con, "event"):
        for (raw,) in con.execute("SELECT data FROM event WHERE type LIKE 'session.created%'"):
            info = json_mapping(json_mapping(raw).get("info"))
            if str(info.get("parentID") or "").strip() == session_id:
                child = str(info.get("id") or "").strip()
                if child:
                    found.append(child)
    return list(dict.fromkeys(found))


def _file_diffs(payload: _Payload) -> list[JsonObject]:
    last: list[JsonObject] = []
    for msg in payload.messages:
        if str(msg.data.get("role") or "") != "user":
            continue
        diffs = json_mapping(msg.data.get("summary")).get("diffs")
        if not isinstance(diffs, list):
            continue
        rows = [as_json_object(item) for item in diffs if isinstance(item, dict)]
        if rows:
            last = rows
    return last


def file_diffs_for(ref: SessionRef | Path | str) -> list[JsonObject]:
    """OpenCode ``summary.diffs`` for *ref*, or empty when the store has none."""
    adapter = OpenCodeAdapter()
    db, sid = _db_from_ref(ref, adapter.db())
    if not sid:
        return []
    try:
        db = _assert_readable(db)
    except FileNotFoundError:
        return []
    with _connect(db) as con:
        payload = _load_payload(con, sid)
    if payload is None:
        return []
    return _file_diffs(payload)


def _part_live_token(payload: _Payload) -> str:
    if not payload.parts:
        return ""
    pdata = payload.parts[-1].data
    return from_last(str(json_mapping(pdata.get("state")).get("status") or "").strip())


def _turn_outcome_payload(payload: _Payload) -> str:
    """List status from the last store-written part status or finished assistant."""
    archived = payload.info.get("time_archived")
    if archived in (None, 0, ""):
        archived = json_mapping(payload.info.get("time")).get("archived")
    if archived not in (None, 0, ""):
        return from_last("complete")
    live = _part_live_token(payload)
    if live:
        return live
    if not payload.messages:
        return ""
    data = payload.messages[-1].data
    role = str(data.get("role") or "")
    if role == "assistant" and json_mapping(data.get("time")).get("completed") not in (
        None,
        0,
        "",
    ):
        return from_last("complete")
    return from_last(role)


def _meta_from_payload(payload: _Payload, db: Path, child_count: int) -> SessionMeta:
    info = payload.info
    sid = str(info.get("id") or "")
    times = json_mapping(info.get("time"))
    created = Stamp.iso(times.get("created"))
    updated = Stamp.iso(times.get("updated") or times.get("created"))
    duration = 0.0
    start = Stamp.epoch(times.get("created"))
    end = Stamp.epoch(times.get("updated") or times.get("created"))
    if start is not None and end is not None:
        duration = float(max(0, end - start))
    cwd = str(info.get("directory") or "").strip()
    tools = sum(1 for part in payload.parts if str(part.data.get("type") or "") == "tool")
    return SessionMeta(
        session_id=sid,
        session_dir=db,
        model_id=_model_id(info.get("model")),
        title=str(info.get("title") or "").strip(),
        created_at=created,
        updated_at=updated,
        duration_seconds=duration,
        run_dir=cwd,
        turn_outcome=_turn_outcome_payload(payload),
        harness=OPENCODE_HARNESS_ID,
        harness_version=str(info.get("version") or "").strip(),
        num_events=len(payload.parts) or len(payload.messages),
        tool_call_count=tools,
        subagent_count=child_count,
        has_subagents=child_count > 0,
    )


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
            payload = _load_payload(con, sid)
            if payload is None:
                raise FileNotFoundError(f"opencode session not found: {sid}")
            return _meta_from_payload(payload, db, len(_child_ids(con, sid)))

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        db, sid = _db_from_ref(ref, self.db())
        if not sid:
            return []
        db = _assert_readable(db)
        with _connect(db) as con:
            payload = _load_payload(con, sid)
            if payload is None:
                return []
            return _timeline_from_payload(payload)

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        db = self.db()
        if not db.is_file():
            return None
        try:
            with _connect(db) as con:
                payload = _load_payload(con, sid)
        except sqlite3.Error:
            return None
        if payload is None:
            return None
        return SessionRef(
            harness=OPENCODE_HARNESS_ID,
            session_id=sid,
            locator=db,
            cwd=str(payload.info.get("directory") or "").strip(),
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
            kids = _child_ids(con, sid)
            targets = [*kids, sid]
            for item in targets:
                if _table_exists(con, "event"):
                    con.execute("DELETE FROM event WHERE aggregate_id = ?", (item,))
                if _table_exists(con, "event_sequence"):
                    con.execute("DELETE FROM event_sequence WHERE aggregate_id = ?", (item,))
                if _table_exists(con, "part"):
                    con.execute("DELETE FROM part WHERE session_id = ?", (item,))
                if _table_exists(con, "message"):
                    con.execute("DELETE FROM message WHERE session_id = ?", (item,))
                if _table_exists(con, "session"):
                    con.execute("DELETE FROM session WHERE id = ?", (item,))
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
                rows = _list_session_rows(con) if _table_exists(con, "session") else []
                found: list[SessionRef] = []
                seen: set[str] = set()
                for row in rows:
                    if _is_child(row):
                        continue
                    sid = str(row["id"])
                    seen.add(sid)
                    found.append(
                        SessionRef(
                            harness=OPENCODE_HARNESS_ID,
                            session_id=sid,
                            locator=db,
                            cwd=str(row["directory"] or "").strip(),
                        )
                    )
                for ref in _discover_event_refs(con, db):
                    if ref.session_id not in seen:
                        found.append(ref)
                return found
        except sqlite3.Error:
            return []


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
        payload = _load_payload(con, session_id)
        if payload is None:
            raise FileNotFoundError(f"opencode session not found: {session_id}")
        times = json_mapping(payload.info.get("time"))
        session = as_json_object(
            {
                "id": payload.info.get("id"),
                "parent_id": payload.info.get("parentID"),
                "directory": payload.info.get("directory"),
                "title": payload.info.get("title"),
                "model": payload.info.get("model"),
                "version": payload.info.get("version"),
                "time_created": times.get("created"),
                "time_updated": times.get("updated"),
                "time_archived": payload.info.get("time_archived"),
            }
        )
        messages = [
            as_json_object(
                {
                    "id": item.id,
                    "session_id": session_id,
                    "time_created": item.time_created,
                    "time_updated": item.time_created,
                    "data": item.data,
                }
            )
            for item in payload.messages
        ]
        parts = [
            as_json_object(
                {
                    "id": item.id,
                    "message_id": item.message_id,
                    "session_id": session_id,
                    "time_created": item.time_created,
                    "time_updated": item.time_created,
                    "data": item.data,
                }
            )
            for item in payload.parts
        ]
    return as_json_object({"session": session, "messages": messages, "parts": parts})


def _timeline_from_payload(payload: _Payload) -> list[TraceEvent]:
    by_msg: dict[str, list[_StoreRow]] = {}
    for part in payload.parts:
        by_msg.setdefault(part.message_id, []).append(part)
    events: list[TraceEvent] = []
    turn = 0
    for msg in payload.messages:
        _append_message(events, msg, by_msg.get(msg.id, []), turn)
        if str(msg.data.get("role") or "") == "user":
            turn += 1
    for i, ev in enumerate(events):
        ev.index = i
    return events


def _append_message(
    events: list[TraceEvent],
    msg: _StoreRow,
    parts: list[_StoreRow],
    turn: int,
) -> None:
    data = msg.data
    role = str(data.get("role") or "")
    ts = Stamp.epoch(msg.time_created)
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


def _parts_text(parts: list[_StoreRow]) -> str:
    bits: list[str] = []
    for part in parts:
        data = part.data
        if str(data.get("type") or "") != "text":
            continue
        text = str(data.get("text") or "").strip()
        if text:
            bits.append(text)
    return "\n".join(bits)


def _append_part(events: list[TraceEvent], part: _StoreRow) -> None:
    data = part.data
    kind = str(data.get("type") or "")
    ts = Stamp.epoch(part.time_created)
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
    "file_diffs_for",
]
