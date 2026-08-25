# Supported harnesses

Anqa inspects coding-agent sessions from native product stores. Each
store is one adapter under `anqa/harness/`. The catalog, timeline,
notes, desktop HUD, and control clients use the same `SessionRef`
(harness + session id + locator). The catalog row carries `harness`
and, when the store records it, `harnessVersion`.

## What “supported” means

A shipped adapter does all of this:

1. **Discover** operator-facing sessions from the default store (or
   `[catalog.roots]`).
2. **Bind** a locator (directory, transcript file, or database row) so
   any client can reopen the same session.
3. **List meta** and a **timeline** using anqa event type names.
4. **Watch hints** so serve refreshes when the store changes.
5. **Catalog filter** `harness:<id>` on every client.
6. **Notes** on the session: in-tree for a directory locator, or
   `~/.anqa/notes/<harness>/<session_id>/` for a file or database.

Follow-up, Done, rewind, and the context meter appear when that store
writes the files those actions read. Missing product data stays unset.

Adding an adapter: `.grok/skills/harness-adapter-qa/SKILL.md`.
`just lint` runs `scripts/check_harness_adapters.py`.

## Config

The catalog lists every shipped adapter. Narrow with `harness:<id>`.
Opt out of a store in `~/.anqa/config.toml`:

```toml
[catalog]
ignore = ["aider"]
```

A store that is not in its default place gets a root override:

```toml
[catalog.roots]
grok = "~/.grok/sessions"
```

## Shipped

| Id | Product | Supported version | Store | Catalog path |
|----|---------|-------------------|--------|--------------|
| `grok` | Grok Build | 1.0.5 | `~/.grok/sessions/<cwd>/<id>/` | directory |

Supported version is the product we last parsed and tested. A session
may carry a different `harnessVersion` from its own files.

## Filter

`harness:grok` (and each shipped id as more adapters land).
