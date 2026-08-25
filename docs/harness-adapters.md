# Host harness adapters

Anqa inspects coding-agent harness sessions. Each store is one adapter
under `anqa/harness/`. The catalog, timeline, notes, desktop HUD, and
control clients use the same `SessionRef` (harness + session id + locator).
The catalog row carries `harness` and, when the store records it,
`harnessVersion`.

Grok Build is the shipped adapter. Follow-up, Done, rewind, and the
context meter come from that store.

## Config

The catalog lists every shipped adapter. Narrow with
`harness:<id>` (and the rest of the query language). Opt out of a store
in `~/.anqa/config.toml`:

```toml
[catalog]
ignore = ["aider"]
```

A store that is not in its default place gets a root override:

```toml
[catalog.roots]
grok = "~/.grok/sessions"
```

## Shipped adapters

| Id | Product | Supported version | Store | Catalog path |
|---|---|---|---|---|
| `grok` | Grok Build | 1.0.5 | `~/.grok/sessions/<cwd>/<id>/` | directory |

Supported version is the product we last parsed and tested. A session may
carry a different `harnessVersion` from its own files.

Directory locators keep notes in the session tree. File or database
locators use `~/.anqa/notes/<harness>/<session_id>/`.

## Filter

`harness:grok`.

## Adding an adapter

Follow `.grok/skills/harness-adapter-qa/SKILL.md`. `just lint` runs
`scripts/check_harness_adapters.py`.
