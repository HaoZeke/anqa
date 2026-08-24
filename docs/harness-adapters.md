# Host harness adapters

Groket inspects coding-agent harness sessions. Each store is one adapter
under `groket/harness/`. The catalog, timeline, notes, desktop HUD, and
control clients use the same `SessionRef` (harness + session id + locator).
The catalog row carries `harness` and, when the store records it,
`harnessVersion`.

Grok Build is the shipped adapter. Docker evals, follow-up, Done, fork,
rewind, and the context meter are capabilities of that store.

## Config

`~/.groket/config.toml`:

```toml
[harness]
host = ["grok"]
```

`H` includes the host catalog. The `host` list is which adapters that
catalog scans. Omit an id to skip that store. Eval traces under
`work/runs/traces` are sessions this tool launched and ignore this list.

## Shipped adapters

| Id | Product | Supported version | Store | Catalog path |
|---|---|---|---|---|
| `grok` | Grok Build | 1.0.5 | `~/.grok/sessions/<cwd>/<id>/` | directory |

Supported version is the product we last parsed and tested. A session may
carry a different `harnessVersion` from its own files.

Directory locators keep notes in the session tree. File or database
locators use `~/.groket/notes/<harness>/<session_id>/`.

## Filter

`harness:grok` and `is:host`.

## Adding an adapter

Follow `.grok/skills/harness-adapter-qa/SKILL.md`. `just lint` runs
`scripts/check_harness_adapters.py`.
