# groket

Evaluate [Grok Build](https://docs.x.ai/grok-build) sessions — interactive TUI,
Docker-backed eval runs from the TUI, and pluggable analysis.

## Install

```bash
make install          # project venv + install `groket` on PATH (uv tool)
groket                # launch the TUI (or: uv run groket)
```

## Usage

```bash
groket                            # interactive TUI
groket PATH                       # open work root / traces / session
groket self-test                  # Docker / auth / work dir (no TUI)
groket gen detector|rule|plugin|tasks   # scaffold under ~/.groket/
groket --help
```

## Task YAML schema

Published JSON Schema (GitHub Pages):

- https://indynull.github.io/groket/schemas/tasks.schema.json

Local copy (same content, for offline editors): ``schemas/tasks.schema.json``.
Regenerate with ``make schema``; CI fails if it drifts (``make schema-check``).

Enable **Settings → Pages → Source: GitHub Actions** so the Pages workflow can
deploy (no custom domain required).

## Development

```bash
make lint          # ruff + mypy
make schema-check  # tasks.schema.json matches Pydantic
make test          # pytest
make ci            # lint + schema-check + test
```


## Paths

| Root | Default | Role |
|------|---------|------|
| Config home | `~/.groket` | `config.json`, personas, detectors, rules, analysis plugins, cache |
| Work dir | `~/.groket/work` (CLI path overrides) | Traces, run configs, Docker build contexts, batch results |

```bash
uv run groket                           # work dir: ~/.groket/work
uv run groket /path/to/work             # open that work root
```

## Key concepts

| Concept | What |
|---------|------|
| **Session** | One model run — traces under `<work>/runs/traces/<container>/<session-id>/` |
| **Run config** | Reusable recipe: prompt, models, persona. Auto-saved on launch. |
| **Persona** | Environment profile — Docker image, MCP servers, skills, env vars (`~/.groket/personas`) |
| **Detector** | Rule that flags patterns in traces (install under `~/.groket/detectors` + `rules`) |
| **Finding** | Automated detector hit shown in the Report tab |
| **Flag** | Manual annotation on a timeline event (verdict + note) |

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `?` | Help |
| `Ctrl+P` | Command palette |
| `F5` / `Ctrl+R` | Refresh |
| `j` | Jobs / logs |
| `Esc` | Back / dismiss |
| `q` | Quit |
| `[` / `]` | Previous / next pane |
| `Enter` | Open selection |
| `/` | Search sessions |
| `f` | Flag event (browser) |

