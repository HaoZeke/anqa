"""Operator notes schema load/defaults."""

from __future__ import annotations

from pathlib import Path

from groket.notes import default_schema, load_schema, schema_from_mapping


def test_default_schema_has_generic_fields() -> None:
    s = default_schema()
    assert s.schema_id == "default"
    assert [f.id for f in s.fields] == ["summary", "detail"]
    assert all(f.label == "" for f in s.fields)


def test_load_schema_missing_uses_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groket.notes.app_home", lambda: tmp_path)
    s = load_schema()
    assert s.schema_id == "default"
    assert [f.id for f in s.fields] == ["summary", "detail"]


def test_load_schema_custom_and_sanitizes(tmp_path: Path) -> None:
    path = tmp_path / "notes_schema.toml"
    path.write_text(
        """
schema_id = "custom"

[[fields]]
id = "what"
label = "What happened"

[[fields]]
id = "bad.id"
label = "skip me"

[[fields]]
id = "what"
label = "duplicate skipped"

[[fields]]
id = "severity"
label = "Severity"
""",
        encoding="utf-8",
    )
    s = load_schema(path=path)
    assert s.schema_id == "custom"
    assert [f.id for f in s.fields] == ["what", "severity"]
    assert s.fields[0].label == "What happened"


def test_schema_from_mapping_empty_fields_uses_default() -> None:
    s = schema_from_mapping({"schema_id": "x", "fields": []})
    assert [f.id for f in s.fields] == ["summary", "detail"]


def test_schema_label_defaults_to_field_id() -> None:
    s = schema_from_mapping(
        {
            "schema_id": "c",
            "fields": [{"id": "severity"}],
        }
    )
    assert s.fields[0].label == "severity"
