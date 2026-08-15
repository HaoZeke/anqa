# Changelog

Notable product state for groket. One first-release section until 0.1.0
is tagged.

## Unreleased

## 0.1.0

First release. Groket evaluates Grok Build sessions: timeline, findings,
workspace diffs, Docker evals, personas, and pluggable detectors /
analysis plugins.

### Install

- `uv tool install --editable .` builds `groket` and `groket-hud` (needs Rust).
- `uv tool install git+https://github.com/indynull/groket` installs from git.
- `uv tool install groket` is the package name on the Python package index.
- `groket --version` (`-V`) prints the product version (`0.1.0`).
- The same version appears on the terminal `?` heading, the desktop
  palette window and `?` sheet, and `groket-hud --version`.
- One product version across the Python package, `groket-hud`, and
  `groket-core`.

### Paths and config

- Config home is `~/.groket` (`config.toml`, personas, detectors, rules,
  plugins, optional `keys.toml`).
- Work root is `~/.groket/work` (`runs/traces/`, recipes, Docker
  contexts, batch results).
- `~/.groket/config.toml` is the only prefs file (terminal app and
  desktop HUD). Schema at the published config schema URL.
- Optional `~/.groket/keys.toml` remaps chords (`groket keys`).

### Sessions

- Eval sessions are Docker launches under `work/runs/traces`.
- Host sessions are native Grok trees at `~/.grok/sessions` (`H` shows
  or hides them).
- Subagent runs stay off the top list; open them from the parent.
- Follow-up (`n`) and Done (`e`) apply while a session is awaiting.
- Fork (`f`) continues an ended session as a new Docker launch.
- Re-run (`R`) launches again from the same recipe fields.

### Terminal app

- `groket` / `groket tui` is the full eval client: session list,
  browser, runner, recipes, personas, analysis, and export.
- Browser panes are Timeline, Summary, Diff, Findings, and Report.
- `y` copies the selection, the finding, or the pane body.
- `E` writes a session bundle under `~/.groket/reports/`.
- Runner launches Docker evals from a recipe (Ctrl+Enter).

### Desktop HUD

- `groket hud` is the summonable session palette (Overview, Turns,
  Timeline, Findings, Notes).
- It runs `groket-hud` from `GROKET_HUD_BIN` or `PATH`; `--rebuild`
  cargo-builds this checkout.
- Default hotkey is Cmd+Shift+G (macOS) / Ctrl+Shift+G (Windows and
  X11). On Wayland bind `groket hud --toggle`.
- `--install-desktop` writes user-local icons and a launcher named
  groket.

### Control

- `groket serve` owns the per-user Unix socket. The four clients
  attach: terminal app, desktop HUD, Emacs, and Neovim.
- Bare `groket` and `groket hud` detach-start serve when the socket is
  free. Quitting a client leaves serve running.
- Emacs opens sessions as Org; Neovim opens them as Markdown.

### Batch, rules, and examples

- `groket batch` runs headless Docker from task YAML
  (`examples/tasks/`).
- `groket rules validate` checks detection rules and composites.
- `groket gen` scaffolds detectors, rules, plugins, and task lists
  under `~/.groket/`.
- Supported packs live in `examples/` (not auto-loaded).

### Development

- `just` is the public development verb (`just lint`, `just test`,
  `just ci`).
- `just bump 0.1.1` sets every product version declaration and
  promotes this file.
- `groket doctor` checks the host (Docker, Grok auth, paths).
