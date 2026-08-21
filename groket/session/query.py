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
from pathlib import Path

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

from ..models import JsonObject, SessionMeta, json_as_str

# Tokens the last-token helper offers. Values for ``in:`` / ``model:`` come
# from the loaded catalog, not this table.
FIELD_NAMES: tuple[str, ...] = (
    "is",
    "has",
    "in",
    "model",
    "task",
    "errors",
    "turns",
    "tools",
    "events",
    "after",
    "before",
)
IS_VALUES: tuple[str, ...] = (
    "running",
    "awaiting",
    "ending",
    "complete",
    "cancelled",
    "host",
    "eval",
)
HAS_VALUES: tuple[str, ...] = ("workflows", "notes", "findings", "errors")
COMPARE_PREFIXES: tuple[str, ...] = (">=", "<=", ">", "<", "=")

_INCOMPLETE_FIELD = re.compile(
    rf"(?i)(?:^|\s)(?:{'|'.join(FIELD_NAMES)}):$",
)
_TRAILING_BOOL = re.compile(r"(?i)(?:^|\s)(?:AND|OR|NOT)$")
_IN_UNQUOTED = re.compile(r'(?i)(?<![A-Za-z0-9_])(in:)(?!")(\S+)')
_IS_HOST = re.compile(r"(?i)(?<![A-Za-z0-9_])is:host\b")
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
    task_id: str = ""
    error_count: int = 0
    turn_count: int = 0
    tool_count: int = 0
    event_count: int = 0
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
            task_id=json_as_str(row.get("taskId")),
            error_count=_as_int(row.get("errorCount")),
            turn_count=_as_int(row.get("turnCount")),
            tool_count=_as_int(row.get("toolCallCount")),
            event_count=_as_int(row.get("numEvents")),
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
            task_id=meta.task_id or "",
            error_count=int(meta.error_count or 0),
            turn_count=int(meta.turn_count or 0),
            tool_count=int(meta.tool_call_count or 0),
            event_count=int(meta.num_events or 0),
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
    """Quote ``in:`` paths so luqum does not treat ``~`` / ``/…/`` as Lucene."""
    return _IN_UNQUOTED.sub(lambda match: f'{match.group(1)}"{match.group(2)}"', query)


def row_matches_query(row: CatalogQueryRow, query: str) -> bool:
    """True when *row* satisfies *query* (empty query matches)."""
    text = finished_prefix(query).strip()
    if not text:
        return True
    try:
        tree = _RESOLVE_AND(luqum_parser.parse(prepare_query(text)))
    except Exception:
        words = _bare_words(text)
        return True if not words else _match_words(row, words)
    return _eval(tree, row)


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


def toggle_is_host(query: str) -> str:
    """Add or remove ``is:host`` (``H`` on the home list)."""
    text = (query or "").strip()
    if _IS_HOST.search(text):
        return _WORD_SPLIT.sub(" ", _IS_HOST.sub(" ", text)).strip()
    if not text:
        return "is:host"
    return f"{text} is:host"


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
    if field in {"errors", "turns", "tools", "events"}:
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
        return _match_in(_term_text(expr), row.path)
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
    if value == "workflows":
        return row.has_workflows
    if value == "notes":
        return row.has_notes
    if value == "findings":
        return row.has_findings
    if value == "errors":
        return row.error_count > 0
    return False


def _match_in(needle: str, path: str) -> bool:
    want = _expand_path(needle)
    have = _expand_path(path)
    if not want:
        return False
    return have == want or have.startswith(want.rstrip("/") + "/")


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
    bound = _parse_epoch(raw)
    if stamp <= 0 or bound <= 0:
        return False
    return stamp >= bound if after else stamp <= bound


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
    return row.event_count


def _expr_number(expr: Item) -> int:
    text = _term_text(expr)
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


def catalog_has_findings(session_id: str) -> bool:
    """True when any analysis cache JSON exists for *session_id*."""
    from ..paths import APP_HOME

    root = APP_HOME / "cache" / "analysis" / session_id
    try:
        return any(root.glob("*.json"))
    except OSError:
        return False


__all__ = [
    "COMPARE_PREFIXES",
    "FIELD_NAMES",
    "HAS_VALUES",
    "IS_VALUES",
    "CatalogQueryRow",
    "apply_suggestion",
    "catalog_has_findings",
    "catalog_has_notes",
    "catalog_has_workflows",
    "finished_prefix",
    "prepare_query",
    "query_has_tokens",
    "row_matches_query",
    "suggest_last_token",
    "toggle_is_host",
]
