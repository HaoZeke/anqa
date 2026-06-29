# Task catalogs (batch)

Headless Docker evals from a YAML file — optional power feature; the TUI runner
covers interactive launches.

## Schema

- Published: https://indynull.github.io/groket/schemas/tasks.schema.json
- In-repo: `schemas/tasks.schema.json` (`make schema` / `groket batch schema`)

## Examples

[`demo_tasks.yaml`](demo_tasks.yaml) — small smoke tasks (no `repo_url`; empty
`/workspace` unless you uncomment clone fields).

```bash
groket batch validate examples/tasks/demo_tasks.yaml
groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
groket batch run -t examples/tasks/demo_tasks.yaml -i demo-list-workspace -p 2
```

Scaffold into your profile:

```bash
groket gen tasks
# → ~/.groket/tasks/example_tasks.yaml
```

Work root (traces / Docker builds) defaults to `~/.groket/work`; override with
`-P /path/to/work`.
