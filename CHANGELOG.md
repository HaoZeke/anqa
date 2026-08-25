# Changelog

Notable product state for groket. One first-release section until 0.1.0
is tagged. This section is the product as it ships.

## Unreleased

First release. Groket is a session review tool: timeline, notes,
workspace diffs, and a desktop palette. Grok Build is the first shipped
adapter. It does not launch evals.

### Install

- `uv tool install --editable .` builds `groket` and `groket-hud` (needs Rust).
- `uv tool install git+https://github.com/indynull/groket` installs from git.
- `uv tool install groket` is the package name on the Python package index.
- `groket --version` (`-V`) prints the product version (`0.1.0`).
- The same version appears on the terminal `?` heading, the desktop
  palette window and `?` sheet, and `groket-hud --version`.
- One product version across the Python package, `groket-hud`, and
  `groket-scan`.
- Pushes to `main`, version tags, and workflow dispatch build Linux,
  macOS, and Windows wheels plus a source distribution.
- A version tag or manual workflow dispatch uploads those files to
  TestPyPI.

### Paths and config

- Config home is `~/.groket` (`config.toml`, personas, optional `keys.toml`).
- Work root is `~/.groket/work` (`runs/traces/`, recipes, Docker
  contexts, batch results).
- `~/.groket/config.toml` is the only prefs file (terminal app and
  desktop HUD). Default look is `theme = "auto"`: the terminal follows
  the terminal then the desktop; the desktop palette follows the
  system pair and system paper when the OS reports it. Named catalog
  themes and `~/.groket/themes/` pin a colorway on both clients.
- Optional `~/.groket/keys.toml` remaps chords (`groket keys`). Footer
  and `?` use the same action words on both clients.

### Sessions

- Eval sessions are Docker launches under `work/runs/traces`.
- Host sessions are native stores. Every shipped adapter is listed;
  `is:host` and `harness:<id>` filter the list. `[catalog] ignore`
  drops a store; `[catalog.roots]` overrides a non-default path.
- Subagent runs stay off the top list; open them from the parent
  (Summary or Timeline Subagents). Esc returns there.
- Catalog, Timeline, and Turns share a query language (`is:`, `has:`,
  counts, `tool:`, `turn:`, `duration:`, `AND` / `OR`). Tokens live in
  the published control schema. Search applies after 0.28s idle.
- Follow-up (`n`) and Done (`e`) apply while a session is awaiting.
- Fork (`f`) continues an ended session as a new Docker launch.
- Re-run (`R`) launches again from the same recipe fields.
- Every note has a `source`. Extra field keys are stored as sent.
  Report and HUD Notes show the writer badge and the stored fields.

### Terminal app

- `groket` / `groket tui` is the session client: session list,
  browser, runner, recipes, personas, and export.
- Browser panes are Timeline, Summary, Diff, and Report.
- Timeline Filter and Turn stack; Tail follows a live session.
  Opening an event asks for the 50,000-character body.
- Summary and Overview share Session, Tasks, Workflows, Subagents,
  and Stats. Tasks is shells, monitors, and schedules. Enter on a
  bookend or child opens that inspect or session.
- Diff lists rewind snapshots, Prompt/Assistant tabs, and a files/hunk
  split. `/` finds path or hunk text.
- `y` copies the selection or the pane body.
- `E` writes a session bundle under `~/.groket/reports/`.
- Runner launches Docker evals from a recipe (Ctrl+Enter).

### Desktop HUD

- `groket hud` is the summonable session palette (Overview, Turns,
  Timeline, Diff, Notes).
- It runs `groket-hud` from `GROKET_HUD_BIN` or `PATH`; `--rebuild`
  cargo-builds this checkout.
- Default hotkey is Cmd+Shift+G (macOS) / Ctrl+Shift+G (Windows and
  X11). On Wayland bind `groket hud --toggle`.
- `--install-desktop` writes user-local icons and a launcher named
  groket.

### Control

- `groket serve` owns the per-user Unix socket. The four clients
  attach: terminal app, desktop HUD, Emacs, and Neovim.
- Serve arms each catalog root watch off the serve loop. The watch
  covers membership directories and session directories (not each
  plane file). Catalog ``has:goal`` / ``has:plan`` follow the goal
  and plan files on disk.
- Bare `groket` and `groket hud` detach-start serve when the socket is
  free. Quitting a client leaves serve running.
- `protocolVersion` is semver (`1.0.0`), independent of the product
  version. Same major keeps a live owner; a major bump is the only
  incompatible handshake change.
- Emacs opens sessions as Org; Neovim opens them as Markdown.

### Batch and examples

- `groket batch` runs headless Docker from task YAML
  (`examples/tasks/`).
- `groket gen` scaffolds task lists under `~/.groket/`.
- Supported packs live in `examples/` (not auto-loaded).

### Development

- `just` is the public development verb (`just lint`, `just test`,
  `just ci`).
- `just bump 0.1.1` sets every product version declaration and
  promotes this file.
- `groket doctor` checks the host (Docker, Grok auth, paths).
