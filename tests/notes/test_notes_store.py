"""Operator notes load/save and export collect."""

from __future__ import annotations

from pathlib import Path

from groket.notes.models import NoteEntry, NotesDoc
from groket.notes.store import (
    NOTES_FILENAME,
    collect_notes_for_export,
    load_notes,
    save_notes,
)
from groket.notes.toml_io import parse_toml


def test_load_empty(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = load_notes(sd)
    assert doc.notes == []
    assert doc.session_id == "sess"


def test_save_load_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess-a"
    sd.mkdir()
    doc = NotesDoc(schema_id="default", schema_version=1, session_id="sess-a")
    entry = NoteEntry.new(
        turn_index=2,
        fields={"summary": "missed check", "detail": "should have run tests"},
        event_indices=[4, 7],
        note_id="n-test1",
    )
    doc.upsert(entry)
    path = save_notes(sd, doc)
    assert path.name == NOTES_FILENAME
    assert path.is_file()

    loaded = load_notes(sd)
    assert len(loaded.notes) == 1
    n = loaded.notes[0]
    assert n.id == "n-test1"
    assert n.turn_index == 2
    assert n.fields["summary"] == "missed check"
    assert n.event_indices == [4, 7]


def test_unknown_fields_preserved(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    (sd / NOTES_FILENAME).write_text(
        """
schema_id = "custom"
schema_version = 1
session_id = "sess"

[[notes]]
id = "n-1"
turn_index = 0
event_indices = [1]

[notes.fields]
summary = "a"
extra_custom = "keep me"
""",
        encoding="utf-8",
    )
    doc = load_notes(sd)
    assert doc.notes[0].fields["extra_custom"] == "keep me"
    save_notes(sd, doc)
    raw = parse_toml((sd / NOTES_FILENAME).read_text(encoding="utf-8"))
    assert raw["notes"][0]["fields"]["extra_custom"] == "keep me"


def test_upsert_and_remove(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = load_notes(sd)
    a = NoteEntry.new(turn_index=0, fields={"summary": "one"}, note_id="n-a")
    doc.upsert(a)
    a2 = NoteEntry(
        id="n-a",
        turn_index=0,
        fields={"summary": "updated"},
        event_indices=[],
        created_at=a.created_at,
        updated_at="later",
    )
    doc.upsert(a2)
    assert len(doc.notes) == 1
    assert doc.notes[0].fields["summary"] == "updated"
    assert doc.remove("n-a") is True
    assert doc.notes == []


def test_fallback_on_permission_error(tmp_path: Path, monkeypatch) -> None:
    sd = tmp_path / "readonly-sess"
    sd.mkdir()
    doc = NotesDoc(session_id=sd.name)
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "x"}, note_id="n-1"))

    import groket.paths as paths_mod

    monkeypatch.setattr(paths_mod, "APP_HOME", tmp_path / "fakehome" / ".groket")

    original_write = Path.write_text

    def patched_write(self, data, *args, **kwargs):
        if self.name == NOTES_FILENAME and self.parent == sd:
            raise PermissionError("read-only")
        return original_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", patched_write)
    path = save_notes(sd, doc)
    assert "notes" in path.parts
    assert path.is_file()
    # load from fallback
    loaded = load_notes(sd)
    assert len(loaded.notes) == 1


def test_collect_notes_for_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groket.notes.schema.app_home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()
    sd = tmp_path / "sess"
    sd.mkdir()
    staging = tmp_path / "staging" / "notes"
    assert collect_notes_for_export(sd, staging) == []

    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "hi"}, note_id="n-1"))
    save_notes(sd, doc)
    written = collect_notes_for_export(sd, staging)
    assert f"notes/{NOTES_FILENAME}" in written
    assert "notes/schema.toml" in written
    assert (staging / NOTES_FILENAME).is_file()
    assert (staging / "schema.toml").is_file()
