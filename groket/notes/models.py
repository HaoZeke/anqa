"""Types for configurable operator session notes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from ..models import JsonObject, as_json_object


@dataclass(frozen=True)
class FieldSpec:
    """One configurable field in the operator notes schema."""

    id: str
    label: str
    multiline: bool = True
    required: bool = False
    choices: tuple[str, ...] = ()

    def to_dict(self) -> JsonObject:
        d: JsonObject = {
            "id": self.id,
            "label": self.label,
            "multiline": self.multiline,
            "required": self.required,
        }
        if self.choices:
            d["choices"] = list(self.choices)
        return d


@dataclass
class NotesSchema:
    """Field layout for operator notes (loaded from config or defaults)."""

    schema_id: str = "default"
    schema_version: int = 1
    fields: list[FieldSpec] = field(default_factory=list)

    def field_ids(self) -> list[str]:
        return [f.id for f in self.fields]

    def to_dict(self) -> JsonObject:
        return as_json_object(
            {
                "schema_id": self.schema_id,
                "schema_version": self.schema_version,
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
    schema_version: int = 1
    session_id: str = ""
    notes: list[NoteEntry] = field(default_factory=list)

    def by_id(self, note_id: str) -> NoteEntry | None:
        for n in self.notes:
            if n.id == note_id:
                return n
        return None

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
