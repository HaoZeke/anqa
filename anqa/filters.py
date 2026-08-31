"""Named saved filters (``~/.anqa/filters.toml``).

A filter is a name, a scope (catalog / timeline / turns), and a query in
the existing search language. ``field:{a,b}`` is a choice hole;
``field:?`` is a free-text hole. The apps collect answers, then
:func:`expand` writes a normal query.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .models import JsonObject, as_json_object, json_as_str
from .paths import app_home

logger = logging.getLogger(__name__)

FILTERS_FILENAME = "filters.toml"
SCOPES = frozenset({"catalog", "timeline", "turns"})
HoleKind = Literal["choice", "text"]

_CHOICE = re.compile(r"(?i)\b([A-Za-z][A-Za-z0-9_]*):\{([^}]*)\}")
_TEXT = re.compile(r"(?i)\b([A-Za-z][A-Za-z0-9_]*):\?")
_NAME_MAX = 80


@dataclass(frozen=True)
class FilterHole:
    """One operator hole in a saved query."""

    field: str
    kind: HoleKind
    choices: tuple[str, ...] = ()

    def mapping(self) -> JsonObject:
        """Control / list payload for this hole."""
        return as_json_object(
            {
                "field": self.field,
                "kind": self.kind,
                "choices": list(self.choices),
            }
        )


@dataclass(frozen=True)
class SavedFilter:
    """One named query for a search box."""

    name: str
    scope: str
    query: str

    def mapping(self) -> JsonObject:
        """Control / list payload, including holes in the query."""
        return as_json_object(
            {
                "name": self.name,
                "scope": self.scope,
                "query": self.query,
                "holes": [hole.mapping() for hole in holes(self.query)],
            }
        )


def filters_path() -> Path:
    """``~/.anqa/filters.toml``."""
    return app_home() / FILTERS_FILENAME


def holes(query: str) -> list[FilterHole]:
    """Choice and text holes in left-to-right order (each field once)."""
    text = query or ""
    found: list[FilterHole] = []
    seen: set[str] = set()
    for match in _CHOICE.finditer(text):
        field = match.group(1).casefold()
        opts = tuple(part.strip() for part in match.group(2).split(",") if part.strip())
        if not opts or field in seen:
            continue
        seen.add(field)
        found.append(FilterHole(field, "choice", opts))
    for match in _TEXT.finditer(text):
        field = match.group(1).casefold()
        if field in seen:
            continue
        seen.add(field)
        found.append(FilterHole(field, "text"))
    # Keep document order: choice scan then text scan is wrong if mixed.
    return _holes_in_order(text, found)


def _holes_in_order(text: str, found: list[FilterHole]) -> list[FilterHole]:
    by_field = {h.field: h for h in found}
    order: list[FilterHole] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?i)\b([A-Za-z][A-Za-z0-9_]*)(?::\{[^}]*\}|:\?)",
        text,
    ):
        field = match.group(1).casefold()
        hole = by_field.get(field)
        if hole is None or field in seen:
            continue
        seen.add(field)
        order.append(hole)
    return order


def expand(query: str, answers: Mapping[str, str]) -> str:
    """Replace holes with *answers* (keyed by field)."""

    def _choice(match: re.Match[str]) -> str:
        field = match.group(1)
        return _filled(field, answers)

    def _text(match: re.Match[str]) -> str:
        field = match.group(1)
        return _filled(field, answers)

    out = _CHOICE.sub(_choice, query or "")
    return _TEXT.sub(_text, out)


def _filled(field: str, answers: Mapping[str, str]) -> str:
    key = field.casefold()
    raw = answers.get(key, answers.get(field, "")).strip()
    if not raw:
        raise ValueError(field.casefold())
    if any(ch.isspace() for ch in raw) and not (raw.startswith('"') and raw.endswith('"')):
        escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
        raw = f'"{escaped}"'
    return f"{field.casefold()}:{raw}"


def load_filters(path: Path | None = None) -> list[SavedFilter]:
    """Load saved filters; missing file is an empty list."""
    src = path if path is not None else filters_path()
    if not src.is_file():
        return []
    try:
        data = tomllib.loads(src.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("filters.toml: %s", exc)
        return []
    rows = data.get("filter")
    if not isinstance(rows, list):
        return []
    out: list[SavedFilter] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            out.append(_row(as_json_object(item)))
        except ValueError:
            continue
    return out


def upsert_filter(row: SavedFilter, *, path: Path | None = None) -> SavedFilter:
    """Insert or replace by name+scope."""
    clean = _validate(row)
    dest = path if path is not None else filters_path()
    rows = [r for r in load_filters(dest) if not _same(r, clean)]
    rows.append(clean)
    _write(dest, rows)
    return clean


def remove_filter(name: str, scope: str, *, path: Path | None = None) -> bool:
    """Drop the named filter in *scope*. True when something was removed."""
    dest = path if path is not None else filters_path()
    before = load_filters(dest)
    after = [r for r in before if not (r.name == name.strip() and r.scope == scope)]
    if len(after) == len(before):
        return False
    _write(dest, after)
    return True


def filters_for_scope(scope: str, *, path: Path | None = None) -> list[SavedFilter]:
    """Saved filters for one search box, name-sorted."""
    return sorted(
        (r for r in load_filters(path) if r.scope == scope),
        key=lambda r: r.name.casefold(),
    )


def _same(a: SavedFilter, b: SavedFilter) -> bool:
    return a.name.casefold() == b.name.casefold() and a.scope == b.scope


def _validate(row: SavedFilter) -> SavedFilter:
    name = " ".join((row.name or "").split())
    if not name or len(name) > _NAME_MAX:
        raise ValueError("name")
    scope = (row.scope or "").strip().casefold()
    if scope not in SCOPES:
        raise ValueError("scope")
    query = (row.query or "").strip()
    if not query:
        raise ValueError("query")
    return SavedFilter(name, scope, query)


def _row(item: JsonObject) -> SavedFilter:
    name = json_as_str(item.get("name"))
    scope = json_as_str(item.get("scope"))
    query = json_as_str(item.get("query"))
    if not name or not scope or not query:
        raise ValueError("row")
    return _validate(SavedFilter(name, scope, query))


def _write(path: Path, rows: list[SavedFilter]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Saved search filters. Copy from examples/filters/.\n"]
    for row in rows:
        lines.append("[[filter]]\n")
        lines.append(f"name = {_toml_str(row.name)}\n")
        lines.append(f"scope = {_toml_str(row.scope)}\n")
        lines.append(f"query = {_toml_str(row.query)}\n")
        lines.append("\n")
    path.write_text("".join(lines), encoding="utf-8")


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
