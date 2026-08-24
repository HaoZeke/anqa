# Host harness adapters

Groket’s home catalog can list native coding-agent stores on this machine
next to Docker eval traces. Each store is one adapter under
`groket/harness/`. The catalog row carries `harness` and, when the store
records it, `harnessVersion`.

Eval launches stay Grok-only (Docker, follow-up, Done, fork, rewind,
context meter).

## Config

`~/.groket/config.toml`:

```toml
[harness]
host = ["grok"]
```

`H` still means “include the host catalog.” The `host` list is which
adapters that catalog scans. Omit an id to skip that store. Eval traces
under `work/runs/traces` are always Grok and ignore this list.

## Shipped adapters

| Id | Product | Supported version | Store | Catalog path |
|---|---|---|---|---|
| `grok` | Grok Build | 1.0.5 | `~/.grok/sessions/<cwd>/<id>/` | directory |

Supported version is the product we last parsed and tested. A session may
carry a different `harnessVersion` from its own files.

Notes for non-directory stores live under
`~/.groket/notes/<harness>/<session_id>/`.

## Filter

`harness:opencode`, `harness:pi`, `harness:grok`, plus `is:host`.

## Non-goals

Launch, follow-up, Done, fork, Docker isolation, and `grok trace --local`
for non-Grok stores. Live watch of sqlite/jsonl host trees (catalog
rebuild / refresh lists them).

## Adding an adapter

Follow `.grok/skills/harness-adapter-qa/SKILL.md`. `just lint` runs
`scripts/check_harness_adapters.py`.
