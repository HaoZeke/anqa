"""Turn-linked operator notes (session TOML + optional schema).

Schema: ``~/.groket/notes_schema.toml`` (default fields: summary, detail).
Session file: ``<session_dir>/operator_notes.toml``, fallback under
``~/.groket/notes/<session_id>/``. Symlinked session dirs (``import-session
--link``) always write the fallback so host ``~/.grok/sessions`` stays clean.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import JsonObject, as_json_object, json_as_object
from .paths import app_home

logger = logging.getLogger(__name__)

NOTES_FILENAME = "operator_notes.toml"
SCHEMA_FILENAME = "notes_schema.toml"
_FIELD_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class FieldSpec:
    """Schema field. Empty *label* means the TUI uses Fluent ``notes-field-{id}``."""

    id: str
    label: str = ""


@dataclass
class NotesSchema:
    """Field layout for operator notes."""

    schema_id: str = "default"
    fields: list[FieldSpec] = field(default_factory=list)


@dataclass
class NoteEntry:
    """One operator note, usually linked to a turn."""

    id: str
    turn_index: int
    fields: dict[str, str] = field(default_factory=dict)
    event_indices: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(
        cls,
        *,
        turn_index: int,
        fields: dict[str, str] | None = None,
        event_indices: list[int] | None = None,
        note_id: str | None = None,
    ) -> NoteEntry:
        now = datetime.now(UTC).isoformat()
        return cls(
            id=note_id or f"n-{uuid4().hex[:12]}",
            turn_index=int(turn_index),
            fields=dict(fields or {}),
            event_indices=list(event_indices or []),
            created_at=now,
            updated_at=now,
        )


@dataclass
class NotesDoc:
    """All operator notes for one session."""

    schema_id: str = "default"
    session_id: str = ""
    notes: list[NoteEntry] = field(default_factory=list)

    def upsert(self, entry: NoteEntry) -> None:
        for i, n in enumerate(self.notes):
            if n.id == entry.id:
                self.notes[i] = entry
                return
        self.notes.append(entry)

    def remove(self, note_id: str) -> bool:
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.id != note_id]
        return len(self.notes) < before

    def sorted_notes(self) -> list[NoteEntry]:
        return sorted(self.notes, key=lambda n: (n.turn_index, n.created_at, n.id))


_DEFAULT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(id="summary"),
    FieldSpec(id="detail"),
)


def default_schema() -> NotesSchema:
    """Built-in schema (summary + detail; labels via Fluent in the TUI)."""
    return NotesSchema(schema_id="default", fields=list(_DEFAULT_FIELDS))


def notes_schema_path() -> Path:
    """``~/.groket/notes_schema.toml``."""
    return app_home() / SCHEMA_FILENAME


def load_schema(*, path: Path | None = None) -> NotesSchema:
    """Load schema from *path* or config home; defaults when missing/invalid."""
    fp = Path(path) if path is not None else notes_schema_path()
    if not fp.is_file():
        return default_schema()
    try:
        raw = parse_toml(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read notes schema %s: %s", fp, exc)
        return default_schema()
    return schema_from_mapping(raw)


def schema_from_mapping(data: JsonObject) -> NotesSchema:
    """Build :class:`NotesSchema` from a TOML/JSON mapping."""
    schema_id = str(data.get("schema_id") or "default").strip() or "default"
    fields: list[FieldSpec] = []
    seen: set[str] = set()
    raw_fields = data.get("fields")
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id") or "").strip()
            if not fid or not _FIELD_ID_RE.match(fid) or fid in seen:
                continue
            seen.add(fid)
            label = str(item.get("label") or "").strip()
            fields.append(FieldSpec(id=fid, label=label or fid))
    if not fields:
        fields = list(_DEFAULT_FIELDS)
    return NotesSchema(schema_id=schema_id, fields=fields)


def parse_toml(text: str) -> JsonObject:
    """Parse TOML text into a JSON-shaped object."""
    data = tomllib.loads(text or "")
    if not isinstance(data, dict):
        return {}
    return as_json_object(data)


def dump_notes_toml(doc: NotesDoc) -> str:
    """Serialize a notes document to constrained TOML."""
    lines: list[str] = [
        f"schema_id = {_toml_str(doc.schema_id)}",
        f"session_id = {_toml_str(doc.session_id)}",
        "",
    ]
    for note in doc.notes:
        lines.append("[[notes]]")
        lines.append(f"id = {_toml_str(note.id)}")
        lines.append(f"turn_index = {int(note.turn_index)}")
        if note.created_at:
            lines.append(f"created_at = {_toml_str(note.created_at)}")
        if note.updated_at:
            lines.append(f"updated_at = {_toml_str(note.updated_at)}")
        if note.event_indices:
            inner = ", ".join(str(int(i)) for i in note.event_indices)
            lines.append(f"event_indices = [{inner}]")
        if note.fields:
            lines.append("")
            lines.append("[notes.fields]")
            for fk, fv in sorted(note.fields.items()):
                lines.append(f"{_toml_str(str(fk))} = {_toml_str(str(fv))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _toml_str(s: str) -> str:
    if "\n" in s or "\r" in s:
        escaped = s.replace("\\", "\\\\").replace('"""', '\\"""')
        return f'"""\n{escaped}"""'
    return f'"{s.replace("\\", "\\\\").replace('"', '\\"')}"'


def _notes_paths(session_dir: Path) -> tuple[Path, Path]:
    """Primary session path and config-home fallback."""
    session_dir = Path(session_dir)
    primary = session_dir / NOTES_FILENAME
    fallback = app_home() / "notes" / session_dir.name / NOTES_FILENAME
    return primary, fallback


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.is_file() else -1.0
    except OSError:
        return -1.0


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def notes_from_mapping(data: JsonObject) -> NotesDoc:
    """Build :class:`NotesDoc` from a TOML/JSON mapping."""
    schema_id = str(data.get("schema_id") or "default").strip() or "default"
    session_id = str(data.get("session_id") or "").strip()
    notes: list[NoteEntry] = []
    raw_notes = data.get("notes")
    if isinstance(raw_notes, list):
        for item in raw_notes:
            if not isinstance(item, dict):
                continue
            entry = _note_from_mapping(json_as_object(item))
            if entry is not None:
                notes.append(entry)
    return NotesDoc(schema_id=schema_id, session_id=session_id, notes=notes)


def _note_from_mapping(item: JsonObject) -> NoteEntry | None:
    nid = str(item.get("id") or "").strip()
    turn_index = _coerce_int(item.get("turn_index"))
    if not nid or turn_index is None:
        return None
    fields_raw = item.get("fields")
    fields: dict[str, str] = {}
    if isinstance(fields_raw, dict):
        for k, v in fields_raw.items():
            if v is not None:
                fields[str(k)] = str(v)
    event_indices: list[int] = []
    ev_raw = item.get("event_indices")
    if isinstance(ev_raw, list):
        for x in ev_raw:
            n = _coerce_int(x)
            if n is not None:
                event_indices.append(n)
    return NoteEntry(
        id=nid,
        turn_index=turn_index,
        fields=fields,
        event_indices=event_indices,
        created_at=str(item.get("created_at") or ""),
        updated_at=str(item.get("updated_at") or ""),
    )


def _try_load(path: Path, session_id: str) -> NotesDoc | None:
    if not path.is_file():
        return None
    try:
        doc = notes_from_mapping(parse_toml(path.read_text(encoding="utf-8")))
        if not doc.session_id:
            doc.session_id = session_id
        return doc
    except (OSError, ValueError) as exc:
        logger.debug("Failed to load notes from %s: %s", path, exc)
        return None


def load_notes(session_dir: Path) -> NotesDoc:
    """Load notes for *session_dir*; prefer newer of primary vs fallback.

    :param session_dir: Grok session directory.
    :returns: Parsed :class:`NotesDoc` (may be empty).
    """
    session_dir = Path(session_dir)
    sid = session_dir.name
    primary, fallback = _notes_paths(session_dir)
    primary_doc = _try_load(primary, sid)
    fallback_doc = _try_load(fallback, sid)
    if primary_doc is None:
        if fallback_doc is not None:
            return fallback_doc
        return NotesDoc(schema_id=load_schema().schema_id, session_id=sid)
    if fallback_doc is None or _mtime(primary) >= _mtime(fallback):
        return primary_doc
    return fallback_doc


def save_notes(session_dir: Path, doc: NotesDoc) -> Path:
    """Write *doc* beside the session; fall back under ``~/.groket/notes``.

    Symlinked session dirs skip the primary path (linked host Grok sessions).

    :raises OSError: When both primary and fallback writes fail.
    """
    session_dir = Path(session_dir)
    if not doc.session_id:
        doc.session_id = session_dir.name
    text = dump_notes_toml(doc)
    primary, fallback = _notes_paths(session_dir)
    try:
        linked = session_dir.is_symlink()
    except OSError:
        linked = False
    if not linked:
        try:
            _atomic_write(primary, text)
            return primary
        except OSError as exc:
            logger.debug("Primary notes path not writable (%s): %s", primary, exc)
    _atomic_write(fallback, text)
    return fallback


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def collect_notes_for_export(session_dir: Path, staging_notes_dir: Path) -> list[str]:
    """Write loaded notes into *staging_notes_dir* for the export tarball.

    :returns: Relative member paths under ``notes/`` (may be empty).
    """
    doc = load_notes(session_dir)
    if not doc.notes:
        return []
    staging = Path(staging_notes_dir)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / NOTES_FILENAME).write_text(dump_notes_toml(doc), encoding="utf-8")
    return [f"notes/{NOTES_FILENAME}"]
