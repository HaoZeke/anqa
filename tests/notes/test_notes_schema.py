"""Operator notes schema load/defaults."""

from __future__ import annotations

from pathlib import Path

from groket.notes.schema import (
    default_schema,
    load_schema,
    schema_to_toml,
    write_default_schema_if_missing,
)
from groket.notes.toml_io import parse_toml


def test_default_schema_has_generic_fields() -> None:
    s = default_schema()
    assert s.schema_id == "default"
    ids = s.field_ids()
    assert "summary" in ids
    assert "detail" in ids
    # No program-specific field ids in defaults.
    assert "what_model_did" not in ids


def test_load_schema_missing_uses_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("groket.notes.schema.app_home", lambda: tmp_path)
    s = load_schema()
    assert s.schema_id == "default"
    assert s.field_ids() == ["summary", "detail"]


def test_load_schema_custom(tmp_path: Path) -> None:
    path = tmp_path / "notes_schema.toml"
    path.write_text(
        """
schema_id = "custom"
schema_version = 2

[[fields]]
id = "what"
label = "What happened"
multiline = true
required = true

[[fields]]
id = "severity"
label = "Severity"
multiline = false
choices = ["major", "minor"]
""",
        encoding="utf-8",
    )
    s = load_schema(path=path)
    assert s.schema_id == "custom"
    assert s.schema_version == 2
    assert s.fields[0].id == "what"
    assert s.fields[0].required is True
    assert s.fields[1].choices == ("major", "minor")


def test_schema_roundtrip_toml() -> None:
    s = default_schema()
    text = schema_to_toml(s)
    raw = parse_toml(text)
    assert raw["schema_id"] == "default"
    assert isinstance(raw["fields"], list)
    assert raw["fields"][0]["id"] == "summary"


def test_write_default_schema_if_missing(tmp_path: Path) -> None:
    path = tmp_path / "notes_schema.toml"
    written = write_default_schema_if_missing(path=path)
    assert written == path
    assert path.is_file()
    again = write_default_schema_if_missing(path=path)
    assert again == path
