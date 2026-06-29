# groket

Interactive [Textual](https://github.com/Textualize/textual) TUI for evaluating
[Grok Build](https://docs.x.ai/grok-build) sessions — timeline, findings, workspace
diff, Docker launches, personas, and pluggable detectors.

## Install

```bash
make install    # project venv + `groket` on PATH (uv tool)
groket          # open the TUI
```

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
| `n` / `e` | Follow-up / end session (when awaiting) |
| `j` | Jobs / logs |
| `F5` / `Ctrl+R` | Refresh list |
| `?` | Help |
| `Ctrl+P` | Command palette (everything for this screen) |
| `q` | Quit |

Footer shows the main shortcuts for the current screen; `?` and `Ctrl+P` cover the rest.

### Session browser

Panes (``[`` / ``]`` or digits **1–5**):

1. **Timeline** — events + detail; `v` view filter, `f` flag event, `/` search  
2. **Summary** — session overview and usage tables  
3. **Diff** — workspace changes  
4. **Findings** — detector hits (`i` also jumps here)  
5. **Report** — analysis panels and flags  

While a multi-turn session is live: follow-up bar, `n` focus prompt, `e` mark done.
`x` deletes the session (confirm twice). `Esc` returns to the session list.

### Runner

Launch Docker evals from a recipe (prompt, models, persona, repo, extras).
**Ctrl+Enter** or **Ctrl+J** launch, **Ctrl+S** save recipe, ``[`` / ``]`` panes.
`Esc` back (asks to discard if the form changed).

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
