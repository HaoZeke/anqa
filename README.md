# groket

**groket** (grok evaluation tool) — a TUI for evaluating
[Grok Build](https://docs.x.ai/build/overview) agent sessions (timeline,
findings, workspace diffs, Docker launches, personas, and pluggable detectors).

It is **not** the Grok coding agent itself. The agent CLI is `grok`
([install / TUI](https://docs.x.ai/build/overview),
[headless & scripting](https://docs.x.ai/build/cli/headless-scripting#cli)).
Groket runs alongside it: open traces, score runs, and drive Docker-based
evals.

## Install

### From a clone (development)

```bash
make install    # uv sync + install this package as a uv tool (entry point on PATH)
groket          # open the TUI
```

### As a uv tool (users)

Install from GitHub (public repo):

```bash
uv tool install git+https://github.com/indynull/groket
groket
```

Upgrade later (same install source; uv re-resolves the package):

```bash
uv tool upgrade groket
# or: uv tool upgrade --all
uv tool list
```


### Paths

Default work root is `~/.groket/work` (traces, recipes, Docker builds).
Config and extensions live under `~/.groket/` (personas, detectors, rules, plugins).

```bash
groket                    # ~/.groket/work
groket /path/to/work      # that work root (or a traces / session path)
```


## Using the TUI

### Sessions home

Browse past and live runs. Primary keys:

| Key | Action |
|-----|--------|
| `Enter` | Open session (browser) |
| `/` | Search sessions |
| `s` / `Space` | Select row (multi-select) |
| `r` | New run (runner) |
| `C` | Run configs (recipes) |
| `P` | Personas |
| `a` | Analyze selection |
| `n` / `e` | Follow-up / end session (when awaiting); **n** can mark last turn |
| `j` | Jobs / logs |
| `F5` / `Ctrl+R` | Refresh list |
| `?` | Help |
| `Ctrl+P` | Command palette (everything for this screen; includes **Change theme**) |
| `q` | Quit |

Footer shows the main shortcuts for the current screen; `?` and `Ctrl+P` cover the rest.
Preferences (theme, tips, analysis) persist under `~/.groket/config.json`.

### Session browser

Panes (``[`` / ``]`` or digits **1–5**):

1. **Timeline** — events + detail; `v` view filter, `f` flag event, `/` search
2. **Summary** — session overview and usage tables
3. **Diff** — workspace changes
4. **Findings** — detector hits (`i` also jumps here)
5. **Report** — analysis panels and flags

While a multi-turn session is live: follow-up bar (optional **Last turn**), `n` focus prompt, `e` mark done.
`x` deletes the session (confirm twice). `Esc` returns to the session list.

### Runner

Launch Docker evals from a recipe (prompt, models, persona, repo, extras).
**Ctrl+Enter** or **Ctrl+J** launch, **Ctrl+S** save recipe, ``[`` / ``]`` panes.
`Esc` back (asks to discard if the form changed).

Reasoning effort (including **xhigh** and **max**) is selected with the model
token (`model:effort`) and passed to the agent via the CLI; it is not written
into the eval `config.toml`.

### Personas & configs

**P** / **C** open persona and recipe managers. Edit with tabbed panes
(``[`` / ``]`` + digits), **Ctrl+S** save, **Esc** cancel.

## Other CLI commands

| Command | Purpose |
|---------|---------|
| `groket self-test` | Host checks (Docker, Grok auth, work dir) — **no TUI** |
| `groket gen …` | Scaffold detectors, rules, analysis plugins under `~/.groket/` |
| `groket batch …` | Headless Docker runs from a **task YAML** catalog (optional power path) |

```bash
groket self-test
groket gen detector my_check
groket batch validate examples/tasks/demo_tasks.yaml
groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
```

Task catalogs use JSON Schema
[`tasks.schema.json`](https://indynull.github.io/groket/schemas/tasks.schema.json)
(see [`examples/tasks/`](examples/tasks/)). Prefer the TUI runner for interactive work.

## Development

```bash
make install
make lint
make test
make ci      # lint + schema-check + test
```

See [AGENTS.md](AGENTS.md) for architecture, keyboard conventions, and contribution rules.
