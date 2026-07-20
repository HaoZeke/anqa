"""Read/write a constrained TOML subset for operator notes (stdlib only)."""

from __future__ import annotations

import re
import tomllib

from ..models import JsonObject, JsonValue, as_json_object, json_as_object


def parse_toml(text: str) -> JsonObject:
    """Parse TOML text into a JSON-shaped object.

    :param text: TOML source.
    :returns: Top-level mapping (empty on blank input).
    :raises tomllib.TOMLDecodeError: Invalid TOML.
    """
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


def dump_toml(data: JsonObject | dict[str, JsonValue]) -> str:
    """Serialize a constrained notes document or schema to TOML.

    Supports top-level scalars, array-of-tables under ``fields`` / ``notes``,
    string maps under ``fields`` on each note, and integer lists.

    :param data: Document mapping.
    :returns: TOML text ending with a newline.
    """
    lines: list[str] = []
    # Scalars first
    for key in ("schema_id", "schema_version", "session_id"):
        if key not in data:
            continue
        lines.append(f"{key} = {_format_value(data[key])}")
    if lines:
        lines.append("")

    fields = data.get("fields")
    if isinstance(fields, list):
        for item in fields:
            if not isinstance(item, dict):
                continue
            lines.append("[[fields]]")
            for fk in ("id", "label", "multiline", "required"):
                if fk in item:
                    lines.append(f"{fk} = {_format_value(item[fk])}")
            choices = item.get("choices")
            if isinstance(choices, list) and choices:
                lines.append(f"choices = {_format_value(choices)}")
            lines.append("")

    notes = data.get("notes")
    if isinstance(notes, list):
        for item in notes:
            if not isinstance(item, dict):
                continue
            note = json_as_object(item)
            lines.append("[[notes]]")
            for nk in ("id", "turn_index", "created_at", "updated_at"):
                if nk in note:
                    lines.append(f"{nk} = {_format_value(note[nk])}")
            ev = note.get("event_indices")
            if isinstance(ev, list):
                lines.append(f"event_indices = {_format_value(ev)}")
            fmap = note.get("fields")
            if isinstance(fmap, dict) and fmap:
                # Nested table for string field map (attaches to this [[notes]]).
                lines.append("")
                lines.append("[notes.fields]")
                for fk, fv in sorted(fmap.items(), key=lambda kv: str(kv[0])):
                    lines.append(f"{fk} = {_format_value(fv)}")
            lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    return body


_NEEDS_BASIC = re.compile(r'[\n\r"]|[\\]')


def _format_value(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _format_string(value)
    if isinstance(value, list):
        inner = ", ".join(_format_value(v) for v in value)
        return f"[{inner}]"
    if value is None:
        return '""'
    return _format_string(str(value))


def _format_string(s: str) -> str:
    if "\n" in s or "\r" in s:
        # Multi-line basic string with escaped quotes.
        escaped = s.replace("\\", "\\\\").replace('"""', '\\"""')
        return f'"""\n{escaped}\n"""'
    if _NEEDS_BASIC.search(s) or s == "":
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    # Prefer quoted always for safety with bare keys.
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
