# TODO — repo health and Unreleased follow-ups

Contract for humans and agents. Prefer fixing debt **when editing** the
module that owns it; do not open a speculative “cleanup epic.”

Keep this file and ``CHANGELOG.md`` in step. A CHANGELOG Unreleased
bullet that still has open work is listed here; remove the TODO when
that work ships (or when the changelog line is no longer true).

## Unreleased (see CHANGELOG.md)

- Control protocol: no check that a breaking handshake or method change
  bumps ``PROTOCOL_VERSION`` major. Runtime accepts the same major only.
  Add a frozen method/field inventory (or schema) that fails when the
  surface changes without a major bump.
- HUD dropdowns use ``kit.compact_pick`` (12px type, tighter pad) because
  icedtea ``themed_pick_list`` has no compact size. When the next icedtea
  release exposes that size, call it from the HUD and delete the kit
  wrapper.
- HUD Diff Prompt/Assistant tabs are local compact buttons because icedtea
  ``tab_bar`` is a pane strip (12px pad, app-bar wrapper). When icedtea
  has a compact in-pane tab size, use that and delete ``diff_context_tab``.
- HUD markdown uses iced ``markdown::view`` with META/TITLE sizes because
  icedtea ``markdown_view`` paints H1 at PAGE (22) and body at BODY (14).
  When icedtea accepts a compact type scale, call that and delete
  ``hud_md_settings``.
- HUD Turns uses a Diff text chip because icedtea ``Icon`` is a closed
  set of seven names. When 0.9 lets an app pass its own SVG bytes, put
  a Diff icon on that chip.

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

New product features, detector catalogs, and operator-facing polish live
in issues or design docs. Follow-ups for work already in CHANGELOG
Unreleased belong in the section above.
