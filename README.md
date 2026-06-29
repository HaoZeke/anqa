# groket

Evaluate [Grok Build](https://docs.x.ai/grok-build) sessions — interactive TUI,
Docker-based batch runs, and pluggable analysis.

## Install

```bash
make install          # uv sync --group test --group dev
uv run groket         # launch the TUI
```

## Usage

```bash
uv run groket                           # interactive TUI
uv run groket ./my-project              # open a specific work directory
uv run groket batch --tasks tasks.yaml  # Docker batch run
uv run groket audit runs/traces         # detector sweep (CLI)
uv run groket self-test                 # check Docker, auth, paths
uv run groket --help                    # all subcommands
```

## Development

```bash
make lint       # ruff check + format + mypy
make test       # pytest
make ci         # lint + test
```

## Paths

| Root | Default | Role |
|------|---------|------|
| Config home | `~/.groket` | `config.json`, personas, detectors, rules, analysis plugins, cache |
| Work dir | `~/.groket/work` (`GROKET_WORK_DIR` or CLI path) | Traces, run configs, Docker build contexts, batch results |

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

## Environment variables

| Variable | Purpose |
|----------|---------|
| `GROKET_WORK_DIR` | Default work directory (fallback: `~/.groket/work`) |
| `GROKET_GH_TOKEN` | GitHub PAT for eval containers |