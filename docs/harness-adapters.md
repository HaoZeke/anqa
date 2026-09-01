# Supported harnesses

Anqa inspects coding-agent sessions from native product stores. Each
store is one adapter under `anqa/harness/`. The catalog, timeline,
notes, desktop HUD, and control clients use the same `SessionRef`
(harness + session id + locator). `adapter_for(path)` returns the
adapter that owns a session. The catalog row carries `harness`
and, when the store records it, `harnessVersion`.

## What “supported” means

A shipped adapter does all of this:

1. **Discover** operator-facing sessions from the default store (or
   `[catalog.roots]`). The home list must include those rows. File
   and database locators are collected by `discover`; directory
   locators by the session-directory walk.
2. **Bind** a locator (directory, transcript file, or database row) so
   any client can reopen the same session.
3. **List meta** and a **timeline** using anqa event type names.
4. **Watch hints** so serve refreshes when the store changes.
5. **Catalog filter** `harness:<id>` on every client.
6. **Notes** on the session: host adapter stores write
   `~/.anqa/notes/<session_id>/`; a directory locator outside those
   stores writes in-tree; a file or database locator uses
   `~/.anqa/notes/<harness>/<session_id>/`.
7. **Write archive** (`write_archive`) so `E` can nest the native
   session files through `export_session_bundle` for a directory or
   a `harness:<id>` catalog path. Anqa adds notes, summary, and the
   manifest. **Open archive** (`open_archive`) is the inverse:
   materialize that native archive under `~/.anqa/imports/<id>/` and
   return a `SessionRef`.
8. **Detail / live stamps** (`load_detail`, `timeline_stamp`,
   `trace_mtime`, `updates_size`, `list_turn_outcome`) so the catalog
   and browser do not import a store parser. List status comes from
   that store’s own live signals through ``from_last``. ``running``
   is a turn in progress. ``—`` is no list status (last user row or
   bookend). Never default a new adapter to complete.
9. **Delete** (`delete_session`) so `x` on the session list removes
   the native locator (directory, transcript file, or database row).
10. **Scheduler blocks** (`scheduler_state`, `reported_completion_ids`)
   when the store has durable schedules.
11. **Diff** is rewind snapshots when that store writes them, else
   write and edit tool calls on the timeline (`edit`, `write`,
   `search_replace`, `Edit`, `StrReplace`, and the same family).
   OpenCode also reads `summary.diffs` (`file` / `patch` / `status`).
   Codex `apply_patch` is the published Begin Patch grammar.

Next prompt, end session, rewind, and the context meter appear when
that store writes the files those actions read. Missing product data
stays unset.

Adding an adapter: `.grok/skills/harness-adapter-qa/SKILL.md`.
`just lint` runs `scripts/check_harness_adapters.py`.
`just harness-probe` compares each installed product version to
`supported_version` and samples on-disk record types (no session text).

## Keeping adapters current

A shipped adapter tracks the product we last parsed. When that product
moves, re-read **both** the live store on this machine and the published
parser or grammar, then bump `supported_version` in the same change.

| Id | Product command | Published source |
|----|-----------------|------------------|
| `grok` | `grok --version` | This repo (`docs/harness-adapters.md`) |
| `opencode` | `opencode --version` | [OpenCode server](https://opencode.ai/docs/server/) (`GET /session/:id/diff`, `summary.diffs`). Live 1.18 writes `event` rows (`session.created.1`, `message.updated.1`, `message.part.updated.1`); `session` / `message` / `part` stay the archive shape. |
| `pi` | `pi --version` | On-disk `~/.pi/agent/sessions/**/*.jsonl` |
| `claude` | `claude --version` | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) jsonl types |
| `gemini` | `gemini --version` | [chatRecordingService.ts](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts) (`$set`, `$rewindTo`, `session_metadata`, `message_update`) |
| `antigravity` | product about string | On-disk `~/.gemini/antigravity-cli/conversations/*.db` |
| `copilot` | `copilot --version` | On-disk `~/.copilot/session-store.db` |
| `codex` | `codex --version` | [apply-patch parser.rs](https://github.com/openai/codex/blob/main/codex-rs/apply-patch/src/parser.rs) (`*** Begin Patch` grammar) |
| `cursor` | `cursor-agent --version` | On-disk `~/.cursor/projects/*/agent-transcripts` |

Probe first: `just harness-probe`. Then extend `parse_timeline` / `load_meta`
for any new key in the same commit as the version bump.

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
opencode = "~/.local/share/opencode/opencode.db"
pi = "~/.pi/agent/sessions"
claude = "~/.claude/projects"
gemini = "~/.gemini/tmp"
antigravity = "~/.gemini/antigravity-cli"
copilot = "~/.copilot"
codex = "~/.codex/sessions"
cursor = "~/.cursor"
```

## Shipped

| Id | Product | Supported version | Store | Catalog path |
|----|---------|-------------------|--------|--------------|
| `grok` | [Grok Build](https://docs.x.ai/build/overview) | 1.0.5 | `~/.grok/sessions/<cwd>/<id>/` | directory |
| `opencode` | [OpenCode](https://opencode.ai) | 1.18.25 | `~/.local/share/opencode/opencode.db` | `opencode:<id>` |
| `pi` | [Pi](https://pi.dev) | 0.84.4 | `~/.pi/agent/sessions/**/*.jsonl` | `pi:<id>` |
| `claude` | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | 2.1.251 | `~/.claude/projects/<cwd>/<uuid>.jsonl` | `claude:<id>` |
| `gemini` | [Gemini CLI](https://github.com/google-gemini/gemini-cli) | 0.57.0 | `~/.gemini/tmp/<project>/chats/session-*.jsonl` | `gemini:<id>` |
| `antigravity` | [Antigravity](https://antigravity.google/docs/cli/overview) | 1.1.22 | `~/.gemini/antigravity-cli/conversations/<uuid>.db` | `antigravity:<id>` |
| `copilot` | [GitHub Copilot](https://docs.github.com/en/copilot) | 1.0.82 | `~/.copilot/session-store.db` | `copilot:<id>` |
| `codex` | [Codex](https://github.com/openai/codex) | 0.151.0 | `~/.codex/sessions/**/rollout-*.jsonl` | `codex:<id>` |
| `cursor` | [Cursor](https://cursor.com) | 2026.08.25-3e8eec8 | `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl` | `cursor:<id>` |

Supported version is the product we last parsed and tested. A session
may carry a different `harnessVersion` from its own files.

## Filter

`harness:grok`, `harness:opencode`, `harness:pi`, `harness:claude`,
`harness:gemini`, `harness:antigravity`, `harness:copilot`, `harness:codex`,
`harness:cursor`.
