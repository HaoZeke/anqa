# Supported harnesses

Anqa inspects coding-agent sessions from each product's own store.
One adapter under `anqa/harness/` owns one store. The catalog,
timeline, notes, desktop HUD, and control clients all use the same
`SessionRef` (harness + session id + locator). `adapter_for(path)`
returns the adapter that owns a session. The catalog row carries
`harness` and, when the store records it, `harnessVersion`.

A shipped adapter lists operator-facing sessions from the default
store (or `[catalog.roots]`), binds the locator so any client can
reopen it, builds list meta and a timeline in anqa event names,
watches the store, filters with `harness:<id>`, stores notes, writes
and opens an archive (`E` / `Ctrl+O`), deletes the locator (`x`),
and builds Diff from rewind snapshots when the store wrote them,
else from write and edit tool calls (and the product Diff rows
named below). List Turn comes from that store's own last signal
through `from_last`. `running` is a turn in progress. `—` is no
list status (last user row or bookend). Missing product data stays
unset. Next prompt, end session, rewind, and the context meter
appear when that store writes the files those actions read.

Adding or re-checking an adapter: `.grok/skills/harness-adapter-qa/SKILL.md`.
`just lint` runs `scripts/check_harness_adapters.py`.
`just harness-probe` compares each installed product version to
`supported_version` and samples on-disk record types (no session text).

## Config

The catalog lists every shipped adapter. Narrow with `harness:<id>`.
Opt out of a store in `~/.anqa/config.toml`:

```toml
[catalog]
ignore = ["pi"]
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

Notes for a host adapter session live under
`~/.anqa/notes/<session_id>/`. A directory locator outside those
stores writes in-tree. A file or database locator uses
`~/.anqa/notes/<harness>/<session_id>/`.

## grok — Grok Build

Directory store. Tested **1.0.5**. Catalog path is the session
directory.

Default root: `~/.grok/sessions/<cwd>/<id>/`. The inspectable
trace is `updates.jsonl` (and `events.jsonl` when present).
`signals.json` feeds the context meter. `rewind_points.jsonl`
feeds Diff. `workspace/` and `terminal/` are host planes, not
list events. Subagent session directories stay off the home list;
open them from the parent Summary or Timeline Subagents filter.

List Turn follows Grok live signals on the updates tail. Diff
prefers rewind snapshots, else write / `search_replace` tools.

## opencode — OpenCode

SQLite store. Tested **1.18.25**. Catalog path `opencode:<id>`.

Default file: `~/.local/share/opencode/opencode.db`. Live 1.18
sessions are `event` rows (`session.created.1`, `session.updated.1`,
`message.updated.1`, `message.part.updated.1`) keyed by
`aggregate_id`. The `session` / `message` / `part` tables are the
archive shape (`E` writes that JSON; `open_archive` imports it).

Discover skips rows with `parentID` / `parent_id`. Those children
open from the parent `task` bookend. Timeline is user text,
assistant text, reasoning, and tool parts (`bash`, `edit`, `write`,
`read`, …). List Turn is the last part `state.status`, or a
finished assistant, or archived. Diff prefers the last user
`summary.diffs` (`file`, `patch`, `status`, `additions`,
`deletions`), else edit / write tool input (`filePath`,
`oldString`, `newString`, `content`).

## pi — Pi

JSONL store. Tested **0.84.4**. Catalog path `pi:<id>`.

Default root: `~/.pi/agent/sessions/**/*.jsonl`. One file is one
session. The first row is `type=session` (id, cwd, version). Later
rows are `message` (roles `user`, `assistant`, `toolResult`) and
`model_change`. Title is the first user text. Model is the last
`provider` / `modelId`. List Turn is `running` when the last row is
`toolResult` or an assistant `stopReason` of tool use; otherwise
the assistant stop reason. Diff is edit / write tools on the
timeline.

## claude — Claude Code

JSONL store. Tested **2.1.251**. Catalog path `claude:<id>`.

Default root: `~/.claude/projects/<cwd-encoded>/<uuid>.jsonl`. One
file is the parent session. Children live under
`<uuid>/subagents/*.jsonl` and stay off the home list. Timeline
rows are `user` and `assistant` (`tool_use` / `tool_result`).
Chrome rows (`progress`, `file-history-snapshot`, `queue-operation`,
`system`, `mode`, `cost-state`, `permission-mode`, `last-prompt`,
`atis-latch`, …) do not become timeline events. List Turn is
`running` when the last assistant `stop_reason` is tool use;
otherwise that stop reason. Agent / Task tools emit subagent
bookends. Diff is Edit / Write / StrReplace on the timeline.

## gemini — Gemini CLI

JSONL store. Tested **0.57.0**. Catalog path `gemini:<id>`.

Default root: `~/.gemini/tmp/<project-hash>/chats/session-*.jsonl`.
A conversation is a header line (`sessionId` + `projectHash`, or
`type=session_metadata`) plus `$set` patches, optional
`$rewindTo`, appended `user` / `gemini` / `error` messages, and
`message_update` merges (tokens and the like; the original message
type stays). `kind=subagent` files stay off the home list.
Bootstrap dumps whose only user text is `<session_context>` are
not list rows. Title is `summary` or the first user text. Model is
the last gemini `model`. List Turn is `running` while a tool is
`pending` / `executing` / `in_progress`; a finished gemini row is
`complete`; a last user row is `—`. Diff is write / replace tools
on the timeline (`run_shell_command` is not a file edit).

## antigravity — Antigravity

SQLite conversation plus JSONL transcript. Tested **1.1.22**.
Catalog path `antigravity:<id>`.

Default root: `~/.gemini/antigravity-cli/`. The locator is
`conversations/<uuid>.db` (`trajectory_meta`). The readable
timeline is `brain/<uuid>/.system_generated/logs/transcript.jsonl`
(or `transcript_full.jsonl`). List title, idle flags, and children
come from `conversation_summaries.db`. Model is `agent_name` or a
`gemini-*` id in the conversation blobs. Working directory comes
from `workspace_uris` or `cache/last_conversations.json`. Discover
skips rows with `parent_conversation_id`. Timeline types include
`USER_INPUT`, `PLANNER_RESPONSE`, and tool calls on those rows.
List Turn is `cancelled` when `killed`, `running` when
`not_fully_idle`, else the last row `status`. Diff is write /
replace tools on the timeline.

## copilot — GitHub Copilot CLI

SQLite catalog plus JSONL events. Tested **1.0.82**. Catalog path
`copilot:<id>`.

Default files: `~/.copilot/session-store.db` (`sessions` table:
id, cwd, repository, branch, summary, timestamps) and
`~/.copilot/session-state/<id>/events.jsonl`. Timeline types
include `user.message`, `assistant.message`, `tool.execution_start`
/ result, `subagent.started` / `subagent.completed`,
`assistant.turn_start` / `assistant.turn_end`, `session.shutdown`.
List Turn follows the last of those turn signals. Title is the
session `summary`. Diff is write / replace tools on the timeline.

## codex — Codex

JSONL store. Tested **0.151.0**. Catalog path `codex:<id>`.

Default root: `~/.codex/sessions/**/rollout-*.jsonl`. The session
id is the UUID in the filename. Rows: `session_meta`,
`response_item` (user / assistant messages, `custom_tool_call`,
`function_call`), `event_msg` (`task_started`, `task_complete`,
`turn_aborted`, `item_completed` / `SubAgentActivity`).
`<environment_context>` user blocks are not the title. Model comes
from `turn_context` or `thread_settings_applied`. List Turn is
`task_complete` → complete, `task_started` → running,
`turn_aborted` → cancelled. Child threads are `SubAgentActivity`
bookends. Diff is `apply_patch` in a tool or `exec` argument: the
published Begin Patch grammar (`*** Add File:`, `*** Update File:`,
`*** Delete File:`, `*** Move to:`, `*** Environment ID:`,
`*** End of File`).

## cursor — Cursor

JSONL transcript plus chat meta. Tested **2026.08.25-3e8eec8**.
Catalog path `cursor:<id>`.

Default files: `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl`
and `~/.cursor/chats/*/<id>/meta.json` (title, cwd, timestamps).
Model is the last `modelName` in `chats/*/<id>/store.db` blobs.
Transcript rows are `role=user` / `role=assistant` (`tool_use`
parts) and `type=turn_ended`. List Turn is the last `turn_ended`
status, or complete when that row has no mapped status. Diff is
write / replace tools on the timeline.

## Filter

`harness:grok`, `harness:opencode`, `harness:pi`, `harness:claude`,
`harness:gemini`, `harness:antigravity`, `harness:copilot`,
`harness:codex`, `harness:cursor`.

## Keeping adapters current

A shipped adapter tracks the product we last parsed. When that
product moves, re-read **both** the live store on this machine and
the published parser or grammar, then bump `supported_version` in
the same change.

| Id | Product command | Published source |
|----|-----------------|------------------|
| `grok` | `grok --version` | This repo (session directory + `updates.jsonl`) |
| `opencode` | `opencode --version` | [OpenCode server](https://opencode.ai/docs/server/) (`GET /session/:id/diff`, `summary.diffs`). Live 1.18 `event` types above. |
| `pi` | `pi --version` | On-disk `~/.pi/agent/sessions/**/*.jsonl` |
| `claude` | `claude --version` | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) jsonl types |
| `gemini` | `gemini --version` | [chatRecordingService.ts](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingService.ts) |
| `antigravity` | product about string | On-disk `conversations/*.db` + `brain/*/…/transcript.jsonl` |
| `copilot` | `copilot --version` | On-disk `session-store.db` + `session-state/<id>/events.jsonl` |
| `codex` | `codex --version` | [apply-patch parser.rs](https://github.com/openai/codex/blob/main/codex-rs/apply-patch/src/parser.rs) |
| `cursor` | `cursor-agent --version` | On-disk `agent-transcripts` + `chats/*/<id>/meta.json` |

Probe first: `just harness-probe`. Then extend `parse_timeline` /
`load_meta` for any new key in the same commit as the version bump.
