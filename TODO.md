# TODO — repo health and Unreleased follow-ups

Contract for humans and agents. Prefer fixing debt **when editing** the
module that owns it; do not open a speculative “cleanup epic.”

Keep this file and ``CHANGELOG.md`` in step. Open work against the
first-release product notes lives here; remove the TODO when that
work ships.

## Unreleased (see CHANGELOG.md)

- icedtea `markdown_view` leaves fence ticks on a code block. HUD notes
  paint a whole-field fence with `highlighted_code` until icedtea
  renders fences cleanly.
- icedtea `widget::switch` is a full-width form row. Timeline Tail
  composes `meta` + `toggler` until icedtea has a compact toolbar switch.

## Always on (`just ci` / `just lint`)

- Keep **`just lint`** and **`just test`** green before commit.
- Default lint is ruff (F/E/W/I/UP/T20) + format check + mypy + fluent + typing policy.
- Size / complexity rules are **not** in default lint (too much historical debt).

## Size limits (edit-time)

Documented limits (ruff pylint family): args 5, returns 5, branches 12,
statements 50, public methods 20 — see `AGENTS.md` §4.6.

```bash
just lint-complexity   # report only the size-limit rules on groket/
```

When you **touch** a function or class that already exceeds a limit: split or
simplify that unit in the same change. **No blanket `noqa`.** Do not mass-fix
unrelated hotspots (browser, orchestrator, parser) “because large.”

## Imports

- Prefer **module-level** imports when editing a file.
- Allowed lazy imports: CLI deferring the Textual app for light `--help`;
  dynamic plugin `importlib` loaders (one factual comment each).

## Out of scope here

New product features and operator-facing polish live
in issues or design docs. Follow-ups for work already in CHANGELOG
Unreleased belong in the section above.
