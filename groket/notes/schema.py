"""Load operator notes schema from config home or built-in defaults."""

from __future__ import annotations

import logging
from pathlib import Path

from ..paths import app_home
from .models import FieldSpec, NotesSchema
from .toml_io import dump_toml, parse_toml

logger = logging.getLogger(__name__)

SCHEMA_FILENAME = "notes_schema.toml"

# Generic defaults only — no program-specific field ids or labels.
_DEFAULT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(id="summary", label="Summary", multiline=True, required=False),
    FieldSpec(id="detail", label="Detail", multiline=True, required=False),
)


def default_schema() -> NotesSchema:
    """Built-in generic schema (summary + detail)."""
    return NotesSchema(
        schema_id="default",
        schema_version=1,
        fields=list(_DEFAULT_FIELDS),
    )


def notes_schema_path() -> Path:
    """``~/.groket/notes_schema.toml``."""
    return app_home() / SCHEMA_FILENAME


def load_schema(*, path: Path | None = None) -> NotesSchema:
    """Load schema from *path* or config home; fall back to :func:`default_schema`.

    :param path: Explicit schema file (tests); default :func:`notes_schema_path`.
    :returns: Parsed schema or defaults when missing/invalid.
    """
    fp = Path(path) if path is not None else notes_schema_path()
    if not fp.is_file():
        return default_schema()
    try:
        raw = parse_toml(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read notes schema %s: %s", fp, exc)
        return default_schema()
    return schema_from_dict(raw)


def schema_from_dict(data: dict) -> NotesSchema:
    """Build :class:`NotesSchema` from a TOML/JSON mapping."""
    schema_id = str(data.get("schema_id") or "default").strip() or "default"
    try:
        version = int(data.get("schema_version") or 1)
    except (TypeError, ValueError):
        version = 1
    fields: list[FieldSpec] = []
    raw_fields = data.get("fields")
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id") or "").strip()
            if not fid:
                continue
            label = str(item.get("label") or fid).strip() or fid
            multiline = bool(item.get("multiline", True))
            required = bool(item.get("required", False))
            choices_raw = item.get("choices") or []
            choices: tuple[str, ...] = ()
            if isinstance(choices_raw, list):
                choices = tuple(str(c) for c in choices_raw if str(c).strip())
            fields.append(
                FieldSpec(
                    id=fid,
                    label=label,
                    multiline=multiline,
                    required=required,
                    choices=choices,
                )
            )
    if not fields:
        fields = list(_DEFAULT_FIELDS)
    return NotesSchema(schema_id=schema_id, schema_version=version, fields=fields)


def schema_to_toml(schema: NotesSchema) -> str:
    """Serialize *schema* to TOML text."""
    return dump_toml(schema.to_dict())


def write_default_schema_if_missing(*, path: Path | None = None) -> Path:
    """Write the built-in default schema when the file does not exist.

    :returns: Path written or already present.
    """
    fp = Path(path) if path is not None else notes_schema_path()
    if fp.is_file():
        return fp
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(schema_to_toml(default_schema()), encoding="utf-8")
    return fp
