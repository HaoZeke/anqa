"""Operator notes load/save and export collect."""

from __future__ import annotations

from pathlib import Path

from groket.notes import (
    NOTES_FILENAME,
    NoteEntry,
    NotesDoc,
    collect_notes_for_export,
    load_notes,
    parse_toml,
    save_notes,
)


def test_load_empty(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = load_notes(sd)
    assert doc.notes == []
    assert doc.session_id == "sess"


def test_save_load_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess-a"
    sd.mkdir()
    doc = NotesDoc(schema_id="default", session_id="sess-a")
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


def test_multi_note_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=0, fields={"summary": "a"}, note_id="n-a"))
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "b"}, note_id="n-b"))
    save_notes(sd, doc)
    loaded = load_notes(sd)
    assert {n.id for n in loaded.notes} == {"n-a", "n-b"}
    assert loaded.notes[0].fields["summary"] in ("a", "b")


def test_quoted_field_keys_roundtrip(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    doc = NotesDoc(session_id="sess")
    doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "x", "custom_field": "keep"},
            note_id="n-1",
        )
    )
    save_notes(sd, doc)
    text = (sd / NOTES_FILENAME).read_text(encoding="utf-8")
    assert '"custom_field"' in text or "custom_field" in text
    loaded = load_notes(sd)
    assert loaded.notes[0].fields["custom_field"] == "keep"


def test_unknown_fields_preserved(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    (sd / NOTES_FILENAME).write_text(
        """
schema_id = "custom"
session_id = "sess"

[[notes]]
id = "n-1"
turn_index = 0
event_indices = [1]

[notes.fields]
"summary" = "a"
"extra_custom" = "keep me"
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

    import groket.notes as notes_mod
    import groket.paths as paths_mod

    monkeypatch.setattr(paths_mod, "APP_HOME", tmp_path / "fakehome" / ".groket")
    monkeypatch.setattr(notes_mod, "app_home", lambda: tmp_path / "fakehome" / ".groket")

    def fail_primary(path: Path, text: str) -> None:
        if path.parent == sd:
            raise PermissionError("read-only")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(notes_mod, "_atomic_write", fail_primary)
    path = save_notes(sd, doc)
    assert "notes" in path.parts
    assert path.is_file()
    loaded = load_notes(sd)
    assert len(loaded.notes) == 1


def test_prefer_newer_fallback(tmp_path: Path, monkeypatch) -> None:
    import time

    import groket.notes as notes_mod
    import groket.paths as paths_mod

    home = tmp_path / "home"
    monkeypatch.setattr(paths_mod, "APP_HOME", home)
    monkeypatch.setattr(notes_mod, "app_home", lambda: home)

    sd = tmp_path / "sess"
    sd.mkdir()
    primary = sd / NOTES_FILENAME
    primary.write_text(
        'schema_id = "default"\nsession_id = "sess"\n\n[[notes]]\nid = "n-old"\n'
        'turn_index = 0\n\n[notes.fields]\n"summary" = "old"\n',
        encoding="utf-8",
    )
    fb = home / "notes" / "sess"
    fb.mkdir(parents=True)
    fb_file = fb / NOTES_FILENAME
    time.sleep(0.02)
    fb_file.write_text(
        'schema_id = "default"\nsession_id = "sess"\n\n[[notes]]\nid = "n-new"\n'
        'turn_index = 0\n\n[notes.fields]\n"summary" = "new"\n',
        encoding="utf-8",
    )
    loaded = load_notes(sd)
    assert loaded.notes[0].id == "n-new"
    assert loaded.notes[0].fields["summary"] == "new"


def test_collect_notes_for_export_copies_file(tmp_path: Path) -> None:
    sd = tmp_path / "sess"
    sd.mkdir()
    staging = tmp_path / "staging" / "notes"
    assert collect_notes_for_export(sd, staging) == []

    doc = NotesDoc(session_id="sess")
    doc.upsert(NoteEntry.new(turn_index=1, fields={"summary": "hi"}, note_id="n-1"))
    save_notes(sd, doc)
    written = collect_notes_for_export(sd, staging)
    assert written == [f"notes/{NOTES_FILENAME}"]
    assert (staging / NOTES_FILENAME).is_file()
    assert "hi" in (staging / NOTES_FILENAME).read_text(encoding="utf-8")
    assert not (staging / "schema.toml").exists()


def test_multiline_field_roundtrip_byte_identical(tmp_path: Path) -> None:
    """Multiline values must not gain a trailing newline on dump/load."""
    sd = tmp_path / "sess"
    sd.mkdir()
    no_trailing = "line1\nline2"
    with_trailing = "line1\nline2\n"
    with_escapes = 'said "hello" and path C:\\tmp\\n\nsecond'
    doc = NotesDoc(session_id="sess")
    doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={
                "summary": no_trailing,
                "detail": with_trailing,
                "extra": with_escapes,
            },
            note_id="n-ml",
        )
    )
    save_notes(sd, doc)
    loaded = load_notes(sd)
    fields = loaded.notes[0].fields
    assert fields["summary"] == no_trailing
    assert fields["detail"] == with_trailing
    assert fields["extra"] == with_escapes
    # Double dump/load must stay stable (no progressive trailing NL growth).
    save_notes(sd, loaded)
    again = load_notes(sd).notes[0].fields
    assert again["summary"] == no_trailing
    assert again["detail"] == with_trailing
    assert again["extra"] == with_escapes
