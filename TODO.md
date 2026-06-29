# TODO — groket

Keep `uv run pytest tests/` green and
`uv run ruff check groket/ --select F401,F841` clean.

## Open

### PLR complexity (optional)

`uv run ruff check groket/ --select PLR0911,PLR0912,PLR0913,PLR0915,PLR0904` — split
large functions/classes in orchestrator, `app.py`, screens, detectors.
Do not blanket `noqa`.

### Imports

~100+ function-level imports remain (CLI lazy TUI, docker/runner edges).
Hoist when touching a file; prefer top-level per AGENTS.md «Imports».

### UX polish

- Session list: optional flag/finding counts on home table
- More modals (MCP pickers) on `[`/`]` pane pattern if they grow multi-section
