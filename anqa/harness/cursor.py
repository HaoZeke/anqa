"""Cursor disk adapter (``~/.cursor/projects/*/agent-transcripts``).

One jsonl file is one conversation. Catalog path is ``cursor:<session_id>``.
List metadata comes from ``~/.cursor/chats/*/<id>/meta.json``.
"""

from __future__ import annotations

import json
import tarfile
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .. import event_types as et
from ..models import JsonObject, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from ..session.tagged_blocks import operator_prompt_text
from .ref import SessionRef

CURSOR_HARNESS_ID = "cursor"
_CANCELLED = frozenset({"cancelled", "canceled", "aborted", "interrupted", "error"})


def default_store_root() -> Path:
    """Host Cursor config tree (resolved at call time)."""
    return Path.home() / ".cursor"


def _as_object(raw: object) -> JsonObject:
    if isinstance(raw, dict):
        return as_json_object(raw)
    return {}


def _iso_ms(raw: object) -> str:
    if isinstance(raw, bool):
        return ""
    if isinstance(raw, (int, float)) and raw > 0:
        val = float(raw)
        sec = val / 1000.0 if val > 1e12 else val
        return datetime.fromtimestamp(sec, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ""


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


def _load_json(path: Path) -> JsonObject:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _as_object(raw)


def _is_transcript(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".jsonl":
        return False
    return path.parent.parent.name == "agent-transcripts"


def _collect_jsonl(roots: Sequence[Path]) -> list[Path]:
    out: list[Path] = []
    for raw in roots:
        path = Path(raw).expanduser()
        if _is_transcript(path):
            out.append(path)
            continue
        if not path.is_dir():
            continue
        out.extend(sorted(path.rglob("agent-transcripts/*/*.jsonl")))
    return out


def _find_meta(root: Path, sid: str) -> JsonObject:
    chats = root / "chats"
    if chats.is_dir():
        for path in chats.glob(f"*/{sid}/meta.json"):
            return _load_json(path)
    if root.name == sid and (root / "meta.json").is_file():
        return _load_json(root / "meta.json")
    for path in root.rglob(f"{sid}/meta.json"):
        return _load_json(path)
    return {}


def _blocks_text(raw: object) -> str:
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, list):
        return ""
    bits: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            bits.append(text)
    return "\n".join(bits)


def _session_title(header: JsonObject, rows: Sequence[JsonObject]) -> str:
    prompt = _first_user_title(rows)
    if prompt:
        return prompt
    raw = operator_prompt_text(str(header.get("title") or "").strip())
    return raw.splitlines()[0][:120] if raw else ""


def _first_user_title(rows: Sequence[JsonObject]) -> str:
    for row in rows:
        if str(row.get("role") or "") != "user":
            continue
        text = operator_prompt_text(_blocks_text(_as_object(row.get("message")).get("content")))
        if text:
            return text.splitlines()[0][:120]
    return ""


def _count_tools(rows: Sequence[JsonObject]) -> int:
    n = 0
    for row in rows:
        content = _as_object(row.get("message")).get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "") == "tool_use":
                n += 1
    return n


def _last_signal(rows: Sequence[JsonObject]) -> tuple[str, str]:
    last_type = ""
    last_status = ""
    last_role = ""
    for row in rows:
        typ = str(row.get("type") or "").strip()
        if typ == "turn_ended":
            last_type = typ
            last_status = str(row.get("status") or "").strip()
            last_role = ""
            continue
        role = str(row.get("role") or "").strip()
        if role in {"user", "assistant"}:
            last_role = role
            last_type = ""
            last_status = ""
    return last_type, last_status or last_role


def _turn_outcome(rows: Sequence[JsonObject]) -> str:
    kind, status = _last_signal(rows)
    if kind == "turn_ended":
        if status.casefold() in _CANCELLED:
            return "cancelled"
        return "complete"
    if status in {"user", "assistant"}:
        return "running"
    return ""


def _from_message(index: int, row: JsonObject) -> list[TraceEvent]:
    role = str(row.get("role") or "")
    content = _as_object(row.get("message")).get("content")
    events: list[TraceEvent] = []
    if role == "user":
        text = operator_prompt_text(_blocks_text(content))
        events.append(
            TraceEvent(
                index=index,
                event_type=et.USER_MESSAGE_CHUNK,
                content=text,
                update_index=index,
            )
        )
        return events
    if role != "assistant":
        return events
    text = _blocks_text(content)
    if text:
        events.append(
            TraceEvent(
                index=index,
                event_type=et.AGENT_MESSAGE_CHUNK,
                content=text,
                update_index=index,
            )
        )
    if not isinstance(content, list):
        return events
    for part in content:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") != "tool_use":
            continue
        name = str(part.get("name") or "").strip()
        raw = part.get("input")
        bag = _as_object(raw) if isinstance(raw, dict) else {}
        events.append(
            TraceEvent(
                index=index,
                event_type=et.TOOL_CALL,
                tool_name=name,
                tool_call_id=str(part.get("id") or ""),
                raw_input=ToolInputBag(bag),
                update_index=index,
            )
        )
    return events


def _from_row(index: int, row: JsonObject) -> list[TraceEvent]:
    typ = str(row.get("type") or "")
    if typ == "turn_ended":
        status = str(row.get("status") or "").strip()
        ended = status.casefold() in _CANCELLED
        return [
            TraceEvent(
                index=index,
                event_type=et.TURN_ENDED if ended else et.TURN_COMPLETED,
                content=status,
                update_index=index,
            )
        ]
    if str(row.get("role") or ""):
        return _from_message(index, row)
    return []


def _timeline_for(rows: Sequence[JsonObject]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for i, row in enumerate(rows):
        events.extend(_from_row(i, row))
    return events


def _meta_from(rows: Sequence[JsonObject], path: Path, sid: str, header: JsonObject) -> SessionMeta:
    created = _iso_ms(header.get("createdAtMs"))
    updated = _iso_ms(header.get("updatedAtMs")) or created
    start = None
    end = None
    created_raw = header.get("createdAtMs")
    updated_raw = header.get("updatedAtMs")
    if isinstance(created_raw, (int, float)) and not isinstance(created_raw, bool):
        start = float(created_raw)
        if start > 1e12:
            start /= 1000.0
    if isinstance(updated_raw, (int, float)) and not isinstance(updated_raw, bool):
        end = float(updated_raw)
        if end > 1e12:
            end /= 1000.0
    duration = float(max(0, (end or 0) - (start or 0))) if start and end else 0.0
    return SessionMeta(
        session_id=sid,
        session_dir=path,
        model_id="unknown",
        title=_session_title(header, rows),
        created_at=created,
        updated_at=updated,
        duration_seconds=duration,
        run_dir=str(header.get("cwd") or "").strip(),
        num_events=len(_timeline_for(rows)),
        tool_call_count=_count_tools(rows),
        turn_outcome=_turn_outcome(rows),
        harness=CURSOR_HARNESS_ID,
    )


def _ref_for_file(path: Path, cwd: str = "") -> SessionRef | None:
    if not _is_transcript(path):
        return None
    sid = path.stem.strip()
    if not sid:
        return None
    return SessionRef(
        harness=CURSOR_HARNESS_ID,
        session_id=sid,
        locator=path,
        cwd=cwd,
    )


def _jsonl_from_ref(ref: SessionRef | Path | str, root: Path) -> tuple[Path, str]:
    if isinstance(ref, SessionRef):
        return Path(ref.locator), ref.session_id
    from .ref import parse_session_ref_string

    parsed = parse_session_ref_string(str(ref))
    if parsed is not None:
        found = _find_file(root, parsed[1])
        return (found or Path(), parsed[1])
    path = Path(str(ref)).expanduser()
    if _is_transcript(path):
        return path, path.stem
    return Path(), path.name


def _find_file(root: Path, session_id: str) -> Path | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    for path in _collect_jsonl([root]):
        if path.stem == sid:
            return path
    return None


class CursorAdapter:
    """Read-only Cursor agent-transcript adapter."""

    id = CURSOR_HARNESS_ID
    product = "Cursor"
    supported_version = "2026.08.25-3e8eec8"

    def root(self) -> Path:
        return default_store_root()

    def default_host_roots(self) -> list[Path]:
        path = self.root()
        return [path] if path.is_dir() else []

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        scan = [self.root()] if roots is None else [Path(r) for r in roots]
        found: list[SessionRef] = []
        seen: set[str] = set()
        for file in _collect_jsonl(scan):
            header = _find_meta(self.root() if roots is None else scan[0], file.stem)
            ref = _ref_for_file(file, cwd=str(header.get("cwd") or "").strip())
            if ref is None or ref.session_id in seen:
                continue
            seen.add(ref.session_id)
            found.append(ref)
        return found

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == CURSOR_HARNESS_ID
        from .ref import parse_session_ref_string

        parsed = parse_session_ref_string(str(ref))
        if parsed is not None:
            return parsed[0] == CURSOR_HARNESS_ID
        return _is_transcript(Path(str(ref)).expanduser())

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not self.looks_like(path):
            return None
        return _ref_for_file(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"cursor session not found: {sid}")
        rows = list(_iter_rows(path))
        header = _find_meta(self.root(), sid)
        return _meta_from(rows, path, sid, header)

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
        return (".jsonl", "meta.json")

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        path, sid = _jsonl_from_ref(ref, self.root())
        if not path.is_file():
            raise FileNotFoundError(f"cursor session not found: {sid}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        packed = False
        members = [f"{sid}/{path.name}"]
        extras: list[tuple[Path, str]] = []
        for meta in self.root().joinpath("chats").glob(f"*/{sid}/meta.json"):
            extras.append((meta, f"{sid}/meta.json"))
            break
        try:
            with tarfile.open(tmp, "w:gz") as tf:
                tf.add(path, arcname=members[0])
                for extra, name in extras:
                    tf.add(extra, arcname=name)
                    members.append(name)
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
    "CURSOR_HARNESS_ID",
    "CursorAdapter",
    "default_store_root",
]
