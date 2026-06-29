# Canonical detection pack (not shipped in the wheel)

Groket’s engine loads **only** user configuration:

| Path | Content |
|------|---------|
| `~/.groket/detectors/*.py` | `@detector("name")` functions |
| `~/.groket/rules/*.yaml` | Rule + optional composite YAML |
| `~/.groket/plugins/*.py` | Analysis plugins **or** extra detectors |

This directory is a **reference catalog** (former built-in rules/detectors).

## Install into your profile

```bash
mkdir -p ~/.groket/detectors ~/.groket/rules
cp examples/canonical_detection/detectors/*.py ~/.groket/detectors/
cp examples/canonical_detection/rules/*.yaml ~/.groket/rules/
```

Restart groket / run analyze — the Rules screen lists YAML rules; findings are
plain `Finding` objects (`plugin_id` `engine` / `rules`).

## Concepts (one output type)

- **Detector** — Python, returns `list[Match]`
- **Rule** — YAML binds detector + templates/severity
- **Finding** — the only analysis artifact (title, detail, severity, tool_call_ids, …)
- **Composite** (optional YAML) — groups child findings under one parent `Finding`

There is no separate “Issue” / report type in the engine API anymore.
