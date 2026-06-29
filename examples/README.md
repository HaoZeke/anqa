# Examples

Reference material you **copy** into ``~/.groket/`` (or pass explicitly). Nothing
under ``examples/`` is loaded by default.

Layout mirrors user extension paths:

| Repo path | Install / use |
|-----------|----------------|
| [`detection/`](detection/) | ``~/.groket/detectors/`` + ``~/.groket/rules/`` |
| [`analysis/`](analysis/) | ``~/.groket/plugins/`` + optional ``config.json`` |
| [`tasks/`](tasks/) | ``batch --tasks <path>`` (never auto-loaded) |

Quick starts:

```bash
# Smallest engine example (one detector + one rule)
mkdir -p ~/.groket/detectors ~/.groket/rules
cp examples/detection/minimal/detectors/*.py ~/.groket/detectors/
cp examples/detection/minimal/rules/*.yaml ~/.groket/rules/

# Or the full reference catalog
cp examples/detection/catalog/detectors/*.py ~/.groket/detectors/
cp examples/detection/catalog/rules/*.yaml ~/.groket/rules/

# Analysis plugins (must be listed in config)
mkdir -p ~/.groket/plugins
cp examples/analysis/plugins/session_event_count.py ~/.groket/plugins/
# or try the sample config from the repo (adds examples/analysis/plugins to the path):
uv run groket --config examples/analysis/configs/all-plugins.json

# Batch tasks
uv run groket batch --tasks examples/tasks/demo_tasks.yaml
```

Scaffold stubs instead of copying::

    uv run groket gen detector my_check
    uv run groket gen rule my-rule --detector my_check
    uv run groket gen plugin my_stats --register
    uv run groket gen tasks
