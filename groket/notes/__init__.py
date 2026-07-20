"""Configurable operator session notes (TOML, turn-linked).

Schema lives in ``~/.groket/notes_schema.toml`` (generic defaults if missing).
Notes live in ``<session_dir>/operator_notes.toml`` with a config-home fallback.
"""

from __future__ import annotations

from .models import FieldSpec, NoteEntry, NotesDoc, NotesSchema
from .schema import (
    SCHEMA_FILENAME,
    default_schema,
    load_schema,
    notes_schema_path,
    schema_to_toml,
    write_default_schema_if_missing,
)
from .store import (
    NOTES_FILENAME,
    collect_notes_for_export,
    load_notes,
    notes_path_in_session,
    save_notes,
)

__all__ = [
    "NOTES_FILENAME",
    "SCHEMA_FILENAME",
    "FieldSpec",
    "NoteEntry",
    "NotesDoc",
    "NotesSchema",
    "collect_notes_for_export",
    "default_schema",
    "load_notes",
    "load_schema",
    "notes_path_in_session",
    "notes_schema_path",
    "save_notes",
    "schema_to_toml",
    "write_default_schema_if_missing",
]
