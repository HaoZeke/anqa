# groket

**groket** (grok evaluation tool) is a [Textual](https://github.com/Textualize/textual)
TUI for evaluating [Grok Build](https://docs.x.ai/build/overview) agent sessions:
timeline, findings, workspace diffs, Docker launches, personas, and pluggable
detectors / analysis plugins.

It is **not** the Grok coding agent. That CLI is `grok`
([install](https://docs.x.ai/build/overview),
[headless](https://docs.x.ai/build/cli/headless-scripting#cli)).
Groket runs alongside it: open traces, score runs, and drive Docker-based evals.

## Install

### Development (clone)

```bash
make install    # .venv + `groket` on PATH via uv tool (editable)
groket          # interactive TUI
```

### Users (uv tool)

```bash
uv tool install git+https://github.com/indynull/groket
groket
uv tool upgrade groket   # later
```

### Paths

| Root | Default | Holds |
|------|---------|--------|
| Config home | `~/.groket` | `config.json`, personas, detectors, rules, analysis plugins, prefs |
| Work root | `~/.groket/work` | `runs/traces/`, recipes, Docker build contexts, batch results |

```bash
groket                      # default work root
groket /path/to/work        # work root, traces tree, or a single session dir
```

## Using the TUI

### Sessions home

Browse past and **live** runs (filesystem watch + periodic read-only refresh).

| Key | Action |
|-----|--------|
| `Enter` | Open session browser |
| `/` | Search sessions |
| `s` / `Space` | Select row (multi-select); `S` select all / clear |
| `r` | New run (runner) |
| `R` | Re-run recipe from the highlighted session (fresh session, same launch fields) |
| `f` | **Fork** an *ended* session into a new interactive multi-turn (see below) |
| `C` | Run configs (recipes) |
| `P` | Personas |
| `d` | Rules (enable/disable detectors) |
| `a` | Analyze selection |
| `I` | Import a native Grok session from `~/.grok/sessions` into this work tree |
| `E` | Export session bundle (tarball) |
| `n` | Follow-up prompt (awaiting sessions; optional **last turn** in the modal) |
| `e` | End session (Done) while awaiting — list shows **ending** until shutdown finishes |
| `x` / `Delete` | Delete session(s) — press twice to confirm |
| `j` | Jobs / logs |
| `F5` / `Ctrl+R` | Refresh |
| `?` | Help |
| `Ctrl+P` | Command palette (theme, tips, analysis, …) |
| `q` | Quit |

Columns include turn status (`running` / `awaiting` / `ending` / `complete`) and
**context** fill from `signals.json` when present. Subagent session dirs are
hidden so each eval is one row. Prefs live in `~/.groket/config.json`.

### Session browser

Panes (`[` / `]` or digits **1–5**):

1. **Timeline** — events + detail; `v` filter, `f` flag, `/` search  
2. **Summary** — overview, usage, turns (context samples when live)  
3. **Diff** — workspace changes  
4. **Findings** — detector / analyzer hits (`i` jumps here)  
5. **Report** — analysis panels and flags  

**Copy from the detail pane:** the TUI owns the mouse, so OS highlight-to-copy
does not work. In the **detail** body (not the timeline table), drag to
highlight a word, line, or region, then **`y`** / **Ctrl+Shift+C** / **Ctrl+C**.
With no selection, **`y`** copies the whole detail pane (OSC 52 clipboard).
tmux over SSH: `set -g set-clipboard on` so OSC 52 reaches the local pasteboard.

Multi-turn live bar: follow-up input, optional **Last turn**, `n` focus, `e` Done.
`x` deletes (double-press). `Esc` back to the list.

**Import Grok session** (`I` on the sessions list, or CLI
``groket import-session PATH``): copy (or ``--link``) a native session from
``~/.grok/sessions/…/<session_id>/`` into ``work/runs/traces/imported/…`` so it
appears next to eval runs. You can also open the host tree read-only with
``groket -P ~/.grok/sessions`` without importing.

**Export as task** (`T` on Runner or Recipes): write a batch tasks YAML
(prompt, repo/local path, persona, models, max_turns, yolo, …). A modal asks
for the file path (default `~/.groket/tasks/<task_id>.yaml`). Run extras
(plugins/skills/MCP) are noted in comments — put those on the persona for
batch, or re-apply as runner extras.

**Export** (`E` in the browser or on the sessions list): writes **one** outer
tarball under `~/.groket/reports/` containing:

- **`grok-trace.tar.gz`** — nested archive that is **only** the output of
  ``grok trace --local $session_id`` (exact CLI bytes; no groket repack).
  Requires ``grok`` on ``PATH``. Layout:
  ``<session_id>/export_metadata.json``, `trace_config.json`, `events.jsonl`,
  `chat_history.jsonl`, `summary.json`, …
- **`run/`** — launch recipe / prompt / config / turn gate (groket extras)
- **`analysis/`** — cached analysis results when present (``*.json``) plus a
  markdown report per analyzer (``*.md``): uses each plugin’s
  ``artifacts["report"]`` when available, otherwise summary + findings)

### Multi-turn and forking

| Path | When | What happens |
|------|------|----------------|
| **Live follow-up** (`n` / browser bar) | Session container still running and awaiting | Same Grok session; host writes the next prompt on the turn gate |
| **End** (`e`) | Awaiting | Mark done; status may show **ending** until the container finishes |
| **Fork** (`f` / palette **Fork session**) | Session has *ended* (chat/events present) | New Docker launch: parent history seeded under `.groket-resume-seed/`; first turn is `grok --resume --fork-session` (new session id). Type the continuation prompt; multi-turn on. The browser **timeline inherits parent turns** (Grok often writes only the new turn into the child dir). **Workspace** usually lives under `runs/checkouts/<container>/` (git clone or CoW from parent) and is bind-mounted as `/workspace`. If the recipe has **`repo_path`**, fork remounts that same host directory (live tree; no CoW). On **reflink** filesystems (btrfs, xfs) managed-checkout forks use CoW (`cp --reflink=always`). On **ext4** (no reflink) groket does **not** silently full-copy multi‑GB trees: it falls back to a fresh git clone / empty workspace (parent dirt not preserved). Set `GROKET_ALLOW_FULL_WORKSPACE_COPY=1` to force a full recursive copy when you need dirt on non-reflink volumes. |
| **Re-run** (`R`) | Any listed session | New launch from the same recipe fields — **not** a conversation continue |

While a session is still live, use follow-up (`n`), not fork. Each fork is an
independent branch of the parent snapshot (parent files are not overwritten).

**Fork is a new container**, not the same disk layout as the parent eval:

- **Persona MCP / plugins / skills** are re-applied from the runner prefill.
  The launch recipe (`run.json`) is written on the **traces volume at container
  start** (not only when the container exits), so interactive / still-running
  sessions and re-forks keep `persona_id`, plugins, skills, and MCP. Prefill
  also falls back to the **fork parent seed** recipe when the child dir has
  none. Plugins are **staged on the host** into `*.stage/plugins/checkouts/`
  and installed in-container with `grok plugin install --trust` only (no git
  inside the image). Plugin skill packs are also staged under
  `~/.grok/skills/<id>/` so skill reads stay stable when Grok’s install dir is
  a new `src-<hash>/`.
- Parent chat that hardcodes `/root/.grok/installed-plugins/<old-id>/…` is
  bridged by recreating those directory names as symlinks after install
  (`RESUME_PLUGIN_DIR_ALIASES`).
- **Share** is a new `grok share <child-id>` for the forked session (parent
  share JSON is not copied into the seed). The TUI shows a share as ready only
  when the last write has a URL and an empty error field.

**Batch / task YAML:**

| Field | Role |
|-------|------|
| `repo_url` / `repo_branch` | Git clone into a managed checkout under `runs/checkouts/` |
| `repo_path:` | Host directory bind-mounted as `/workspace` (live tree; **no** clone/CoW). Single model only. Agent edits that directory; root-owned new files are reclaimed for the host user on exit |
| `yolo:` | When true, launch with `grok --yolo` (default false → `--always-approve`) |
| `turns:` | Scripted follow-ups after the primary `prompt` on a **new** session |
| `resume_session_dir:` | Host path to an *ended* session dir — **fork** (same as TUI `f`): seed history, first turn `grok --resume --fork-session`; `prompt` is the continuation; optional `turns` after that |
| `resume_session_id:` | Optional parent id (defaults to the directory basename) |
| `max_turns:` | Grok `--max-turns` per prompt (default **50**); also allowed under `defaults:` |

Example: see commented `demo-fork-resume` / `repo_path` notes in [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml).

### Runner

Docker evals from a recipe (prompt, models, persona, repo, extras).
**Ctrl+Enter** / **Ctrl+J** launch, **Ctrl+S** save, `[` / `]` panes.
`Esc` back (discard prompt if dirty).

**Workspace:** optional git **Repository** URL (cloned into `runs/checkouts/`),
or **Local path** (bind-mount that host directory as `/workspace` — edits are
permanent; one model only). Managed checkouts are chowned to the host user on
exit; **local path** only reclaims **root-owned** paths the agent created (so
your existing tree is not fully re-chowned).

**Permissions:** by default the agent runs with ``--always-approve``. Check
**YOLO mode** on the runner (or set task YAML ``yolo: true``) to use
``grok --yolo`` instead (same auto-approve family; sets `yolo` in container
config / session telemetry).

Pick reasoning effort with the model token (`model:effort`, e.g. `…:xhigh` or
`…:max`) when selecting models for a run.

**Max turns** (Runtime pane, default **50**) sets Grok `--max-turns`: how many
agent tool/plan steps are allowed **per prompt** (not host multi-turn count).
Batch task YAML may set `max_turns:` (or document-level `defaults.max_turns`).

Runtime shows **persona + effective** plugins / skills / MCP / inline skills /
env (persona base merged with this-run extras). Extras pane still has the full
breakdown.

### Personas & configs

**P** / **C** — tabbed editors (`[` / `]` + digits), **Ctrl+S** save, **Esc** cancel.
Env key/value editing and Grok marketplace plugins/skills/MCP are supported.

## CLI (non-TUI)

| Command | Purpose |
|---------|---------|
| `groket self-test` | Host checks (Docker, Grok auth, paths) — no TUI |
| `groket gen …` | Scaffold detectors, rules, analysis plugins, tasks under `~/.groket/` |
| `groket batch …` | Headless Docker runs from a task YAML catalog |
| `groket rules …` | Validate rules / composites YAML |
| `groket import-session PATH` | Copy (or `--link`) a `~/.grok/sessions/…` dir into `work/runs/traces/imported/` |

```bash
groket self-test
groket gen detector my_check
groket rules validate examples/detection/minimal/rules/demo_rule.yaml
groket batch validate examples/tasks/demo_tasks.yaml
groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
# Import a host Grok session so it appears in the default TUI work tree:
groket import-session ~/.grok/sessions/%2Fpath%2Fto%2Fcwd/<session-id>
groket import-session --link -P ~/.groket/work ~/.grok/sessions/…/<session-id>
```

Schemas (editors / Pages):

- Tasks: https://indynull.github.io/groket/schemas/tasks.schema.json  
- Rules: https://indynull.github.io/groket/schemas/rules.schema.json  

Prefer the TUI runner for interactive work.

## Examples (reference packs)

Supported, CI-gated packs under [`examples/`](examples/README.md) — copy into
`~/.groket/` or pass paths. **Not** auto-loaded.

| Goal | Start here |
|------|------------|
| Smallest detector + rule | [`examples/detection/minimal/`](examples/detection/minimal/) |
| Full detector catalog | [`examples/detection/catalog/`](examples/detection/catalog/) |
| Analysis plugin | [`examples/analysis/plugins/session_event_count.py`](examples/analysis/plugins/session_event_count.py) |
| LLM analysis plugin | [`examples/analysis/plugins/llm_instruction_check.py`](examples/analysis/plugins/llm_instruction_check.py) |
| Batch tasks | [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml) |

```bash
make examples-check   # hard gate: schemas, imports, rule↔detector links
```

## Development

```bash
make install
make lint            # ruff, mypy, fluent + typing policy scripts
make test            # pytest (daemon-free)
make examples-check  # examples/ contract
make test-cov        # pytest + coverage report
make ci              # lint + schema-check + examples-check + test
```

Conventions, architecture, and agent rules: [AGENTS.md](AGENTS.md).
