# Detection examples (engine)

Detectors are Python (``@detector``); rules are YAML. Install under
``~/.groket/detectors/`` and ``~/.groket/rules/``. The engine merges user YAML
and imports detectors from those directories (and ``~/.groket/plugins/*.py``).

| Pack | Purpose |
|------|---------|
| [`minimal/`](minimal/) | One detector + one rule — onboarding and tests |
| [`starters/`](starters/) | Small worked examples (e.g. repeated shell ``cd``) |
| [`catalog/`](catalog/) | Full reference catalog |

Each pack has the same shape as the install layout:

```text
<pack>/
  detectors/*.py
  rules/*.yaml
```

## Install a pack

```bash
mkdir -p ~/.groket/detectors ~/.groket/rules
cp examples/detection/minimal/detectors/*.py ~/.groket/detectors/
cp examples/detection/minimal/rules/*.yaml ~/.groket/rules/
# or starters / catalog the same way
```

## Concepts

- **Detector** — returns ``list[Match]``
- **Rule** — binds detector + severity / templates
- **Finding** — analysis output (runner applies rules)
- **Composite** (optional YAML) — groups child findings
