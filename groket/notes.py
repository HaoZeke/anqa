"""Configurable turn-linked operator notes (TOML; flags-shaped leaf module).

Schema: ``~/.groket/notes_schema.toml`` (generic summary/detail defaults).
Session file: ``<session_dir>/operator_notes.toml`` with config-home fallback.
Symlinked session dirs (``import-session --link``) always use the fallback so
notes never write into the live host ``~/.grok/sessions`` tree.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import JsonObject, JsonValue, as_json_object, json_as_object
from .paths import app_home, notes_fallback_dir

logger = logging.getLogger(__name__)

NOTES_FILENAME = "operator_notes.toml"
SCHEMA_FILENAME = "notes_schema.toml"
_FIELD_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


# --- types -----------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """One configurable field (id + label; always multiline in the TUI)."""

    id: str
    label: str

    def to_dict(self) -> JsonObject:
        return as_json_object({"id": self.id, "label": self.label})


@dataclass
class NotesSchema:
    """Field layout for operator notes."""

    schema_id: str = "default"
    fields: list[FieldSpec] = field(default_factory=list)

    def to_dict(self) -> JsonObject:
        return as_json_object(
            {
                "schema_id": self.schema_id,
                "fields": [f.to_dict() for f in self.fields],
            }
        )


@dataclass
class NoteEntry:
    """One operator note, usually linked to a turn."""

    id: str
    turn_index: int
    fields: dict[str, str] = field(default_factory=dict)
    event_indices: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> JsonObject:
        return as_json_object(
            {
                "id": self.id,
                "turn_index": self.turn_index,
                "fields": dict(self.fields),
                "event_indices": list(self.event_indices),
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )

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


# --- schema ----------------------------------------------------------------


_DEFAULT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(id="summary", label="Summary"),
    FieldSpec(id="detail", label="Detail"),
)


def default_schema() -> NotesSchema:
    """Built-in generic schema (summary + detail)."""
    return NotesSchema(schema_id="default", fields=list(_DEFAULT_FIELDS))


def notes_schema_path() -> Path:
    """``~/.groket/notes_schema.toml``."""
    return app_home() / SCHEMA_FILENAME


def sanitize_field_id(raw: str) -> str | None:
    """Return a safe field id or None if *raw* cannot be used."""
    fid = str(raw or "").strip()
    if not fid or not _FIELD_ID_RE.match(fid):
        return None
    return fid


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
            fid = sanitize_field_id(str(item.get("id") or ""))
            if fid is None or fid in seen:
                continue
            seen.add(fid)
            label = str(item.get("label") or fid).strip() or fid
            fields.append(FieldSpec(id=fid, label=label))
    if not fields:
        fields = list(_DEFAULT_FIELDS)
    return NotesSchema(schema_id=schema_id, fields=fields)


# --- TOML I/O (constrained; stdlib reader + tiny writer) --------------------


def parse_toml(text: str) -> JsonObject:
    """Parse TOML text into a JSON-shaped object."""
    data = tomllib.loads(text or "")
    if not isinstance(data, dict):
        return {}
    converted = _to_json(data)
    if not isinstance(converted, dict):
        return {}
    return as_json_object(converted)


def _to_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json(v) for v in value]
    return str(value)


def dump_notes_toml(doc: NotesDoc) -> str:
    """Serialize a notes document to constrained TOML."""
    lines: list[str] = [
        f"schema_id = {_format_string(doc.schema_id)}",
        f"session_id = {_format_string(doc.session_id)}",
        "",
    ]
    for note in doc.notes:
        lines.append("[[notes]]")
        lines.append(f"id = {_format_string(note.id)}")
        lines.append(f"turn_index = {int(note.turn_index)}")
        if note.created_at:
            lines.append(f"created_at = {_format_string(note.created_at)}")
        if note.updated_at:
            lines.append(f"updated_at = {_format_string(note.updated_at)}")
        if note.event_indices:
            inner = ", ".join(str(int(i)) for i in note.event_indices)
            lines.append(f"event_indices = [{inner}]")
        if note.fields:
            lines.append("")
            lines.append("[notes.fields]")
            for fk, fv in sorted(note.fields.items(), key=lambda kv: kv[0]):
                # Always quote keys so custom field ids stay valid TOML.
                lines.append(f"{_format_string(str(fk))} = {_format_string(str(fv))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def dump_schema_toml(schema: NotesSchema) -> str:
    """Serialize a schema to constrained TOML (examples / optional)."""
    lines = [f"schema_id = {_format_string(schema.schema_id)}", ""]
    for spec in schema.fields:
        lines.append("[[fields]]")
        lines.append(f"id = {_format_string(spec.id)}")
        lines.append(f"label = {_format_string(spec.label)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_string(s: str) -> str:
    if "\n" in s or "\r" in s:
        escaped = s.replace("\\", "\\\\").replace('"""', '\\"""')
        # Opening """\n is trimmed by TOML; do not force a trailing NL before """.
        return f'"""\n{escaped}"""'
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# --- load / save -----------------------------------------------------------


def notes_path_in_session(session_dir: Path) -> Path:
    """Primary path: ``<session_dir>/operator_notes.toml``."""
    return Path(session_dir) / NOTES_FILENAME


def _fallback_notes_path(session_id: str) -> Path:
    """Fallback path without creating dirs (mkdir only on save)."""
    return app_home() / "notes" / session_id / NOTES_FILENAME


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
            entry = note_from_mapping(json_as_object(item))
            if entry is not None:
                notes.append(entry)
    return NotesDoc(schema_id=schema_id, session_id=session_id, notes=notes)


def note_from_mapping(item: JsonObject) -> NoteEntry | None:
    """Parse one note table; None when id/turn_index invalid."""
    nid = str(item.get("id") or "").strip()
    if not nid:
        return None
    raw_turn = item.get("turn_index")
    if raw_turn is None:
        return None
    try:
        turn_index = int(raw_turn)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    fields_raw = item.get("fields") or {}
    fields: dict[str, str] = {}
    if isinstance(fields_raw, dict):
        for k, v in fields_raw.items():
            if v is None:
                continue
            fields[str(k)] = str(v)
    ev_raw = item.get("event_indices") or []
    event_indices: list[int] = []
    if isinstance(ev_raw, list):
        for x in ev_raw:
            try:
                event_indices.append(int(x))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
    return NoteEntry(
        id=nid,
        turn_index=turn_index,
        fields=fields,
        event_indices=event_indices,
        created_at=str(item.get("created_at") or ""),
        updated_at=str(item.get("updated_at") or ""),
    )


def _try_load_path(path: Path, session_id: str) -> NotesDoc | None:
    if not path.is_file():
        return None
    try:
        raw = parse_toml(path.read_text(encoding="utf-8"))
        doc = notes_from_mapping(raw)
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
    primary = notes_path_in_session(session_dir)
    fallback = _fallback_notes_path(sid)

    primary_doc = _try_load_path(primary, sid)
    fallback_doc = _try_load_path(fallback, sid)

    if primary_doc is None and fallback_doc is None:
        schema = load_schema()
        return NotesDoc(schema_id=schema.schema_id, session_id=sid, notes=[])
    if primary_doc is None:
        assert fallback_doc is not None
        return fallback_doc
    if fallback_doc is None:
        return primary_doc

    # Both exist: prefer the newer file by mtime (handles RO session after fallback write).
    try:
        p_m = primary.stat().st_mtime
    except OSError:
        p_m = 0.0
    try:
        f_m = fallback.stat().st_mtime
    except OSError:
        f_m = 0.0
    if f_m > p_m:
        return fallback_doc
    return primary_doc


def _session_dir_is_link(session_dir: Path) -> bool:
    """True when *session_dir* is a symlink (e.g. ``import-session --link``).

    Linked imports point at live ``~/.grok/sessions`` trees. Groket must not
    write private files into that host session; use the config-home fallback.
    """
    try:
        return Path(session_dir).is_symlink()
    except OSError:
        return False


def save_notes(session_dir: Path, doc: NotesDoc) -> Path:
    """Write *doc* beside the session; fall back under ``~/.groket/notes``.

    Uses temp + replace when possible. On primary failure, writes fallback and
    does not leave success implied by the caller without catching OSError.

    When *session_dir* is a symlink (linked import of a host Grok session),
    skips the primary path so notes never land inside the live ``~/.grok``
    tree — same isolation as import meta for ``--link``.

    :raises OSError: When both primary and fallback writes fail.
    """
    session_dir = Path(session_dir)
    if not doc.session_id:
        doc.session_id = session_dir.name
    text = dump_notes_toml(doc)
    primary = notes_path_in_session(session_dir)
    if not _session_dir_is_link(session_dir):
        try:
            _atomic_write(primary, text)
            return primary
        except OSError:
            pass
    fallback = notes_fallback_dir(session_dir.name) / NOTES_FILENAME
    try:
        _atomic_write(fallback, text)
        return fallback
    except OSError:
        logger.exception("Failed to save operator notes for %s", session_dir.name)
        raise


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def collect_notes_for_export(session_dir: Path, staging_notes_dir: Path) -> list[str]:
    """Copy on-disk notes file into *staging_notes_dir* for export.

    Prefers the same path resolution as :func:`load_notes` (newer of primary /
    fallback). Copies bytes as-is when a file exists and has notes; no schema
    snapshot (field keys already live in the notes file).

    :returns: Relative member paths under ``notes/`` (may be empty).
    """
    session_dir = Path(session_dir)
    doc = load_notes(session_dir)
    if not doc.notes:
        return []

    sid = session_dir.name
    primary = notes_path_in_session(session_dir)
    fallback = _fallback_notes_path(sid)
    src: Path | None = None
    if primary.is_file() and fallback.is_file():
        try:
            src = fallback if fallback.stat().st_mtime > primary.stat().st_mtime else primary
        except OSError:
            src = primary if primary.is_file() else fallback
    elif primary.is_file():
        src = primary
    elif fallback.is_file():
        src = fallback

    staging_notes_dir = Path(staging_notes_dir)
    staging_notes_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_notes_dir / NOTES_FILENAME
    if src is not None and src.is_file():
        dest.write_bytes(src.read_bytes())
    else:
        # In-memory only (e.g. tests that never flushed a preferred path).
        dest.write_text(dump_notes_toml(doc), encoding="utf-8")
    return [f"notes/{NOTES_FILENAME}"]
