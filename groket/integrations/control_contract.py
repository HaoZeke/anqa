"""Control JSON-RPC contract: version, methods, notifications, and emit.

This module is the single source for the owner handshake version, the
method and notification inventory, and the operator document plus JSON
Schema generated from it. ``ControlServer`` initialize capabilities and
dispatch keys come from the same inventory.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from ..models import JsonObject, as_json_object

# Handshake only. Independent of ``groket.__version__``.
# Same major: additive methods and fields; a live owner of that major stays up.
# A major bump is the only backwards-incompatible change.
MIN_PROTOCOL_VERSION = "1.0.0"
PROTOCOL_VERSION = "1.0.0"

SCHEMA_TITLE = "groket-control"
SCHEMA_ID = "https://indynull.github.io/groket/schemas/control.schema.json"
CATALOG_QUERY_ASSET = Path("desktop/assets/catalog-query.json")


@dataclass(frozen=True)
class CatalogQueryToken:
    """One ``session/list`` ``query`` token (``is:``, ``has:``, …)."""

    name: str
    role: str
    values: tuple[str, ...] = ()
    compare: bool = False


CATALOG_QUERY_BARE = "title, sessionId, and label"
CATALOG_QUERY_OPERATORS: tuple[str, ...] = ("AND", "OR", "NOT", "-")
CATALOG_QUERY_COMPARE: tuple[str, ...] = (">=", "<=", ">", "<", "=")
CATALOG_QUERY_TOKENS: tuple[CatalogQueryToken, ...] = (
    CatalogQueryToken(
        "is",
        "Status or origin.",
        ("running", "awaiting", "ending", "complete", "cancelled", "host", "eval"),
    ),
    CatalogQueryToken(
        "has",
        "Presence on the list row.",
        (
            "workflows",
            "notes",
            "goals",
            "subagents",
            "tasks",
            "jobs",
            "schedules",
            "plan",
            "errors",
            "failures",
            "diff",
            "git",
            "context",
            "compaction",
            "doom",
        ),
    ),
    CatalogQueryToken("in", "Directory the session was run in."),
    CatalogQueryToken("model", "Model id substring."),
    CatalogQueryToken("task", "Task id substring."),
    CatalogQueryToken("errors", "errorCount.", compare=True),
    CatalogQueryToken("turns", "turnCount.", compare=True),
    CatalogQueryToken("tools", "toolCallCount.", compare=True),
    CatalogQueryToken("events", "numEvents.", compare=True),
    CatalogQueryToken("duration", "Session length (1h, 2d, 30m).", compare=True),
    CatalogQueryToken(
        "after",
        "updatedAt on or after this time (ISO, yesterday, 2d, 2 days ago).",
    ),
    CatalogQueryToken(
        "before",
        "updatedAt on or before this time (ISO, yesterday, 2d, 2 days ago).",
    ),
)


def catalog_query_compare_fields() -> tuple[str, ...]:
    """Token names that take ``>`` / ``>=`` / ``<`` / ``<=`` / ``=``."""
    return tuple(token.name for token in CATALOG_QUERY_TOKENS if token.compare)


def catalog_query_field_names() -> tuple[str, ...]:
    """Token names last-token completion offers."""
    return tuple(token.name for token in CATALOG_QUERY_TOKENS)


def catalog_query_values(name: str) -> tuple[str, ...]:
    """Closed values for *name*, or empty when the catalog supplies them."""
    for token in CATALOG_QUERY_TOKENS:
        if token.name == name:
            return token.values
    return ()


def catalog_query_help_plain() -> str:
    """Compact catalog-search help. Same tokens as ``catalogQuery``.

    :returns: Wrapped plain lines for TUI ``?`` and the HUD cheatsheet.
    """
    lines = [
        "Bare words match title, id, and label. Space is AND.",
        "",
    ]
    compare: list[str] = []
    for token in CATALOG_QUERY_TOKENS:
        if token.values:
            lines.extend(_wrap_csv(f"{token.name}: ", token.values))
        elif token.compare:
            compare.append(f"{token.name}:")
        else:
            lines.extend(_wrap_words(f"{token.name}: ", token.role.rstrip(".")))
    if compare:
        cmp = "  ".join(CATALOG_QUERY_COMPARE)
        lines.append(f"{' '.join(compare)}  {cmp}")
    lines.append("  ".join((*CATALOG_QUERY_OPERATORS, "(", ")")))
    return "\n".join(lines)


def _wrap_csv(prefix: str, items: Sequence[str], width: int = 68) -> list[str]:
    """Wrap a comma list so help stays a short column, not one long line."""
    return _wrap_parts(prefix, items, ", ", width)


def _wrap_words(prefix: str, body: str, width: int = 68) -> list[str]:
    """Wrap a role sentence on word boundaries."""
    return _wrap_parts(prefix, body.split(), " ", width)


def _wrap_parts(prefix: str, items: Sequence[str], sep: str, width: int) -> list[str]:
    out: list[str] = []
    current = prefix
    pad = " " * len(prefix)
    for i, item in enumerate(items):
        piece = item if i == 0 else f"{sep}{item}"
        if current != prefix and len(current) + len(piece) > width:
            out.append(current)
            current = pad + item
        else:
            current += piece
    if current.strip():
        out.append(current)
    return out


def catalog_query_mapping() -> JsonObject:
    """JSON for the published schema and the HUD token file."""
    return {
        "bareWords": CATALOG_QUERY_BARE,
        "implicitAnd": True,
        "operators": list(CATALOG_QUERY_OPERATORS),
        "compare": list(CATALOG_QUERY_COMPARE),
        "tokens": [
            {
                "name": token.name,
                "role": token.role,
                "values": list(token.values),
                "compare": token.compare,
            }
            for token in CATALOG_QUERY_TOKENS
        ],
    }


def _session_list_query_md() -> str:
    """Operator markdown for ``session/list`` ``query``."""
    rows = [
        "`query` is the catalog language. Bare words match title, id, and label.",
        "Space is AND. Full token list: this schema's `catalogQuery`.",
        "",
        "| Token | Matches |",
        "|-------|---------|",
    ]
    for token in CATALOG_QUERY_TOKENS:
        if token.values:
            names = " ".join(f"`{token.name}:{value}`" for value in token.values)
            rows.append(f"| {names} | {token.role} |")
        elif token.compare:
            rows.append(f"| `{token.name}:` with `>` `>=` `<` `<=` `=` | {token.role} |")
        else:
            rows.append(f"| `{token.name}:` | {token.role} |")
    rows += [
        "",
        "Optional `limit` and `offset` page the filtered rows; omit",
        "`offset` for the first page. Optional `sinceRevision` matching",
        "the owner’s `revision` returns no rows (`unchanged`). When the",
        "client is behind, the owner may send a `delta` (upserted rows",
        "plus `removed` ids). Result includes `sessions`, `total`,",
        "`matched`, and `revision`. Clients that need the full catalog",
        "drain pages until `matched` on first paint only.",
    ]
    return "\n".join(rows)


NOTIFY_SESSION_SELECTED = "session/selected"
NOTIFY_SESSION_CHANGED = "session/changed"
NOTIFY_NOTES_CHANGED = "notes/changed"


@dataclass(frozen=True)
class FieldSpec:
    """One request, result, or notification field on the socket."""

    name: str
    role: str
    required: bool = False
    json_type: str = "string"


@dataclass(frozen=True)
class MethodSpec:
    """One JSON-RPC method the owner implements."""

    name: str
    role: str
    params: tuple[FieldSpec, ...] = ()
    result: tuple[FieldSpec, ...] = ()
    extra_md: str = ""
    capability: bool = True


@dataclass(frozen=True)
class NotificationSpec:
    """One JSON-RPC notification the owner publishes (no ``id``)."""

    name: str
    when: str
    params: tuple[FieldSpec, ...] = ()


_SESSION = FieldSpec("session", "Session id or path.", required=True)
_SESSION_ID = FieldSpec("sessionId", "Session directory name.")
_PROMPT_INDEX = FieldSpec("promptIndex", "Turn / prompt index.", json_type="integer")
_REVISION = FieldSpec("revision", "Notes document revision.")
_EXPECTED_REV = FieldSpec("expectedRevision", "Notes revision the client last read.")


METHODS: tuple[MethodSpec, ...] = (
    MethodSpec(
        name="initialize",
        role=f"Handshake (owner reports `protocolVersion` `{PROTOCOL_VERSION}`)",
        capability=False,
        params=(
            FieldSpec(
                "protocolVersion",
                "Client protocol version (same major as the owner).",
                required=True,
            ),
            FieldSpec(
                "clientInfo",
                "Optional client name and version.",
                json_type="object",
            ),
        ),
        result=(
            FieldSpec("protocolVersion", "Owner protocol version.", required=True),
            FieldSpec(
                "capabilities",
                "Method names the owner implements after handshake.",
                required=True,
                json_type="array",
            ),
            FieldSpec(
                "renderFormats",
                "Values ``session/render`` accepts for ``format``.",
                required=True,
                json_type="array",
            ),
        ),
    ),
    MethodSpec(
        name="session/list",
        role="Catalog page (see below)",
        params=(
            FieldSpec(
                "query",
                "Catalog query language (see catalogQuery in this schema).",
            ),
            FieldSpec("limit", "Page size.", json_type="integer"),
            FieldSpec(
                "offset",
                "Page start; omit for the first page.",
                json_type="integer",
            ),
            FieldSpec(
                "sinceRevision",
                "When this matches the owner revision, the page is empty "
                "(`unchanged`). A client that is behind may receive a "
                "`delta` (upserted rows plus `removed` ids).",
                json_type="integer",
            ),
        ),
        result=(
            FieldSpec("sessions", "Catalog rows.", json_type="array"),
            FieldSpec("total", "Unfiltered catalog size.", json_type="integer"),
            FieldSpec("matched", "Rows matching ``query``.", json_type="integer"),
            FieldSpec("revision", "Catalog revision.", json_type="integer"),
        ),
        extra_md=_session_list_query_md(),
    ),
    MethodSpec(
        name="session/get",
        role="Session meta (status, context, counts, notes revision)",
        params=(_SESSION,),
    ),
    MethodSpec(
        name="session/overview",
        role="Meta + turns + notes + event/tool counts (`stats`). Turns include `subagentRuns`. "
        "Also `backgroundJobs`, `schedules`, and `workflows` (no log or script bodies).",
        params=(_SESSION,),
        result=(
            FieldSpec("backgroundJobs", "Background shell and monitor rows.", json_type="array"),
            FieldSpec("schedules", "Durable scheduler rows.", json_type="array"),
            FieldSpec("workflows", "Grok workflow run rows.", json_type="array"),
            FieldSpec(
                "stats",
                "Full-session event type and tool counts (`eventTypes`, `tools`).",
                json_type="object",
            ),
        ),
        extra_md=(
            "`backgroundJobs`, `schedules`, and `workflows` are additive. Each job has `id`,\n"
            "`kind` (`background` or `monitor`), `status`, `description`,\n"
            "`command`, `cwd`, `startedAt`, `endedAt`, `outputPath`,\n"
            "`reported`, `toolCallId`, and `eventIndex`. Schedules have `id`, `intervalSecs`,\n"
            "`humanSchedule`, `nextFireAt`, `lastFiredAt`, `lastSubagentId`,\n"
            "`promptPreview`, `durable`, `recurring`, and `createdAt`.\n"
            "Workflows have `id`, `name`, `status`, `phase`, `objective`,\n"
            "`agentsUsed`, `agentBudget`, `elapsedMs`, `pauseMessage`, `eventIndex`,\n"
            "and `children` (id, label, success, sessionId, path).\n"
            "`stats.eventTypes` and `stats.tools` are `{id, count}` rows from the parsed\n"
            "session so clients do not page Timeline to fill Stats.\n"
            "Overview does not embed log tails or Rhai script bodies."
        ),
    ),
    MethodSpec(
        name="session/timeline",
        role="Paged events (`offset`, `limit`, `type`, `kind`, `query`, "
        "`promptIndex`, `aroundIndex`, `atIndex`, `contentChars`). "
        "Spawn/finish rows include `childSessionId` and finish stats.",
        params=(
            _SESSION,
            FieldSpec("offset", "Filtered page start.", json_type="integer"),
            FieldSpec("limit", "Page size.", json_type="integer"),
            FieldSpec("type", "Event type filter (also accepted as `eventType`)."),
            FieldSpec("kind", "Kind filter (tools, user, assistant, …)."),
            FieldSpec("query", "Substring match over the event body."),
            FieldSpec("promptIndex", "Restrict to one turn.", json_type="integer"),
            FieldSpec(
                "aroundIndex",
                "Center the page on this event index.",
                json_type="integer",
            ),
            FieldSpec(
                "atIndex",
                "Return the single event at this index.",
                json_type="integer",
            ),
            FieldSpec(
                "contentChars",
                "Body character cap (owner clamps to its ceiling).",
                json_type="integer",
            ),
        ),
    ),
    MethodSpec(
        name="session/turns",
        role="Turn segments plus `subagentRuns` (turn-scoped child runs; "
        "`openable` + `childPath`).",
        params=(_SESSION,),
    ),
    MethodSpec(
        name="session/usage",
        role="Tool / MCP / skill usage",
        params=(_SESSION,),
    ),
    MethodSpec(
        name="session/diff",
        role="Rewind snapshots or approximate `search_replace` edits "
        "(files + hunks + prompt/assistant text)",
        params=(_SESSION,),
    ),
    MethodSpec(
        name="session/open",
        role="Resolve a session and notify `session/selected`",
        params=(_SESSION, _PROMPT_INDEX),
        result=(FieldSpec("opened", "True when the session resolved.", json_type="boolean"),),
    ),
    MethodSpec(
        name="session/render",
        role="Project a document (`format`: below)",
        params=(
            _SESSION,
            FieldSpec("format", "Projection: `org` (default), `markdown`, or `json`."),
        ),
        extra_md="",  # filled after CONTENT_TYPES table in emit
    ),
    MethodSpec(
        name="session/follow_up",
        role="Stage or queue the next prompt (`session`, `prompt`, optional `final`)",
        params=(
            _SESSION,
            FieldSpec("prompt", "Follow-up text.", required=True),
            FieldSpec("final", "Mark the turn done after this prompt.", json_type="boolean"),
        ),
    ),
    MethodSpec(
        name="session/done",
        role="Mark a live session done (`session`)",
        params=(_SESSION,),
    ),
    MethodSpec(
        name="notes/list",
        role="Notes snapshot (`revision`, schema, notes)",
        params=(_SESSION,),
    ),
    MethodSpec(
        name="notes/upsert",
        role="Write a note (`expectedRevision`)",
        params=(
            _SESSION,
            FieldSpec("note", "Note object to write.", required=True, json_type="object"),
            _EXPECTED_REV,
        ),
        extra_md=(
            "Every `notes/upsert` and `notes/delete` sends `expectedRevision`.\n"
            "A mismatch is a conflict; the client reloads and retries.\n"
            "Canonical store is `operator_notes.toml` (host sessions under\n"
            "`~/.groket/notes/`).\n"
            "Every note must include a non-empty `source` (who wrote it).\n"
            "`fields` need not match the configured form schema; extra keys\n"
            "are stored as sent. The in-app form uses `notes_schema.toml`\n"
            "and stamps its own source."
        ),
    ),
    MethodSpec(
        name="notes/delete",
        role="Delete a note (`expectedRevision`)",
        params=(
            _SESSION,
            FieldSpec("noteId", "Note id to delete.", required=True),
            _EXPECTED_REV,
        ),
    ),
)


NOTIFICATIONS: tuple[NotificationSpec, ...] = (
    NotificationSpec(
        name=NOTIFY_SESSION_SELECTED,
        when="After `session/open`",
        params=(_SESSION_ID, _PROMPT_INDEX),
    ),
    NotificationSpec(
        name=NOTIFY_SESSION_CHANGED,
        when="Session files or status changed. `listChanged` is false when only the trace grew.",
        params=(
            _SESSION_ID,
            FieldSpec(
                "listChanged",
                "False when only the trace grew; catalog row fields are unchanged.",
                json_type="boolean",
            ),
        ),
    ),
    NotificationSpec(
        name=NOTIFY_NOTES_CHANGED,
        when="Notes written or deleted",
        params=(_SESSION_ID, _REVISION),
    ),
)


def method_names() -> tuple[str, ...]:
    """Every JSON-RPC method name in the contract, including ``initialize``."""
    return tuple(spec.name for spec in METHODS)


def capability_names() -> tuple[str, ...]:
    """Method names advertised in ``initialize`` ``capabilities``."""
    return tuple(spec.name for spec in METHODS if spec.capability)


def notification_names() -> tuple[str, ...]:
    """Outbound notification method names."""
    return tuple(spec.name for spec in NOTIFICATIONS)


def method_by_name(name: str) -> MethodSpec | None:
    """Return the contract row for *name*, or ``None``."""
    for spec in METHODS:
        if spec.name == name:
            return spec
    return None


def _field_schema(spec: FieldSpec) -> JsonObject:
    return as_json_object(
        {
            "description": spec.role,
            "type": spec.json_type,
        }
    )


def _object_schema(fields: Sequence[FieldSpec], *, title: str) -> JsonObject:
    required = [item.name for item in fields if item.required]
    body: JsonObject = {
        "title": title,
        "type": "object",
        "additionalProperties": True,
        "properties": as_json_object({item.name: _field_schema(item) for item in fields}),
    }
    if required:
        return as_json_object({**body, "required": list(required)})
    return as_json_object(body)


def control_json_schema() -> JsonObject:
    """JSON Schema for the control contract (draft 2020-12)."""
    methods: JsonObject = {}
    for spec in METHODS:
        methods[spec.name] = {
            "description": spec.role,
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "params": _object_schema(spec.params, title=f"{spec.name} params"),
                "result": _object_schema(spec.result, title=f"{spec.name} result"),
            },
            "required": ["params"],
        }
    notifications: JsonObject = {}
    for note in NOTIFICATIONS:
        notifications[note.name] = {
            "description": note.when,
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "params": _object_schema(note.params, title=f"{note.name} params"),
            },
            "required": ["params"],
        }
    return as_json_object(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_ID,
            "title": SCHEMA_TITLE,
            "description": (
                "JSON-RPC 2.0 control protocol for groket serve "
                f"(protocolVersion {PROTOCOL_VERSION})."
            ),
            "type": "object",
            "additionalProperties": False,
            "required": [
                "protocolVersion",
                "minProtocolVersion",
                "methods",
                "notifications",
                "catalogQuery",
            ],
            "properties": {
                "catalogQuery": {
                    "description": "session/list query language (tokens, values, operators).",
                    "const": catalog_query_mapping(),
                },
                "protocolVersion": {
                    "const": PROTOCOL_VERSION,
                    "description": "Owner initialize protocolVersion.",
                },
                "minProtocolVersion": {
                    "const": MIN_PROTOCOL_VERSION,
                    "description": "Oldest protocolVersion this owner accepts.",
                },
                "methods": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(method_names()),
                    "properties": methods,
                },
                "notifications": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(notification_names()),
                    "properties": notifications,
                },
            },
        }
    )


def emit_control_schema(out: Path | None = None) -> str:
    """Serialize the control JSON Schema; optionally write *out*."""
    text = json.dumps(control_json_schema(), indent=2) + "\n"
    if out is not None:
        dest = Path(out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return text


def emit_catalog_query_asset(out: Path | None = None) -> str:
    """Write the HUD token file (same mapping as ``catalogQuery``)."""
    text = json.dumps(catalog_query_mapping(), indent=2) + "\n"
    dest = Path(out) if out is not None else CATALOG_QUERY_ASSET
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return text


def _md_table(headers: tuple[str, str], rows: Sequence[tuple[str, str]]) -> str:
    lines = [
        f"| {headers[0]} | {headers[1]} |",
        "|--------|------|",
    ]
    for left, right in rows:
        lines.append(f"| {left} | {right} |")
    return "\n".join(lines)


def render_control_doc() -> str:
    """Operator markdown for ``docs/control.md``."""
    method_rows = tuple((f"`{spec.name}`", spec.role) for spec in METHODS)
    notify_rows = tuple((f"`{spec.name}`", spec.when) for spec in NOTIFICATIONS)
    list_extra = next(spec.extra_md for spec in METHODS if spec.name == "session/list")
    notes_extra = next(spec.extra_md for spec in METHODS if spec.name == "notes/upsert")
    body = f"""# Control

One process owns a per-user Unix socket. The four clients — [terminal
app](../README.md#terminal-app), [Desktop HUD](../README.md#desktop-hud),
[Emacs](../README.md#emacs), and [Neovim](../README.md#neovim-09) — attach
and talk JSON-RPC 2.0. They never bind the socket.

Implementation: `groket/integrations/control_contract.py` (contract),
`groket/integrations/control.py` (owner),
`groket/integrations/daemon.py` (`groket serve`),
`groket/integrations/control_client.py` (Python attach).

## Start and stop

```bash
groket serve                 # foreground (Ctrl-C / SIGTERM)
groket serve -d              # background; return when the socket accepts
groket serve stop
groket serve restart         # stop, then start -d
groket serve status          # exit 0 if live
```

A second `serve -d` reports already running. Quitting a client leaves the
owner up.

## Socket

Default path: `$XDG_RUNTIME_DIR/groket/control.sock`, or
`~/.groket/run/control.sock` when `XDG_RUNTIME_DIR` is unset.

`-s` / `--socket PATH` on `serve` and on every client selects another
path. The HUD also reads `GROKET_CONTROL_SOCKET` (the Python launcher sets
this when it starts the palette).

```bash
groket serve -d -s /path/to/control.sock
groket -s /path/to/control.sock
```

## Framing

JSON-RPC 2.0, protocol version **{PROTOCOL_VERSION}** (`initialize` with
`protocolVersion: "{PROTOCOL_VERSION}"`). Same major is compatible: a newer
client keeps a live owner of that major. A major bump is the only
backwards-incompatible change; older clients fail `initialize`. Two
frames on the same socket:

- one JSON object per line
- LSP-style headers ending in `Content-Length: N` plus a blank line, then
  N bytes of JSON

The owner accepts either and replies in the same frame the client used.

## Methods

`initialize` returns `protocolVersion`, `capabilities`, and
`renderFormats`.

{_md_table(("Method", "Role"), method_rows)}

### `session/list`

{list_extra}

### `session/render`

| `format` | `contentType` | Typical client |
|----------|---------------|----------------|
| `org` (default) | `text/org` | Emacs |
| `markdown` | `text/markdown` | Neovim |
| `json` | `application/json` | Scripts |

### Notes revision

{notes_extra}

## Notifications

{_md_table(("Method", "When"), notify_rows)}

No `id` on these messages (JSON-RPC notifications).
"""
    return body if body.endswith("\n") else body + "\n"


class InventorySnapshot(TypedDict):
    """Handshake major plus method, notification, and initialize field names."""

    major: int
    methods: tuple[str, ...]
    notifications: tuple[str, ...]
    handshake: tuple[str, ...]


def protocol_major(version: str | None = None) -> int:
    """Integer major of *version* (defaults to :data:`PROTOCOL_VERSION`)."""
    text = (version or PROTOCOL_VERSION).split(".", 1)[0]
    return int(text)


def handshake_field_names() -> tuple[str, ...]:
    """``initialize`` request and result field names, in contract order."""
    spec = method_by_name("initialize")
    if spec is None:
        return ()
    return tuple(item.name for item in spec.params) + tuple(item.name for item in spec.result)


def inventory_snapshot() -> InventorySnapshot:
    """Current handshake major plus method, notification, and initialize fields."""
    return {
        "major": protocol_major(),
        "methods": method_names(),
        "notifications": notification_names(),
        "handshake": handshake_field_names(),
    }


def is_breaking_inventory_change(previous: InventorySnapshot, current: InventorySnapshot) -> bool:
    """True when a method, notification, or initialize field was removed or renamed."""
    if set(previous["methods"]) - set(current["methods"]):
        return True
    if set(previous["notifications"]) - set(current["notifications"]):
        return True
    return previous["handshake"] != current["handshake"]


def emit_control_doc(out: Path | None = None) -> str:
    """Serialize ``docs/control.md``; optionally write *out*."""
    text = render_control_doc()
    if out is not None:
        dest = Path(out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return text


__all__ = (
    "MIN_PROTOCOL_VERSION",
    "NOTIFY_NOTES_CHANGED",
    "NOTIFY_SESSION_CHANGED",
    "NOTIFY_SESSION_SELECTED",
    "PROTOCOL_VERSION",
    "SCHEMA_ID",
    "CATALOG_QUERY_ASSET",
    "CATALOG_QUERY_COMPARE",
    "CATALOG_QUERY_TOKENS",
    "CatalogQueryToken",
    "catalog_query_compare_fields",
    "catalog_query_field_names",
    "catalog_query_help_plain",
    "catalog_query_mapping",
    "catalog_query_values",
    "emit_catalog_query_asset",
    "FieldSpec",
    "InventorySnapshot",
    "MethodSpec",
    "NotificationSpec",
    "METHODS",
    "NOTIFICATIONS",
    "capability_names",
    "control_json_schema",
    "emit_control_doc",
    "emit_control_schema",
    "handshake_field_names",
    "inventory_snapshot",
    "is_breaking_inventory_change",
    "method_by_name",
    "method_names",
    "notification_names",
    "protocol_major",
    "render_control_doc",
)
