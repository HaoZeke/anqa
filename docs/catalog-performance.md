# Catalog and session-list cost

The home list is a cheap snapshot. Resolve is a lookup. Parse happens
only for the session the operator opened. The terminal app and the
desktop palette talk to `anqa serve` the same way: `session/list`,
`session/overview`, paged `session/timeline`, `session/turns`,
`session/diff`, `notes/*`. Neither client walks a store to paint the
list or to reopen a row.

## Moments

| Moment | Cost |
|--------|------|
| List (warm) | In-memory snapshot. Matching `sinceRevision` returns no rows. |
| List (cold) | Current rows plus `building` / `incomplete`. First paint does not wait on a full walk. |
| Resolve `harness:id` | Dict lookup to a locator. Never `discover()` / `rglob`. |
| Open | `session/overview` + one `session/timeline` page. One parse on the owner. |
| Live write | `refresh_rows` for that locator. Not a nine-store rebuild. |

Directory stores fill a list row from `summary.json`, `signals.json`,
and turn markers (a tail, not `updates.jsonl`). File stores fill a list
row from the file header and a 64 KiB tail (`list_window`). A list row
never reads a full transcript. The owner keeps `harness:id` → locator
in the warm snapshot; `session/list` never waits on a cold walk.

Membership walks and transcript ingest run in `anqa-core`
(`anqa._core`). Every harness store implements the same typed event
and list-meta contract. A raw event is the original record string.

## Surfaces

Every store that can write the data fills the same overview fields:
turns, `subagentRuns`, `backgroundJobs`, `schedules`, `workflows`,
`stats`, and Diff from rewind or write/edit tools. Missing product data
stays unset. Timeline Subagents is spawn/finish bookends. A child
without its own file is listed and not openable.

## Clients

When a control socket is configured, the terminal browser loads
overview, timeline, turns, Diff, and notes through control methods.
It does not call `require_adapter` / `parse_timeline` /
`load_workspace_diff_doc` on a catalog id. Offline (`--no-socket`) is
the only in-process parse path.
