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

Multi-turn live bar: follow-up input, optional **Last turn**, `n` focus, `e` Done.
`x` deletes (double-press). `Esc` back to the list.

### Multi-turn and forking

| Path | When | What happens |
|------|------|----------------|
| **Live follow-up** (`n` / browser bar) | Session container still running and awaiting | Same Grok session; host writes the next prompt on the turn gate |
| **End** (`e`) | Awaiting | Mark done; status may show **ending** until the container finishes |
| **Fork** (`f` / palette **Fork session**) | Session has *ended* (chat/events present) | New Docker launch: history seeded from the parent; first turn is `grok --resume <parent> --fork-session` so the branch gets a **new Grok session id**. Type the continuation as the runner prompt; multi-turn is on. Workspace is a fresh clone/setup (conversation only is resumed). |
| **Re-run** (`R`) | Any listed session | New launch from the same recipe fields — **not** a conversation continue |

While a session is still live, use follow-up (`n`), not fork. Each fork is an
independent branch of the parent snapshot (parent files are not overwritten).

**Batch / task YAML:**

| Field | Role |
|-------|------|
| `turns:` | Scripted follow-ups after the primary `prompt` on a **new** session |
| `resume_session_dir:` | Host path to an *ended* session dir — **fork** (same as TUI `f`): seed history, first turn `grok --resume --fork-session`; `prompt` is the continuation; optional `turns` after that |
| `resume_session_id:` | Optional parent id (defaults to the directory basename) |

Example: see commented `demo-fork-resume` in [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml).

### Runner

Docker evals from a recipe (prompt, models, persona, repo, extras).
**Ctrl+Enter** / **Ctrl+J** launch, **Ctrl+S** save, `[` / `]` panes.
`Esc` back (discard prompt if dirty).

Pick reasoning effort with the model token (`model:effort`, e.g. `…:xhigh` or
`…:max`) when selecting models for a run.

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

```bash
groket self-test
groket gen detector my_check
groket rules validate examples/detection/minimal/rules/demo_rule.yaml
groket batch validate examples/tasks/demo_tasks.yaml
groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
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
