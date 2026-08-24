# Control

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

JSON-RPC 2.0, protocol version **1.0.0** (`initialize` with
`protocolVersion: "1.0.0"`). Same major is compatible: a newer
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

| Method | Role |
|--------|------|
| `initialize` | Handshake (owner reports `protocolVersion` `1.0.0`) |
| `session/list` | Catalog page (see below) |
| `session/get` | Session meta (status, context, counts, notes revision) |
| `session/overview` | Meta + turns + notes + event/tool counts (`stats`). Turns include `subagentRuns`. Also `backgroundJobs`, `schedules`, and `workflows` (no log or script bodies). |
| `session/timeline` | Paged events (`offset`, `limit`, `type`, `kind`, `query`, `promptIndex`, `aroundIndex`, `atIndex`, `contentChars`). Spawn/finish rows include `childSessionId` and finish stats. |
| `session/turns` | Turn segments plus `subagentRuns` (turn-scoped child runs; `openable` + `childPath`). |
| `session/usage` | Tool / MCP / skill usage |
| `session/diff` | Rewind snapshots or approximate `search_replace` edits (files + hunks + prompt/assistant text) |
| `session/open` | Resolve a session and notify `session/selected` |
| `session/render` | Project a document (`format`: below) |
| `session/follow_up` | Stage or queue the next prompt (`session`, `prompt`, optional `final`) |
| `session/done` | Mark a live session done (`session`) |
| `notes/list` | Notes snapshot (`revision`, schema, notes) |
| `notes/upsert` | Write a note (`expectedRevision`) |
| `notes/delete` | Delete a note (`expectedRevision`) |

### `session/list`

`query` is the catalog language. Bare words match title, id, and label.
Space is AND. Full token list: this schema's `catalogQuery`.

| Token | Matches |
|-------|---------|
| `is:running` `is:awaiting` `is:ending` `is:complete` `is:cancelled` `is:host` `is:eval` | Status or origin. |
| `has:workflows` `has:notes` `has:goals` `has:subagents` `has:tasks` `has:jobs` `has:schedules` `has:plan` `has:errors` `has:failures` `has:diff` `has:git` `has:context` `has:compaction` `has:doom` | Presence on the list row. Countable names take has:name:>=N. |
| `has:workflows:>=N` `has:notes:>=N` `has:goals:>=N` `has:subagents:>=N` `has:tasks:>=N` `has:jobs:>=N` `has:schedules:>=N` `has:errors:>=N` `has:failures:>=N` `has:diff:>=N` `has:compaction:>=N` `has:doom:>=N` | Count on the list row. |
| `in:` | Directory the session was run in. |
| `model:` | Model id substring. |
| `task:` | Task id substring. |
| `turns:` with `>` `>=` `<` `<=` `=` | turnCount. |
| `tools:` with `>` `>=` `<` `<=` `=` | toolCallCount. |
| `events:` with `>` `>=` `<` `<=` `=` | numEvents. |
| `duration:` with `>` `>=` `<` `<=` `=` | Session length (1h, 2d, 30m). |
| `after:` | updatedAt on or after this time (ISO, yesterday, 2d, 2 days ago). |
| `before:` | updatedAt on or before this time (ISO, yesterday, 2d, 2 days ago). |

Optional `limit` and `offset` page the filtered rows; omit
`offset` for the first page. Optional `sinceRevision` matching
the owner’s `revision` returns no rows (`unchanged`). When the
client is behind, the owner may send a `delta` (upserted rows
plus `removed` ids). Result includes `sessions`, `total`,
`matched`, and `revision`. Clients that need the full catalog
drain pages until `matched` on first paint only.

### `session/render`

| `format` | `contentType` | Typical client |
|----------|---------------|----------------|
| `org` (default) | `text/org` | Emacs |
| `markdown` | `text/markdown` | Neovim |
| `json` | `application/json` | Scripts |

### Notes revision

Every `notes/upsert` and `notes/delete` sends `expectedRevision`.
A mismatch is a conflict; the client reloads and retries.
Canonical store is `operator_notes.toml` (host sessions under
`~/.groket/notes/`).
Every note must include a non-empty `source` (who wrote it).
`fields` need not match the configured form schema; extra keys
are stored as sent. The in-app form uses `notes_schema.toml`
and stamps its own source.

## Notifications

| Method | When |
|--------|------|
| `session/selected` | After `session/open` |
| `session/changed` | Session files or status changed. `listChanged` is false when only the trace grew. |
| `notes/changed` | Notes written or deleted |

No `id` on these messages (JSON-RPC notifications).
