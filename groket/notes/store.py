"""Load and save operator notes next to a session (with config-home fallback)."""

from __future__ import annotations

import logging
from pathlib import Path

from ..paths import notes_fallback_dir
from .models import NoteEntry, NotesDoc
from .schema import load_schema
from .toml_io import dump_toml, parse_toml

logger = logging.getLogger(__name__)

NOTES_FILENAME = "operator_notes.toml"


def notes_path_in_session(session_dir: Path) -> Path:
    """Primary path: ``<session_dir>/operator_notes.toml``."""
    return Path(session_dir) / NOTES_FILENAME


def notes_candidate_paths(session_dir: Path) -> list[Path]:
    """Session file first, then config-home fallback."""
    sid = Path(session_dir).name
    return [
        notes_path_in_session(session_dir),
        notes_fallback_dir(sid) / NOTES_FILENAME,
    ]


def load_notes(session_dir: Path) -> NotesDoc:
    """Load notes for *session_dir*; empty document when none.

    :param session_dir: Grok session directory.
    :returns: Parsed :class:`NotesDoc` (may be empty).
    """
    session_dir = Path(session_dir)
    for path in notes_candidate_paths(session_dir):
        if not path.is_file():
            continue
        try:
            raw = parse_toml(path.read_text(encoding="utf-8"))
            doc = notes_from_dict(raw)
            if not doc.session_id:
                doc.session_id = session_dir.name
            return doc
        except (OSError, ValueError) as exc:
            logger.debug("Failed to load notes from %s: %s", path, exc)
            continue
    schema = load_schema()
    return NotesDoc(
        schema_id=schema.schema_id,
        schema_version=schema.schema_version,
        session_id=session_dir.name,
        notes=[],
    )


def notes_from_dict(data: dict) -> NotesDoc:
    """Build :class:`NotesDoc` from a TOML/JSON mapping."""
    schema_id = str(data.get("schema_id") or "default").strip() or "default"
    try:
        version = int(data.get("schema_version") or 1)
    except (TypeError, ValueError):
        version = 1
    session_id = str(data.get("session_id") or "").strip()
    notes: list[NoteEntry] = []
    raw_notes = data.get("notes")
    if isinstance(raw_notes, list):
        for item in raw_notes:
            if not isinstance(item, dict):
                continue
            entry = note_from_dict(item)
            if entry is not None:
                notes.append(entry)
    return NotesDoc(
        schema_id=schema_id,
        schema_version=version,
        session_id=session_id,
        notes=notes,
    )


def note_from_dict(item: dict) -> NoteEntry | None:
    """Parse one note table; returns None when id/turn_index invalid."""
    nid = str(item.get("id") or "").strip()
    if not nid:
        return None
    raw_turn = item.get("turn_index")
    if raw_turn is None:
        return None
    try:
        turn_index = int(raw_turn)
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
                event_indices.append(int(x))
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


def notes_to_dict(doc: NotesDoc) -> dict:
    """Serialize *doc* for TOML dump."""
    return {
        "schema_id": doc.schema_id,
        "schema_version": doc.schema_version,
        "session_id": doc.session_id,
        "notes": [n.to_dict() for n in doc.notes],
    }


def save_notes(session_dir: Path, doc: NotesDoc) -> Path:
    """Write *doc* beside the session; fall back under ``~/.groket/notes``.

    :param session_dir: Session directory.
    :param doc: Document to persist.
    :returns: Path written.
    :raises OSError: When both primary and fallback writes fail.
    """
    session_dir = Path(session_dir)
    if not doc.session_id:
        doc.session_id = session_dir.name
    text = dump_toml(notes_to_dict(doc))
    primary = notes_path_in_session(session_dir)
    try:
        primary.write_text(text, encoding="utf-8")
        return primary
    except OSError:
        fallback = notes_fallback_dir(session_dir.name) / NOTES_FILENAME
        try:
            fallback.write_text(text, encoding="utf-8")
            return fallback
        except OSError:
            logger.exception("Failed to save operator notes for %s", session_dir.name)
            raise


def collect_notes_for_export(session_dir: Path, staging_notes_dir: Path) -> list[str]:
    """Copy notes + schema snapshot into *staging_notes_dir* for export.

    Writes only when the session has at least one note. Includes a snapshot of
    the active field schema so the tarball is self-describing.

    :returns: Relative member paths under the ``notes/`` prefix (may be empty).
    """
    from .schema import load_schema, schema_to_toml

    doc = load_notes(session_dir)
    if not doc.notes:
        return []

    staging_notes_dir = Path(staging_notes_dir)
    staging_notes_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    notes_file = staging_notes_dir / NOTES_FILENAME
    notes_file.write_text(dump_toml(notes_to_dict(doc)), encoding="utf-8")
    written.append(f"notes/{NOTES_FILENAME}")

    schema = load_schema()
    schema_path = staging_notes_dir / "schema.toml"
    schema_path.write_text(schema_to_toml(schema), encoding="utf-8")
    written.append("notes/schema.toml")
    return written
