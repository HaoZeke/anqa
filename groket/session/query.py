"""Catalog query language: luqum parse tree applied to list columns.

``session/list`` ``query`` is this language. Bare words match title, id, and
label. Typed tokens (``is:``, ``has:``, ``errors:>20``, ``in:``) match
catalog columns. Implicit space is AND. Unknown fields and parse failures
become ordinary words (Gmail-style).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import dateparser
from luqum.parser import parser as luqum_parser
from luqum.tree import (
    AndOperation,
    From,
    Group,
    Item,
    Not,
    OrOperation,
    Phrase,
    Prohibit,
    Range,
    SearchField,
    To,
    UnknownOperation,
    Word,
)
from luqum.utils import UnknownOperationResolver
from pytimeparse import parse as parse_span

from ..integrations.control_contract import (
    CATALOG_QUERY_COMPARE,
    catalog_query_compare_fields,
    catalog_query_field_names,
    catalog_query_values,
)
from ..models import JsonObject, SessionMeta, json_as_str

# Language comes from the published control contract. Row attributes for
# ``has:`` stay here (implementation, not the token list).
FIELD_NAMES: tuple[str, ...] = catalog_query_field_names()
IS_VALUES: tuple[str, ...] = catalog_query_values("is")
HAS_VALUES: tuple[str, ...] = catalog_query_values("has")
COMPARE_PREFIXES: tuple[str, ...] = CATALOG_QUERY_COMPARE
COMPARE_FIELDS: tuple[str, ...] = catalog_query_compare_fields()
HAS_FLAGS: tuple[tuple[str, str], ...] = (
    ("workflows", "has_workflows"),
    ("notes", "has_notes"),
)

_INCOMPLETE_FIELD = re.compile(
    rf"(?i)(?:^|\s)(?:{'|'.join(FIELD_NAMES)}):$",
)
_TRAILING_BOOL = re.compile(r"(?i)(?:^|\s)(?:AND|OR|NOT)$")
_IN_UNQUOTED = re.compile(r'(?i)(?<![A-Za-z0-9_])(in:)(?!")(\S+)')
_WHEN_UNQUOTED = re.compile(
    r'(?i)(?<![A-Za-z0-9_])((?:after|before):)(?!")(.+?)(?=\s+(?:AND|OR|NOT)\b|\s*\)|$)'
)
_COMPACT_SPAN = re.compile(r"(?i)^(\d+(?:\.\d+)?)([smhdw])$")
_SPAN_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_WORD_SPLIT = re.compile(r"\s+")
_SKIP_WORDS = frozenset({"and", "or", "not", "(", ")", "((", "))"})

_RESOLVE_AND = UnknownOperationResolver(resolve_to=AndOperation)


@dataclass(frozen=True)
class CatalogQueryRow:
    """Column bag one catalog query can see."""

    session_id: str = ""
    title: str = ""
    label: str = ""
    model: str = ""
    status: str = ""
    outcome: str = ""
    origin: str = ""
    path: str = ""
    git_repo: str = ""
    task_id: str = ""
    error_count: int = 0
    turn_count: int = 0
    tool_count: int = 0
    event_count: int = 0
    duration_seconds: int = 0
    updated_at: str = ""
    has_workflows: bool = False
    has_notes: bool = False
    has_findings: bool = False

    @classmethod
    def from_wire(cls, row: JsonObject) -> CatalogQueryRow:
        """Build from a ``session/list`` row."""
        return cls(
            session_id=json_as_str(row.get("sessionId")),
            title=json_as_str(row.get("title")),
            label=json_as_str(row.get("label")),
            model=json_as_str(row.get("model")),
            status=json_as_str(row.get("status")),
            outcome=json_as_str(row.get("outcome")),
            origin=json_as_str(row.get("origin")),
            path=json_as_str(row.get("path")),
            git_repo=json_as_str(row.get("gitRepo")),
            task_id=json_as_str(row.get("taskId")),
            error_count=_as_int(row.get("errorCount")),
            turn_count=_as_int(row.get("turnCount")),
            tool_count=_as_int(row.get("toolCallCount")),
            event_count=_as_int(row.get("numEvents")),
            duration_seconds=_as_int(row.get("durationSeconds")),
            updated_at=json_as_str(row.get("updatedAt") or row.get("updated_at")),
            has_workflows=bool(row.get("hasWorkflows")),
            has_notes=bool(row.get("hasNotes")),
            has_findings=bool(row.get("hasFindings")),
        )

    @classmethod
    def from_meta(cls, meta: SessionMeta, label: str = "") -> CatalogQueryRow:
        """Build from home-list :class:`~groket.models.SessionMeta`."""
        try:
            path = str(Path(meta.session_dir).expanduser())
        except OSError:
            path = str(meta.session_dir)
        return cls(
            session_id=meta.session_id or "",
            title=meta.title or "",
            label=(label or meta.label or "")[:80],
            model=meta.model_display,
            status=meta.list_status_label() or "",
            outcome=meta.turn_outcome or "",
            origin=meta.origin or "",
            path=path,
            git_repo=meta.git_repo or "",
            task_id=meta.task_id or "",
            error_count=int(meta.error_count or 0),
            turn_count=int(meta.turn_count or 0),
            tool_count=int(meta.tool_call_count or 0),
            event_count=int(meta.num_events or 0),
            duration_seconds=int(meta.duration_seconds or 0),
            updated_at=str(meta.updated_at or ""),
            has_workflows=bool(meta.has_workflows),
            has_notes=bool(meta.has_notes),
            has_findings=bool(meta.has_findings),
        )


def finished_prefix(query: str) -> str:
    """Drop a trailing empty ``field:`` or boolean so as-you-type keeps matches."""
    text = (query or "").rstrip()
    if _INCOMPLETE_FIELD.search(text):
        text = _INCOMPLETE_FIELD.sub("", text).rstrip()
    if _TRAILING_BOOL.search(text):
        text = _TRAILING_BOOL.sub("", text).rstrip()
    return text


def prepare_query(query: str) -> str:
    """Quote ``in:`` paths and ``after:`` / ``before:`` phrases for luqum."""
    text = _IN_UNQUOTED.sub(lambda match: f'{match.group(1)}"{match.group(2)}"', query)
    return _WHEN_UNQUOTED.sub(lambda match: f'{match.group(1)}"{match.group(2).strip()}"', text)


def row_matches_query(row: CatalogQueryRow, query: str) -> bool:
    """True when *row* satisfies *query* (empty query matches)."""
    text = finished_prefix(query).strip()
    if not text:
        return True
    tree = _parsed_tree(text)
    if tree is None:
        words = _bare_words(text)
        return True if not words else _match_words(row, words)
    return _eval(tree, row)


@lru_cache(maxsize=64)
def _parsed_tree(text: str) -> Item | None:
    try:
        return _RESOLVE_AND(luqum_parser.parse(prepare_query(text)))
    except Exception:
        return None


def suggest_last_token(
    query: str,
    *,
    models: Sequence[str] = (),
    paths: Sequence[str] = (),
) -> list[str]:
    """Last-token completions (field names, closed values, live model/path)."""
    token = _last_token(query)
    if ":" not in token:
        return [f"{name}:" for name in FIELD_NAMES if name.startswith(token.casefold())]
    field, _, rest = token.partition(":")
    key = field.casefold()
    prefix = rest.casefold()
    values = _values_for_field(key, models=models, paths=paths)
    return [f"{key}:{value}" for value in values if value.casefold().startswith(prefix)]


def apply_suggestion(query: str, suggestion: str) -> str:
    """Replace the last token with *suggestion* and leave a trailing space."""
    raw = query or ""
    if not raw.strip():
        return f"{suggestion} "
    stripped = raw.rstrip()
    lead = raw[: len(raw) - len(raw.lstrip())]
    if " " not in stripped:
        return f"{lead}{suggestion} "
    head, _sep, _tail = stripped.rpartition(" ")
    return f"{head} {suggestion} "


def query_has_tokens(query: str) -> bool:
    """True when the finished prefix contains a typed ``field:`` token."""
    text = finished_prefix(query)
    return any(re.search(rf"(?i)(?<![A-Za-z0-9_]){name}:", text) for name in FIELD_NAMES)


def _values_for_field(
    field: str,
    *,
    models: Sequence[str],
    paths: Sequence[str],
) -> tuple[str, ...]:
    if field == "is":
        return IS_VALUES
    if field == "has":
        return HAS_VALUES
    if field in COMPARE_FIELDS:
        return COMPARE_PREFIXES
    if field == "model":
        return tuple(dict.fromkeys(m for m in models if m.strip()))
    if field == "in":
        return tuple(dict.fromkeys(_short_path(p) for p in paths if p.strip()))
    return ()


def _short_path(path: str) -> str:
    home = str(Path.home())
    if path.startswith(home + "/") or path == home:
        return "~" + path[len(home) :]
    return path


def _last_token(query: str) -> str:
    text = (query or "").rstrip()
    if not text:
        return ""
    if text.endswith(":"):
        piece = text.rsplit(None, 1)[-1]
        return piece
    return text.rsplit(None, 1)[-1]


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _eval(node: Item, row: CatalogQueryRow) -> bool:
    if isinstance(node, Group):
        children = list(node.children)
        return _eval(children[0], row) if children else True
    if isinstance(node, AndOperation | UnknownOperation):
        return all(_eval(child, row) for child in node.children)
    if isinstance(node, OrOperation):
        return any(_eval(child, row) for child in node.children)
    if isinstance(node, Not | Prohibit):
        children = list(node.children)
        return not _eval(children[0], row) if children else True
    if isinstance(node, SearchField):
        return _eval_field(node.name.casefold(), node.expr, row)
    if isinstance(node, Word | Phrase):
        return _match_words(row, [_term_text(node)])
    return _match_words(row, [str(node)])


def _eval_field(field: str, expr: Item, row: CatalogQueryRow) -> bool:
    if field not in FIELD_NAMES:
        return _match_words(row, [f"{field}:{_term_text(expr)}"])
    if field == "is":
        return _match_is(_term_text(expr).casefold(), row)
    if field == "has":
        return _match_has(_term_text(expr).casefold(), row)
    if field == "in":
        return _match_in(_term_text(expr), row)
    if field == "model":
        return _term_text(expr).casefold() in row.model.casefold()
    if field == "task":
        return _term_text(expr).casefold() in row.task_id.casefold()
    if field == "after":
        return _match_date(row.updated_at, _term_text(expr), after=True)
    if field == "before":
        return _match_date(row.updated_at, _term_text(expr), after=False)
    return _match_number(field, expr, row)


def _match_is(value: str, row: CatalogQueryRow) -> bool:
    origin = row.origin.strip().casefold()
    if value == "host":
        return origin == "host"
    if value == "eval":
        return origin != "host"
    status = row.status.strip().casefold()
    if value in {"cancelled", "canceled"}:
        return status in {"cancelled", "canceled"}
    return status == value


def _match_has(value: str, row: CatalogQueryRow) -> bool:
    if value == "errors":
        return row.error_count > 0
    for name, attr in HAS_FLAGS:
        if name == value:
            return bool(getattr(row, attr))
    return False


def _match_in(needle: str, row: CatalogQueryRow) -> bool:
    """Prefix or substring on the start repo / workspace, not the store path."""
    want = _expand_path(needle)
    started = _expand_path(row.git_repo)
    if not want or not started:
        raw = (row.git_repo or "").casefold()
        return bool(raw) and needle.strip().strip('"').casefold() in raw
    if want.casefold() in started.casefold():
        return True
    return started == want or started.startswith(want.rstrip("/") + "/")


def _expand_path(raw: str) -> str:
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return ""
    try:
        return str(Path(text).expanduser())
    except OSError:
        return text


def _match_date(updated: str, raw: str, *, after: bool) -> bool:
    stamp = _parse_epoch(updated)
    bound = _parse_when(raw)
    if bound <= 0:
        return True
    if stamp <= 0:
        return False
    return stamp >= bound if after else stamp <= bound


def _parse_when(raw: str) -> float:
    """ISO date, compact span (``2d``), or a dateparser phrase (``yesterday``)."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return 0.0
    return _parse_when_cached(text)


@lru_cache(maxsize=256)
def _parse_when_cached(text: str) -> float:
    iso = _parse_epoch(text)
    if iso > 0:
        return iso
    span = _parse_duration_seconds(text)
    if span > 0:
        return datetime.now(tz=UTC).timestamp() - span
    if not any(ch.isalpha() for ch in text):
        return 0.0
    parsed = dateparser.parse(
        text,
        languages=["en"],
        settings={
            "TO_TIMEZONE": "UTC",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PARSERS": ["relative-time"],
        },
    )
    if parsed is None:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _parse_epoch(raw: str) -> float:
    text = (raw or "").strip().strip('"')
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _parse_duration_seconds(raw: str) -> int:
    """``90``, ``1h``, ``2d``, ``30m``, or a pytimeparse phrase."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    compact = _COMPACT_SPAN.fullmatch(text)
    if compact:
        return int(float(compact.group(1)) * _SPAN_SECONDS[compact.group(2).lower()])
    parsed = parse_span(_expand_compact_span(text))
    if parsed is None:
        return 0
    return int(parsed)


def _expand_compact_span(raw: str) -> str:
    compact = _COMPACT_SPAN.fullmatch(raw.strip())
    if compact is None:
        return raw
    amount, unit = compact.group(1), compact.group(2).lower()
    names = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    return f"{amount} {names[unit]} ago"


def _match_number(field: str, expr: Item, row: CatalogQueryRow) -> bool:
    actual = _number_column(field, row)
    if isinstance(expr, From):
        bound = _expr_number(expr.a)
        return actual >= bound if expr.include else actual > bound
    if isinstance(expr, To):
        bound = _expr_number(expr.a)
        return actual <= bound if expr.include else actual < bound
    if isinstance(expr, Range):
        return _in_range(actual, expr)
    return actual == _expr_number(expr)


def _in_range(actual: int, expr: Range) -> bool:
    low = _expr_number(expr.low) if str(expr.low) != "*" else None
    high = _expr_number(expr.high) if str(expr.high) != "*" else None
    if low is not None and actual < low:
        return False
    return high is None or actual <= high


def _number_column(field: str, row: CatalogQueryRow) -> int:
    if field == "errors":
        return row.error_count
    if field == "turns":
        return row.turn_count
    if field == "tools":
        return row.tool_count
    if field == "duration":
        return row.duration_seconds
    return row.event_count


def _expr_number(expr: Item) -> int:
    text = _term_text(expr)
    span = _parse_duration_seconds(text)
    if span > 0:
        return span
    try:
        return int(text)
    except ValueError:
        return 0


def _term_text(expr: Item) -> str:
    if isinstance(expr, Phrase):
        return str(expr.value).strip().strip('"')
    if isinstance(expr, Word):
        return str(expr.value)
    return str(expr).strip().strip('"')


def _bare_words(text: str) -> list[str]:
    return [
        word
        for word in _WORD_SPLIT.split(text)
        if word and word.casefold() not in _SKIP_WORDS and not word.startswith("(")
    ]


def _match_words(row: CatalogQueryRow, words: Sequence[str]) -> bool:
    hay = " ".join(part for part in (row.session_id, row.title, row.label) if part).casefold()
    return all(word.casefold() in hay for word in words if word)


def catalog_has_workflows(session_dir: Path) -> bool:
    """True when ``workflows/`` exists and is non-empty."""
    root = Path(session_dir) / "workflows"
    try:
        return root.is_dir() and any(root.iterdir())
    except OSError:
        return False


def catalog_has_notes(session_dir: Path) -> bool:
    """True when a notes file exists (session dir or config-home fallback)."""
    from ..notes import notes_mtime

    return notes_mtime(session_dir) > 0


__all__ = [
    "COMPARE_PREFIXES",
    "FIELD_NAMES",
    "HAS_FLAGS",
    "HAS_VALUES",
    "IS_VALUES",
    "CatalogQueryRow",
    "apply_suggestion",
    "catalog_has_notes",
    "catalog_has_workflows",
    "finished_prefix",
    "prepare_query",
    "query_has_tokens",
    "row_matches_query",
    "suggest_last_token",
]
