"""Operator notes schema load/defaults."""

from __future__ import annotations

from pathlib import Path

from groket.notes import (
    default_schema,
    dump_schema_toml,
    load_schema,
    parse_toml,
    sanitize_field_id,
)


def test_default_schema_has_generic_fields() -> None:
    s = default_schema()
    assert s.schema_id == "default"
    ids = [f.id for f in s.fields]
    assert ids == ["summary", "detail"]
    assert "what_model_did" not in ids


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


def test_sanitize_field_id() -> None:
    assert sanitize_field_id("summary") == "summary"
    assert sanitize_field_id("a-b_1") == "a-b_1"
    assert sanitize_field_id("bad.id") is None
    assert sanitize_field_id("") is None


def test_schema_roundtrip_toml() -> None:
    s = default_schema()
    text = dump_schema_toml(s)
    raw = parse_toml(text)
    assert raw["schema_id"] == "default"
    assert isinstance(raw["fields"], list)
    assert raw["fields"][0]["id"] == "summary"
