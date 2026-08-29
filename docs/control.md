# Control

One process owns a per-user Unix socket. The four clients — [terminal
app](../README.md#terminal-app), [Desktop HUD](../README.md#desktop-hud),
[Emacs](../README.md#emacs), and [Neovim](../README.md#neovim-09) — attach
and talk JSON-RPC 2.0. They never bind the socket.

Implementation: `anqa/control/contract.py` (contract),
`anqa/control/server.py` (owner),
`anqa/control/daemon.py` (`anqa serve`),
`anqa/control/client.py` (Python attach).

## Start and stop

```bash
anqa serve                 # foreground (Ctrl-C / SIGTERM)
anqa serve -d              # background; return when the socket accepts
anqa serve stop
anqa serve restart         # stop, then start -d
anqa serve status          # exit 0 if live
```

A second `serve -d` reports already running. Quitting a client leaves the
owner up.

## Socket

Default path: `$XDG_RUNTIME_DIR/anqa/control.sock`, or
`~/.anqa/run/control.sock` when `XDG_RUNTIME_DIR` is unset.

`-s` / `--socket PATH` on `serve` and on every client selects another
path. The HUD also reads `ANQA_CONTROL_SOCKET` (the Python launcher sets
this when it starts the palette).

```bash
anqa serve -d -s /path/to/control.sock
anqa -s /path/to/control.sock
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
| `session/overview` | Meta + turns + notes + event/tool counts (`stats`). Turns include `subagentRuns`. Also `backgroundJobs`, `schedules`, and `workflows` (no log or script bodies). |
| `session/timeline` | Paged events (`offset`, `limit`, `type`, `kind`, `query`, `promptIndex`, `aroundIndex`, `atIndex`, `contentChars`). Spawn/finish rows include `childSessionId` and finish stats. |
| `session/turns` | Turn segments plus `subagentRuns` (turn-scoped child runs; `openable` + `childPath`). |
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
| `is:running` `is:awaiting` `is:ending` `is:complete` `is:cancelled` | Status. |
| `has:workflow` `has:note` `has:goal` `has:plan` `has:subagent` `has:task` `has:job` `has:schedule` `has:error` `has:failure` `has:diff` `has:compaction` `has:doom` `has:git` `has:context` | Presence (has:plan). Counts use the written pair (plans:>=2). |
| `has:workflow` `workflows:>=N` `has:note` `notes:>=N` `has:goal` `goals:>=N` `has:plan` `plans:>=N` `has:subagent` `subagents:>=N` `has:task` `tasks:>=N` `has:job` `jobs:>=N` `has:schedule` `schedules:>=N` `has:error` `errors:>=N` `has:failure` `failures:>=N` `has:diff` `diff:>=N` `has:compaction` `compaction:>=N` `has:doom` `doom:>=N` | Presence and count (written pairs). |
| `in:` | Directory the session was run in. |
| `harness:grok` `harness:opencode` `harness:pi` `harness:claude` `harness:gemini` | Disk adapter id. |
| `model:` | Model id substring. |
| `task:` | Task id substring. |
| `workflows:` with `>` `>=` `<` `<=` `=` | Count of workflows. |
| `notes:` with `>` `>=` `<` `<=` `=` | Count of notes. |
| `goals:` with `>` `>=` `<` `<=` `=` | Count of goals. |
| `plans:` with `>` `>=` `<` `<=` `=` | Count of plans. |
| `subagents:` with `>` `>=` `<` `<=` `=` | Count of subagents. |
| `tasks:` with `>` `>=` `<` `<=` `=` | Count of tasks. |
| `jobs:` with `>` `>=` `<` `<=` `=` | Count of jobs. |
| `schedules:` with `>` `>=` `<` `<=` `=` | Count of schedules. |
| `errors:` with `>` `>=` `<` `<=` `=` | Count of errors. |
| `failures:` with `>` `>=` `<` `<=` `=` | Count of failures. |
| `turns:` with `>` `>=` `<` `<=` `=` | turnCount. |
| `tools:` with `>` `>=` `<` `<=` `=` | toolCallCount. |
| `events:` with `>` `>=` `<` `<=` `=` | numEvents. |
| `duration:` with `>` `>=` `<` `<=` `=` | Session length (1h, 2d, 30m). |
| `diff:` with `>` `>=` `<` `<=` `=` | Diff line count. |
| `compaction:` with `>` `>=` `<` `<=` `=` | Compaction count. |
| `doom:` with `>` `>=` `<` `<=` `=` | Doom-loop warnings. |
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
`~/.anqa/notes/`).
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
