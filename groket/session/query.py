"""Catalog query language: luqum parse tree applied to list columns.

``session/list`` ``query`` is this language. Bare words match title, id, and
label. Typed tokens (``is:``, ``has:``, ``has:errors:>=5``, ``in:``) match
catalog columns. Implicit space is AND. Unknown fields and parse failures
become ordinary words (Gmail-style).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    CATALOG_QUERY_OPERATORS,
    catalog_query_compare_fields,
    catalog_query_field_names,
    catalog_query_has_count_fields,
    catalog_query_values,
)
from ..models import JsonObject, SessionMeta, as_json_object, json_as_str

# Language comes from the published control contract. Row attributes for
# ``has:`` stay here (implementation, not the token list).
FIELD_NAMES: tuple[str, ...] = catalog_query_field_names()
IS_VALUES: tuple[str, ...] = catalog_query_values("is")
HAS_VALUES: tuple[str, ...] = catalog_query_values("has")
HAS_COUNT_FIELDS: dict[str, str] = catalog_query_has_count_fields()
COMPARE_PREFIXES: tuple[str, ...] = CATALOG_QUERY_COMPARE
COMPARE_FIELDS: tuple[str, ...] = catalog_query_compare_fields()
HAS_FLAGS: tuple[tuple[str, str], ...] = (
    ("workflows", "has_workflows"),
    ("notes", "has_notes"),
    ("goals", "has_goals"),
    ("subagents", "has_subagents"),
    ("jobs", "has_jobs"),
    ("schedules", "has_schedules"),
    ("plan", "has_plan"),
    ("failures", "has_failures"),
    ("diff", "has_diff"),
    ("compaction", "has_compaction"),
    ("doom", "has_doom"),
)
_PRESENCE_ATTRS: tuple[tuple[str, str], ...] = tuple(
    (f"has{name[:1].upper()}{name[1:]}", attr) for name, attr in HAS_FLAGS
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
_FIELD_SET = frozenset(FIELD_NAMES)
_BOOL_WORDS = frozenset({"and", "or", "not"})
_HIGHLIGHT_RE = re.compile(
    r"(?P<operator>\b(?:"
    + "|".join(re.escape(op) for op in CATALOG_QUERY_OPERATORS if op != "-")
    + r")\b)"
    r"|(?P<prohibit>-)?(?P<field>(?i:"
    + "|".join(re.escape(name) for name in FIELD_NAMES)
    + r")):(?P<value>\s*\"[^\"]*\"|\s*[^\s)]*)"
)

_RESOLVE_AND = UnknownOperationResolver(resolve_to=AndOperation)

type QuerySpanKind = Literal["field", "value", "unknown", "operator"]


@dataclass(frozen=True)
class QuerySpan:
    """One highlighted slice of a catalog query (character offsets)."""

    start: int
    end: int
    kind: QuerySpanKind


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
    run_dir: str = ""
    task_id: str = ""
    error_count: int = 0
    turn_count: int = 0
    tool_count: int = 0
    event_count: int = 0
    duration_seconds: int = 0
    updated_at: str = ""
    has_workflows: bool = False
    has_notes: bool = False
    has_goals: bool = False
    has_subagents: bool = False
    has_jobs: bool = False
    has_schedules: bool = False
    has_plan: bool = False
    has_failures: bool = False
    has_diff: bool = False
    has_compaction: bool = False
    has_doom: bool = False
    has_context: bool = False
    counts: dict[str, int] = field(default_factory=dict)

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
            run_dir=json_as_str(row.get("runDir")),
            task_id=json_as_str(row.get("taskId")),
            error_count=_as_int(row.get("errorCount")),
            turn_count=_as_int(row.get("turnCount")),
            tool_count=_as_int(row.get("toolCallCount")),
            event_count=_as_int(row.get("numEvents")),
            duration_seconds=_as_int(row.get("durationSeconds")),
            updated_at=json_as_str(row.get("updatedAt") or row.get("updated_at")),
            has_workflows=bool(row.get("hasWorkflows")),
            has_notes=bool(row.get("hasNotes")),
            has_goals=bool(row.get("hasGoals")),
            has_subagents=bool(row.get("hasSubagents")),
            has_jobs=bool(row.get("hasJobs")),
            has_schedules=bool(row.get("hasSchedules")),
            has_plan=bool(row.get("hasPlan")),
            has_failures=bool(row.get("hasFailures")),
            has_diff=bool(row.get("hasDiff")),
            has_compaction=bool(row.get("hasCompaction")),
            has_doom=bool(row.get("hasDoom")),
            has_context=_wire_has_context(row),
            counts=_counts_from_wire(row),
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
            run_dir=meta.run_dir or "",
            task_id=meta.task_id or "",
            error_count=int(meta.error_count or 0),
            turn_count=int(meta.turn_count or 0),
            tool_count=int(meta.tool_call_count or 0),
            event_count=int(meta.num_events or 0),
            duration_seconds=int(meta.duration_seconds or 0),
            updated_at=str(meta.updated_at or ""),
            has_workflows=bool(meta.has_workflows),
            has_notes=bool(meta.has_notes),
            has_goals=bool(meta.has_goals),
            has_subagents=bool(meta.has_subagents),
            has_jobs=bool(meta.has_jobs),
            has_schedules=bool(meta.has_schedules),
            has_plan=bool(meta.has_plan),
            has_failures=bool(meta.has_failures),
            has_diff=bool(meta.has_diff),
            has_compaction=bool(meta.has_compaction),
            has_doom=bool(meta.has_doom),
            has_context=bool(meta.has_context_usage),
            counts=_counts_from_meta(meta),
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
    if not token:
        return []
    if ":" not in token:
        return [f"{name}:" for name in FIELD_NAMES if name.startswith(token.casefold())]
    field, _, rest = token.partition(":")
    key = field.casefold()
    if key == "has":
        name, sep, cmp = rest.partition(":")
        if sep:
            folded = name.casefold()
            if folded not in HAS_COUNT_FIELDS:
                return []
            return [f"has:{folded}:{item}" for item in COMPARE_PREFIXES if item.startswith(cmp)]
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


def _quoted_value(raw: str) -> bool:
    return len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"'


def _extend_open_value(text: str, end: int) -> int:
    """Keep unquoted open values through spaces until AND/OR/NOT or the next field."""
    n = len(text)
    while end < n:
        if text[end] == ")":
            return end
        i = end
        while i < n and text[i] in " \t":
            i += 1
        if i == end or i == n:
            return end
        j = i
        while j < n and text[j] not in " \t)":
            j += 1
        word = text[i:j]
        if word.casefold() in _BOOL_WORDS:
            return end
        head, sep, _rest = word.partition(":")
        if sep and head.casefold() in _FIELD_SET:
            return end
        end = j
    return end


def _trim_value_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in " \t":
        start += 1
    while end > start and text[end - 1] in " \t":
        end -= 1
    return start, end


def _has_compare_known(name: str, cmp: str) -> bool:
    if name not in HAS_COUNT_FIELDS or not cmp:
        return False
    for prefix in COMPARE_PREFIXES:
        if cmp.startswith(prefix):
            return bool(cmp[len(prefix) :].strip())
    return cmp.strip().isalnum()


def _value_known(field: str, inner: str) -> bool:
    closed = catalog_query_values(field)
    if field == "has":
        name, cmp = _split_has_value(inner)
        if not cmp:
            return name in {item.casefold() for item in closed}
        return _has_compare_known(name, cmp)
    if not closed:
        return True
    key = inner.casefold()
    if key in {item.casefold() for item in closed}:
        return True
    return field == "is" and key in {"cancelled", "canceled"}


def highlight_query_spans(query: str) -> tuple[QuerySpan, ...]:
    """Paint offsets for known fields, values, and operators.

    Live box color uses this scanner because the luqum tree has no source
    offsets. Matching still goes through :func:`row_matches_query`.
    """
    text = query or ""
    spans: list[QuerySpan] = []
    for match in _HIGHLIGHT_RE.finditer(text):
        if match.start() > 0 and text[match.start() - 1] not in " \t\n\r(":
            continue
        if match.group("operator") is not None:
            start, end = match.span("operator")
            spans.append(QuerySpan(start, end, "operator"))
            continue
        if match.group("prohibit") is not None:
            start, end = match.span("prohibit")
            spans.append(QuerySpan(start, end, "operator"))
        field_start, field_end = match.span("field")
        spans.append(QuerySpan(field_start, field_end + 1, "field"))
        raw = match.group("value") or ""
        value_start, value_end = match.span("value")
        quoted = _quoted_value(raw)
        field_key = match.group("field").casefold()
        closed = catalog_query_values(field_key)
        if not quoted and not closed:
            value_end = _extend_open_value(text, value_end)
        if not quoted:
            value_start, value_end = _trim_value_span(text, value_start, value_end)
        if value_start >= value_end:
            continue
        inner = text[value_start + 1 : value_end - 1] if quoted else text[value_start:value_end]
        if field_key == "has" and not quoted:
            spans.extend(_has_value_spans(value_start, value_end, inner))
            continue
        kind: QuerySpanKind = "value" if _value_known(field_key, inner) else "unknown"
        spans.append(QuerySpan(value_start, value_end, kind))
    return tuple(spans)


def _has_value_spans(start: int, end: int, inner: str) -> tuple[QuerySpan, ...]:
    name, cmp = _split_has_value(inner)
    closed = {item.casefold() for item in HAS_VALUES}
    if name not in closed:
        return (QuerySpan(start, end, "unknown"),)
    if not cmp:
        return (QuerySpan(start, end, "value"),)
    name_end = start + len(name)
    cmp_kind: QuerySpanKind = "value" if _has_compare_known(name, cmp) else "unknown"
    return (
        QuerySpan(start, name_end, "value"),
        QuerySpan(name_end, end, cmp_kind),
    )


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


def _split_has_value(raw: str) -> tuple[str, str]:
    name, sep, rest = (raw or "").partition(":")
    return name.casefold(), rest if sep else ""


def _has_count(row: CatalogQueryRow, name: str) -> int:
    if name in row.counts:
        return int(row.counts[name])
    if name == "errors":
        return int(row.error_count)
    if name == "tasks":
        return int(row.has_jobs) + int(row.has_schedules)
    flags = {
        "workflows": row.has_workflows,
        "notes": row.has_notes,
        "goals": row.has_goals,
        "subagents": row.has_subagents,
        "jobs": row.has_jobs,
        "schedules": row.has_schedules,
        "plan": row.has_plan,
        "failures": row.has_failures,
        "diff": row.has_diff,
        "compaction": row.has_compaction,
        "doom": row.has_doom,
        "git": bool(row.git_repo.strip()),
        "context": row.has_context,
    }
    return 1 if flags.get(name) else 0


def _match_has(value: str, row: CatalogQueryRow) -> bool:
    name, cmp = _split_has_value(value)
    if cmp:
        if name not in HAS_COUNT_FIELDS:
            return False
        return _match_number_text(_has_count(row, name), cmp)
    if name == "errors":
        return _has_count(row, "errors") > 0
    if name == "git":
        return bool(row.git_repo.strip())
    if name == "context":
        return bool(row.has_context)
    if name == "tasks":
        return bool(row.has_jobs or row.has_schedules) or _has_count(row, "tasks") > 0
    flags = {
        "workflows": row.has_workflows,
        "notes": row.has_notes,
        "goals": row.has_goals,
        "subagents": row.has_subagents,
        "jobs": row.has_jobs,
        "schedules": row.has_schedules,
        "plan": row.has_plan,
        "failures": row.has_failures,
        "diff": row.has_diff,
        "compaction": row.has_compaction,
        "doom": row.has_doom,
    }
    return bool(flags.get(name)) or _has_count(row, name) > 0


def _match_in(needle: str, row: CatalogQueryRow) -> bool:
    """Prefix or substring on the directory the session was run in."""
    want = _expand_path(needle)
    started = _expand_path(row.run_dir)
    if not want or not started:
        raw = (row.run_dir or "").casefold()
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


def _match_number_text(actual: int, raw: str) -> bool:
    """Compare *actual* to ``>=5``, ``>2``, ``3``, or a duration (``1h``)."""
    text = (raw or "").strip().strip('"').strip("'")
    for prefix in COMPARE_PREFIXES:
        if text.startswith(prefix):
            bound = _parse_duration_seconds(text[len(prefix) :])
            if prefix == ">=":
                return actual >= bound
            if prefix == "<=":
                return actual <= bound
            if prefix == ">":
                return actual > bound
            if prefix == "<":
                return actual < bound
            return actual == bound
    return actual == _parse_duration_seconds(text)


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


def _counts_from_wire(row: JsonObject) -> dict[str, int]:
    return {name: _as_int(row.get(wire)) for name, wire in HAS_COUNT_FIELDS.items()}


def _counts_from_meta(meta: SessionMeta) -> dict[str, int]:
    by_wire = {
        "workflowCount": int(meta.workflow_count or 0),
        "noteCount": int(meta.note_count or 0),
        "subagentCount": int(meta.subagent_count or 0),
        "taskCount": int(meta.task_count or 0),
        "jobCount": int(meta.job_count or 0),
        "scheduleCount": int(meta.schedule_count or 0),
        "errorCount": int(meta.error_count or 0),
        "failureCount": int(meta.tool_failure_count or 0),
        "diffLineCount": int(meta.lines_added or 0) + int(meta.lines_removed or 0),
        "compactionCount": int(meta.compaction_count or 0),
        "doomCount": int(meta.doom_loop_warnings or 0),
    }
    return {name: int(by_wire.get(wire, 0)) for name, wire in HAS_COUNT_FIELDS.items()}


def _wire_has_context(row: JsonObject) -> bool:
    if row.get("hasContext") is True:
        return True
    if row.get("contextWindowUsagePct") is not None:
        return True
    if row.get("contextTokensUsed") is not None:
        return True
    window = row.get("contextWindowTokens")
    return isinstance(window, int | float) and not isinstance(window, bool) and window > 0


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _nonempty_dir(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def _json_list_len(path: Path) -> int:
    if not _is_file(path):
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0
    return len(raw) if isinstance(raw, list) else 0


def _json_list_nonempty(path: Path) -> bool:
    return _json_list_len(path) > 0


def _dir_child_count(path: Path) -> int:
    try:
        if not path.is_dir():
            return 0
        return sum(1 for _ in path.iterdir())
    except OSError:
        return 0


def catalog_workflow_count(session_dir: Path) -> int:
    """Child entries under ``workflows/``."""
    return _dir_child_count(Path(session_dir) / "workflows")


def catalog_has_workflows(session_dir: Path) -> bool:
    """True when ``workflows/`` exists and is non-empty."""
    return catalog_workflow_count(session_dir) > 0


def catalog_note_count(session_dir: Path) -> int:
    """Notes in the session notes file."""
    from ..notes import load_notes

    return len(load_notes(session_dir).notes)


def catalog_has_notes(session_dir: Path) -> bool:
    """True when the session notes file has at least one note."""
    return catalog_note_count(session_dir) > 0


def catalog_has_goals(session_dir: Path) -> bool:
    """True when ``goal/state.json`` is present."""
    return _is_file(Path(session_dir) / "goal" / "state.json")


def catalog_subagent_count(session_dir: Path) -> int:
    """Child directories under ``subagents/``."""
    return _dir_child_count(Path(session_dir) / "subagents")


def catalog_has_subagents(session_dir: Path) -> bool:
    """True when ``subagents/`` lists at least one child directory."""
    return catalog_subagent_count(session_dir) > 0


def catalog_job_count(session_dir: Path) -> int:
    """Jobs in the manifest, or ``terminal/`` call logs when there is no list."""
    root = Path(session_dir)
    listed = _json_list_len(root / "background_tasks_manifest.json")
    if listed:
        return listed
    terminal = root / "terminal"
    try:
        if not terminal.is_dir():
            return 0
        return sum(
            1
            for child in terminal.iterdir()
            if child.is_file()
            and (child.name.startswith("call-") or child.name.startswith("monitor-call-"))
        )
    except OSError:
        return 0


def catalog_has_jobs(session_dir: Path) -> bool:
    """True when a job manifest or ``terminal/`` call log is present."""
    return catalog_job_count(session_dir) > 0


def catalog_schedule_count(session_dir: Path) -> int:
    """Scheduler tasks in ``resources_state.json``."""
    path = Path(session_dir) / "resources_state.json"
    if not _is_file(path):
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(raw, dict):
        return 0
    inner = raw.get("state")
    state = inner if isinstance(inner, dict) else {}
    scheduler = state.get("grok_build.Scheduler")
    if not isinstance(scheduler, dict):
        return 0
    tasks = scheduler.get("tasks")
    return len(tasks) if isinstance(tasks, list) else 0


def catalog_has_schedules(session_dir: Path) -> bool:
    """True when ``resources_state.json`` lists scheduler tasks."""
    return catalog_schedule_count(session_dir) > 0


def catalog_has_tasks(session_dir: Path) -> bool:
    """True when Overview Tasks would list a job or a schedule."""
    return catalog_has_jobs(session_dir) or catalog_has_schedules(session_dir)


def catalog_has_plan(session_dir: Path) -> bool:
    """True when ``plan.json`` or ``plan_mode.json`` is present."""
    root = Path(session_dir)
    return _is_file(root / "plan.json") or _is_file(root / "plan_mode.json")


def catalog_has_compaction(session_dir: Path) -> bool:
    """True when ``compaction/`` exists and is non-empty."""
    return _nonempty_dir(Path(session_dir) / "compaction")


def catalog_presence(session_dir: Path, meta: SessionMeta) -> dict[str, bool | int]:
    """``has:`` flags and counts for one catalog row (disk + loaded meta)."""
    jobs = catalog_job_count(session_dir)
    schedules = catalog_schedule_count(session_dir)
    workflows = catalog_workflow_count(session_dir)
    notes = catalog_note_count(session_dir)
    subagents = catalog_subagent_count(session_dir)
    errors = int(meta.error_count or 0)
    failures = int(meta.tool_failure_count or 0)
    diff_lines = int(meta.lines_added or 0) + int(meta.lines_removed or 0)
    compaction = max(
        _dir_child_count(Path(session_dir) / "compaction"),
        int(meta.compaction_count or 0),
    )
    doom = int(meta.doom_loop_warnings or 0)
    tasks = jobs + schedules
    counts = {
        "workflows": workflows,
        "notes": notes,
        "subagents": subagents,
        "tasks": tasks,
        "jobs": jobs,
        "schedules": schedules,
        "errors": errors,
        "failures": failures,
        "diff": diff_lines,
        "compaction": compaction,
        "doom": doom,
    }
    out: dict[str, bool | int] = {
        "hasWorkflows": workflows > 0,
        "hasNotes": notes > 0,
        "hasGoals": catalog_has_goals(session_dir),
        "hasSubagents": subagents > 0,
        "hasJobs": jobs > 0,
        "hasSchedules": schedules > 0,
        "hasTasks": tasks > 0,
        "hasPlan": catalog_has_plan(session_dir),
        "hasFailures": failures > 0,
        "hasDiff": diff_lines > 0,
        "hasCompaction": compaction > 0,
        "hasDoom": doom > 0,
        "hasContext": bool(meta.has_context_usage),
    }
    for name, wire in HAS_COUNT_FIELDS.items():
        out[wire] = int(counts.get(name, 0))
    return out


def apply_catalog_presence(meta: SessionMeta) -> None:
    """Set cheap ``has:`` flags on *meta* from disk and loaded counts."""
    apply_catalog_presence_row(meta, as_json_object(catalog_presence(meta.session_dir, meta)))


_COUNT_META_ATTR: tuple[tuple[str, str], ...] = (
    ("workflowCount", "workflow_count"),
    ("noteCount", "note_count"),
    ("subagentCount", "subagent_count"),
    ("taskCount", "task_count"),
    ("jobCount", "job_count"),
    ("scheduleCount", "schedule_count"),
)


def apply_catalog_presence_row(meta: SessionMeta, row: JsonObject) -> None:
    """Copy ``has*`` flags and countable fields onto *meta*."""
    for key, attr in _PRESENCE_ATTRS:
        setattr(meta, attr, bool(row.get(key)))
    for wire, attr in _COUNT_META_ATTR:
        setattr(meta, attr, _as_int(row.get(wire)))
    if "failureCount" in row:
        meta.tool_failure_count = _as_int(row.get("failureCount"))
    if "compactionCount" in row:
        meta.compaction_count = _as_int(row.get("compactionCount"))
    if "doomCount" in row:
        meta.doom_loop_warnings = _as_int(row.get("doomCount"))


__all__ = [
    "COMPARE_PREFIXES",
    "FIELD_NAMES",
    "HAS_FLAGS",
    "HAS_VALUES",
    "IS_VALUES",
    "CatalogQueryRow",
    "QuerySpan",
    "QuerySpanKind",
    "apply_catalog_presence",
    "apply_catalog_presence_row",
    "apply_suggestion",
    "HAS_COUNT_FIELDS",
    "catalog_has_compaction",
    "catalog_has_goals",
    "catalog_has_jobs",
    "catalog_has_notes",
    "catalog_has_plan",
    "catalog_has_schedules",
    "catalog_has_subagents",
    "catalog_has_tasks",
    "catalog_has_workflows",
    "catalog_job_count",
    "catalog_note_count",
    "catalog_schedule_count",
    "catalog_subagent_count",
    "catalog_workflow_count",
    "catalog_presence",
    "finished_prefix",
    "highlight_query_spans",
    "prepare_query",
    "query_has_tokens",
    "row_matches_query",
    "suggest_last_token",
]
