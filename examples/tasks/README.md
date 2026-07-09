# Task catalogs (batch)

YAML catalogs for headless Docker evals. Optional power path — the TUI runner
covers interactive launches.

## Schema

- Published: https://indynull.github.io/groket/schemas/tasks.schema.json  
- In-repo: `schemas/tasks.schema.json` (`make schema` / `groket batch schema`)

## Files

| File | Purpose |
|------|---------|
| [`demo_tasks.yaml`](demo_tasks.yaml) | Small smoke tasks (empty workspace unless you set `repo_url`) |
| [`superpowers_tasks.yaml`](superpowers_tasks.yaml) | Marketplace **superpowers** plugin exercises |

For superpowers, also install the persona:

```bash
mkdir -p ~/.groket/personas
cp examples/personas/superpowers.json ~/.groket/personas/
```

## Commands

```bash
uv run groket batch validate examples/tasks/demo_tasks.yaml
uv run groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
uv run groket batch run -t examples/tasks/demo_tasks.yaml -i demo-list-workspace -p 2

uv run groket batch validate examples/tasks/superpowers_tasks.yaml
uv run groket batch run -t examples/tasks/superpowers_tasks.yaml -m <model-id>
```

Scaffold:

```bash
uv run groket gen tasks   # → ~/.groket/tasks/example_tasks.yaml
```

Work root defaults to `~/.groket/work` (`-P` to override). Contract:
`make examples-check`.
