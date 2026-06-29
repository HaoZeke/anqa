# Task list examples

Batch never auto-loads tasks. Pass a file explicitly:

```bash
uv run groket batch --tasks examples/tasks/demo_tasks.yaml
# optional: --models <id> --task-id … --category …
```

Validate:

```bash
uv run groket tasks validate examples/tasks/demo_tasks.yaml
```

Scaffold a template into your profile:

```bash
uv run groket gen tasks
# → ~/.groket/tasks/example_tasks.yaml
```
