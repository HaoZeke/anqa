"""Versioned local control protocol over a private Unix socket."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject, JsonValue, as_json_object, json_as_int, json_as_str
from ..notes import (
    NoteEntry,
    NotesConflict,
    NotesSnapshot,
    delete_note,
    load_schema,
    notes_snapshot,
    upsert_note,
)
from .editor import SUPPORTED_FORMATS, EditorDocument, render_editor_document

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
NOTIFY_TIMEOUT_SECONDS = 2.0
MAX_HEADER_LINES = 32
# JSON-RPC bodies always open with ``{``; anything shaped like ``Name:`` is an
# LSP-style framing header regardless of which header comes first.
_HEADER_LINE_RE = re.compile(rb"^[A-Za-z][A-Za-z0-9-]*:")
# Note ids and field ids are woven verbatim into the Org property drawers and
# ``<!-- groket:… -->`` machine tags of every projection; whitespace, ``-->``
# or newlines there corrupt the round trip, so reject them at the boundary.
_NOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TIMESTAMP_RE = re.compile(r"^[0-9][0-9T:+.Z-]{0,63}$")
DEFAULT_SESSION_LIST_LIMIT = 200
CAPABILITIES = (
    "session/list",
    "session/open",
    "session/render",
    "notes/list",
    "notes/upsert",
    "notes/delete",
)

type SessionResolver = Callable[[str], Path | None]
type SessionLister = Callable[[], list[JsonObject]]
type OpenSession = Callable[[Path, int | None], Awaitable[bool]]
type NotesChanged = Callable[[Path], Awaitable[None]]


@dataclass(frozen=True)
class ControlError(Exception):
    """JSON-RPC error with stable structured data."""

    code: int
    message: str
    data: JsonObject | None = None


@dataclass(frozen=True)
class ControlSocketInUse(Exception):
    """Another live process already owns the control socket (singleton)."""

    socket_path: Path

    def __str__(self) -> str:
        return f"control socket is active: {self.socket_path}"


def default_socket_path() -> Path:
    """Return the per-user runtime socket path."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    root = Path(runtime) if runtime else Path.home() / ".groket" / "run"
    return root / "groket" / "control.sock" if runtime else root / "control.sock"


def _default_resolve_session(reference: str) -> Path | None:
    path = Path(reference).expanduser()
    return path.resolve() if path.is_dir() else None


def _session_list_haystack(entry: JsonObject) -> str:
    parts = (
        json_as_str(entry.get("sessionId")),
        json_as_str(entry.get("path")),
        json_as_str(entry.get("title")),
        json_as_str(entry.get("label")),
        json_as_str(entry.get("model")),
        json_as_str(entry.get("status")),
        json_as_str(entry.get("outcome")),
        json_as_str(entry.get("origin")),
    )
    return " ".join(part for part in parts if part).casefold()


def _optional_int_param(value: JsonValue | None, *, name: str) -> int | None:
    """Parse an optional JSON-RPC integer param, or raise ``ControlError`` (-32602)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ControlError(-32602, f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ControlError(-32602, f"{name} must be an integer")
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ControlError(-32602, f"{name} must be an integer") from exc
    raise ControlError(-32602, f"{name} must be an integer")


def filter_session_catalog(
    sessions: list[JsonObject],
    *,
    query: str = "",
    limit: int | None = None,
) -> JsonObject:
    """Filter and cap a catalog snapshot for ``session/list``.

    :param sessions: Full catalog rows (already shaped for the wire).
    :param query: Case-insensitive substring across id/path/title/label/model/status/outcome/origin.
    :param limit: Max rows to return after filtering; ``None`` means default cap.
    :returns: Mapping with ``sessions``, ``total``, and ``matched``.
    """
    needle = (query or "").strip().casefold()
    if needle:
        matched = [row for row in sessions if needle in _session_list_haystack(row)]
    else:
        matched = list(sessions)
    cap = DEFAULT_SESSION_LIST_LIMIT if limit is None else max(0, limit)
    sessions_out: list[JsonValue] = list(matched[:cap])
    return {
        "sessions": sessions_out,
        "total": len(sessions),
        "matched": len(matched),
    }


def _note_mapping(note: NoteEntry) -> JsonObject:
    return {
        "id": note.id,
        "turnIndex": note.turn_index,
        "fields": dict(note.fields),
        "eventIndices": list(note.event_indices),
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
    }


def _snapshot_mapping(snapshot: NotesSnapshot) -> JsonObject:
    schema = load_schema()
    return {
        "revision": snapshot.revision,
        "schema": {
            "id": schema.schema_id,
            "fields": [
                {
                    "id": field.id,
                    "label": field.label,
                    "choices": list(field.choices),
                    "pick": field.pick,
                }
                for field in schema.fields
            ],
        },
        "notes": [_note_mapping(note) for note in snapshot.doc.sorted_notes()],
    }


def _note_from_params(data: JsonObject) -> NoteEntry:
    note_id = json_as_str(data.get("id")).strip()
    if not note_id:
        raise ControlError(-32602, "note.id is required")
    if not _NOTE_TOKEN_RE.match(note_id):
        raise ControlError(-32602, "note.id must match [A-Za-z0-9][A-Za-z0-9._-]*")
    raw_fields = data.get("fields")
    fields = (
        {str(key): str(value) for key, value in raw_fields.items() if value is not None}
        if isinstance(raw_fields, dict)
        else {}
    )
    for key in fields:
        if not _NOTE_TOKEN_RE.match(key):
            raise ControlError(-32602, f"field id {key!r} must match [A-Za-z0-9][A-Za-z0-9._-]*")
    created_at = json_as_str(data.get("createdAt"))
    updated_at = json_as_str(data.get("updatedAt"))
    for stamp_name, stamp in (("createdAt", created_at), ("updatedAt", updated_at)):
        if stamp and not _TIMESTAMP_RE.match(stamp):
            raise ControlError(-32602, f"{stamp_name} must be a compact timestamp")
    raw_indices = data.get("eventIndices")
    event_indices = (
        [json_as_int(value) for value in raw_indices] if isinstance(raw_indices, list) else []
    )
    return NoteEntry(
        id=note_id,
        turn_index=json_as_int(data.get("turnIndex")),
        fields=fields,
        event_indices=event_indices,
        created_at=created_at,
        updated_at=updated_at,
    )


class ControlServer:
    """Async JSON-RPC server for local editor integrations."""

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        resolve_session: SessionResolver | None = None,
        list_sessions: SessionLister | None = None,
        open_session: OpenSession | None = None,
        notes_changed: NotesChanged | None = None,
    ) -> None:
        self.socket_path = Path(socket_path or default_socket_path()).expanduser()
        self._resolve_session = resolve_session or _default_resolve_session
        self._list_sessions = list_sessions
        self._open_session = open_session
        self._notes_changed = notes_changed
        self._server: asyncio.AbstractServer | None = None
        self._lock_fd: int | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._writer_framing: dict[asyncio.StreamWriter, str] = {}

    async def start(self) -> None:
        """Bind the configured socket and begin accepting connections."""
        socket_parent = self.socket_path.parent
        try:
            socket_parent.mkdir(parents=True)
        except FileExistsError:
            if not socket_parent.is_dir():
                raise
        try:
            socket_parent.chmod(0o700)
        except OSError:
            logger.warning("could not tighten permissions on %s", socket_parent)
        self._acquire_lock()
        try:
            await self._takeover_or_fail()
            try:
                self._server = await asyncio.start_unix_server(
                    self._handle_client,
                    path=self.socket_path,
                    limit=MAX_MESSAGE_BYTES + 1,
                )
            except OSError as exc:
                if exc.errno in {errno.EADDRINUSE, errno.EEXIST}:
                    raise ControlSocketInUse(self.socket_path) from exc
                raise
            self.socket_path.chmod(0o600)
        except BaseException:
            self._release_lock()
            raise

    def _acquire_lock(self) -> None:
        """Take the exclusive advisory lock that serializes socket ownership.

        The lock removes the probe/unlink/bind race between two starting
        instances and lets ``close()`` know the socket file is still ours to
        remove. The lock file itself is never unlinked: removing it would
        reopen the race for a third starter.
        """
        lock_path = self.socket_path.with_name(self.socket_path.name + ".lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(lock_fd)
            raise ControlSocketInUse(self.socket_path) from exc
        self._lock_fd = lock_fd

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    async def _takeover_or_fail(self) -> None:
        """Remove a stale socket file, or refuse when a live owner answers.

        The probe still matters with the lock held: an owner from a build
        without the lock file may hold the path, and it must not be stolen.
        """
        if not self.socket_path.exists():
            return
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=0.5,
            )
        except TimeoutError as exc:
            # Ambiguous: prefer not to unlink a path that may still be owned.
            raise ControlSocketInUse(self.socket_path) from exc
        except (ConnectionRefusedError, FileNotFoundError):
            self.socket_path.unlink(missing_ok=True)
        except OSError as exc:
            if exc.errno in {errno.ECONNREFUSED, errno.ENOENT}:
                self.socket_path.unlink(missing_ok=True)
            else:
                # Do not unlink on unexpected errors (avoids stealing a live socket).
                raise ControlSocketInUse(self.socket_path) from exc
        else:
            writer.close()
            await writer.wait_closed()
            raise ControlSocketInUse(self.socket_path)

    async def serve_forever(self) -> None:
        """Serve until cancelled, then release the owned socket.

        :raises ControlSocketInUse: When another live owner holds the path.
        """
        if self._server is None:
            await self.start()
        assert self._server is not None
        try:
            await self._server.serve_forever()
        finally:
            await self.close()

    async def close(self) -> None:
        """Close listeners and connected streams."""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        writers = list(self._writers)
        self._writers.clear()
        self._writer_framing.clear()
        for writer in writers:
            writer.close()
        for writer in writers:
            try:
                await writer.wait_closed()
            except OSError:
                pass
        # Unlink only while holding the ownership lock; another instance may
        # have bound a fresh socket at this path since we lost or never had it.
        if self._lock_fd is not None:
            self.socket_path.unlink(missing_ok=True)
        self._release_lock()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while not reader.at_eof():
                try:
                    first_line = await reader.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    await self._send_error(writer, None, -32600, "message exceeds size limit")
                    break
                if not first_line:
                    break
                if _HEADER_LINE_RE.match(first_line):
                    self._writer_framing[writer] = "headers"
                    length = await self._read_header_length(reader, writer, first_line)
                    if length is None:
                        break
                    try:
                        message = await reader.readexactly(length)
                    except asyncio.IncompleteReadError:
                        break
                else:
                    self._writer_framing.setdefault(writer, "newline")
                    message = first_line
                if len(message) > MAX_MESSAGE_BYTES:
                    await self._send_error(writer, None, -32600, "message exceeds size limit")
                    break
                # Broadcast only once the client's framing is known; a
                # notification sent earlier would use a framing the peer may
                # not speak and desynchronize it permanently.
                self._writers.add(writer)
                await self._handle_line(message, writer)
        finally:
            self._writers.discard(writer)
            self._writer_framing.pop(writer, None)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _read_header_length(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        first_line: bytes,
    ) -> int | None:
        """Consume an LSP-style header block and return the Content-Length.

        Accepts any header order (e.g. ``Content-Type`` first); replies with a
        -32600 error and returns ``None`` when the block is unusable.
        """
        length: int | None = None
        header = first_line
        header_count = 0
        while header not in (b"\r\n", b"\n", b""):
            header_count += 1
            if header_count > MAX_HEADER_LINES:
                await self._send_error(writer, None, -32600, "too many framing headers")
                return None
            name, _, value = header.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    await self._send_error(writer, None, -32600, "invalid Content-Length")
                    return None
            header = await reader.readline()
        if length is None:
            await self._send_error(writer, None, -32600, "missing Content-Length")
            return None
        if length < 0 or length > MAX_MESSAGE_BYTES:
            await self._send_error(writer, None, -32600, "message exceeds size limit")
            return None
        return length

    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await self._send_error(writer, None, -32700, "parse error")
            return
        if not isinstance(raw, dict):
            await self._send_error(writer, None, -32600, "invalid request")
            return
        request = as_json_object(raw)
        request_id = request.get("id")
        method = json_as_str(request.get("method"))
        if request.get("jsonrpc") != "2.0" or not method:
            await self._send_error(writer, request_id, -32600, "invalid request")
            return
        params_raw = request.get("params")
        params = as_json_object(params_raw) if isinstance(params_raw, dict) else {}
        after_send: list[tuple[str, JsonObject]] = []
        try:
            result = await self._dispatch(method, params, after_send)
        except NotesConflict as exc:
            await self._send_error(
                writer,
                request_id,
                409,
                "operator notes changed",
                {"kind": "notes_conflict", "currentRevision": exc.current_revision},
            )
            return
        except ControlError as exc:
            await self._send_error(writer, request_id, exc.code, exc.message, exc.data)
            return
        except OSError as exc:
            await self._send_error(writer, request_id, -32603, str(exc))
            return
        except Exception as exc:
            logger.exception("control method %s failed", method)
            await self._send_error(writer, request_id, -32603, f"internal error: {exc}")
            return
        if request_id is not None:
            await self._send(writer, {"jsonrpc": "2.0", "id": request_id, "result": result})
        # Broadcasts go out after the response so the requesting client can update
        # its own revision first and recognize the notification as its own echo.
        for notify_method, notify_params in after_send:
            await self.notify(notify_method, notify_params)

    def _session(self, params: JsonObject) -> Path:
        reference = json_as_str(params.get("session")).strip()
        if not reference:
            raise ControlError(-32602, "session is required")
        session = self._resolve_session(reference)
        if session is None or not session.is_dir():
            raise ControlError(404, "session not found", {"session": reference})
        return session

    async def _dispatch(
        self,
        method: str,
        params: JsonObject,
        after_send: list[tuple[str, JsonObject]],
    ) -> JsonValue:
        if method == "initialize":
            requested = json_as_int(params.get("protocolVersion"))
            if requested != PROTOCOL_VERSION:
                raise ControlError(
                    -32602,
                    "unsupported protocol version",
                    {"supported": PROTOCOL_VERSION},
                )
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": list(CAPABILITIES),
                "renderFormats": list(SUPPORTED_FORMATS),
            }
        if method == "session/list":
            if self._list_sessions is None:
                catalog: list[JsonObject] = []
            else:
                # Row building resolves paths (one realpath chain per session);
                # keep those syscalls off the event loop like every other method.
                lister = self._list_sessions
                catalog = await asyncio.to_thread(lambda: list(lister()))
            return filter_session_catalog(
                catalog,
                query=json_as_str(params.get("query")),
                limit=_optional_int_param(params.get("limit"), name="limit"),
            )
        if method == "session/render":
            fmt = json_as_str(params.get("format")).strip().lower() or "org"
            if fmt not in SUPPORTED_FORMATS:
                raise ControlError(
                    -32602,
                    "unsupported editor format",
                    {"supported": list(SUPPORTED_FORMATS), "format": fmt},
                )
            session = self._session(params)

            def _render() -> EditorDocument:
                return render_editor_document(session, format=fmt)

            document = await asyncio.to_thread(_render)
            return {
                "sessionId": document.session_id,
                "notesRevision": document.notes_revision,
                "promptIndexes": list(document.prompt_indexes),
                "format": document.format,
                "contentType": document.content_type,
                "text": document.text,
            }
        if method == "session/open":
            if self._open_session is None:
                raise ControlError(501, "session opening is unavailable")
            raw_prompt = params.get("promptIndex")
            prompt_index = None if raw_prompt is None else json_as_int(raw_prompt)
            session = self._session(params)
            opened = await self._open_session(session, prompt_index)
            if opened:
                after_send.append(
                    (
                        "session/selected",
                        {"sessionId": session.name, "promptIndex": prompt_index},
                    )
                )
            return {"opened": bool(opened)}
        if method == "notes/list":
            snapshot = await asyncio.to_thread(notes_snapshot, self._session(params))
            return _snapshot_mapping(snapshot)
        if method == "notes/upsert":
            note_raw = params.get("note")
            if not isinstance(note_raw, dict):
                raise ControlError(-32602, "note is required")
            session = self._session(params)
            snapshot = await asyncio.to_thread(
                upsert_note,
                session,
                _note_from_params(as_json_object(note_raw)),
                expected_revision=json_as_str(params.get("expectedRevision")),
            )
            result = _snapshot_mapping(snapshot)
            if self._notes_changed is not None:
                await self._notes_changed(session)
            after_send.append(
                ("notes/changed", {"sessionId": session.name, "revision": snapshot.revision})
            )
            return result
        if method == "notes/delete":
            note_id = json_as_str(params.get("noteId")).strip()
            if not note_id:
                raise ControlError(-32602, "noteId is required")
            session = self._session(params)
            snapshot = await asyncio.to_thread(
                delete_note,
                session,
                note_id,
                expected_revision=json_as_str(params.get("expectedRevision")),
            )
            result = _snapshot_mapping(snapshot)
            if self._notes_changed is not None:
                await self._notes_changed(session)
            after_send.append(
                ("notes/changed", {"sessionId": session.name, "revision": snapshot.revision})
            )
            return result
        raise ControlError(-32601, "method not found", {"method": method})

    async def notify(self, method: str, params: JsonObject) -> None:
        """Publish a notification to connected editor clients.

        Each send is time-bounded: a client that stopped reading (full pipe
        buffer) is dropped instead of blocking every later broadcast and the
        callers awaiting them.
        """
        message: JsonObject = {"jsonrpc": "2.0", "method": method, "params": params}
        for writer in list(self._writers):
            try:
                await asyncio.wait_for(
                    self._send(writer, message), timeout=NOTIFY_TIMEOUT_SECONDS
                )
            except (TimeoutError, ConnectionError, OSError):
                self._writers.discard(writer)
                self._writer_framing.pop(writer, None)
                writer.close()

    async def publish_session_changed(self, session_dir: Path) -> None:
        """Notify editor clients that a session projection changed."""
        session = Path(session_dir)
        await self.notify("session/changed", {"sessionId": session.name})

    async def publish_session_selected(
        self,
        session_dir: Path,
        prompt_index: int | None,
    ) -> None:
        """Notify editor clients about the TUI's active session and prompt."""
        session = Path(session_dir)
        await self.notify(
            "session/selected",
            {"sessionId": session.name, "promptIndex": prompt_index},
        )

    async def publish_notes_changed(self, session_dir: Path) -> None:
        """Notify editor clients that canonical operator notes changed."""
        session = Path(session_dir)
        snapshot = await asyncio.to_thread(notes_snapshot, session)
        await self.notify(
            "notes/changed",
            {"sessionId": session.name, "revision": snapshot.revision},
        )

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        request_id: JsonValue | None,
        code: int,
        message: str,
        data: JsonObject | None = None,
    ) -> None:
        error: JsonObject = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self._send(
            writer,
            {"jsonrpc": "2.0", "id": request_id, "error": error},
        )

    async def _send(self, writer: asyncio.StreamWriter, payload: JsonObject) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self._writer_framing.get(writer) == "headers":
            header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
            writer.write(header + encoded)
        else:
            writer.write(encoded + b"\n")
        await writer.drain()
