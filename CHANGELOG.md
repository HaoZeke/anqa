# Changelog

Notable product state for groket. One first-release section until 0.1.0
is tagged.

## Unreleased

- HUD Timeline event types and tool names use the same small badges as
  session status (color by type family / tool family). Overview, Recent
  session cards, and the browse bar share one status/model/origin row.
- The control JSON-RPC contract (version, methods, notifications) lives
  in `groket/integrations/control_contract.py`. `docs/control.md` and
  `schemas/control.schema.json` are generated from that inventory
  (`just schema`).
- Terminal session open paints Timeline first; Summary and Report fill
  when those panes are opened. Attached control loads the first event
  page, then appends the rest. Timeline search applies after a short
  idle. Live control refresh fetches only new events.
- A Tail switch on a live Timeline follows the last event; off leaves
  the highlight still. The HUD jumps to the last event page so a large
  session is not stuck on the first window.
- Session waits use the toolkit loading readout (Textual LoadingIndicator
  and widget loading; HUD indeterminate progress) instead of a lone
  sentence.
- Opening a Timeline event (terminal and HUD) asks for the owner’s
  50,000-character ceiling, including the paired tool result.
- HUD palette show is a 220 ms ease-out, hide a 180 ms ease-in; tab
  changes fade; opening and closing a session or event push and pop;
  expanders animate height. Motion ticks at display refresh.
- Overlay summon fades the card in (clear window fill + short rise).
- HUD launch stays on Recent; a catalog refresh leaves the list unpicked.
- HUD `?` and the terminal browser footer follow the current pane: Enter
  and list motion on Overview, Turns, and Timeline; turn step on Timeline.
  The HUD footer is keys only. Session running/complete labels are
  badges.
- HUD `?` lists Left and Right next to h and l for Timeline turn step.
- HUD dropdowns (Snapshot, Timeline turn, Filter) use 12px type and
  tighter padding. Diff Prompt/Assistant tabs are compact in-pane
  buttons. Overlay type is 12px for reading, 14px for card titles,
  16px only for the Overview session name. Markdown headings follow
  that scale.
- HUD Turns cards have a Diff chip when that turn has a snapshot.
- An older `groket serve` that lacks a method shows
  `control owner is older · run: groket serve restart` (terminal and HUD).
  The raw error goes to the log.
- Terminal Diff lists rewind snapshots and changed files. Prompt and
  Assistant tabs sit above a files and hunk split; the assistant is
  markdown. Nested paths group as a directory tree. Without rewind
  points it lists approximate `search_replace` edits. `/` fuzzy-finds
  path or hunk text; `h`/`l` step snapshots. The HUD Diff pane uses
  the same layout, with a snapshot dropdown. Switching to the terminal
  Diff tab paints the snapshot already loaded with the session.
- Session walk and `updates.jsonl` keep/skip share one `groket._scan`
  extension (`groket.scan`, setuptools-rust, same install as the HUD
  binary). `GROKET_SCAN=0` uses the Python body. Continuous integration
  runs both.
- Example analysis READMEs point at `.toml` prefs samples.
- `groket config validate` rejects missing or invalid TOML. Load uses defaults when the file is absent or unreadable.
- Continuous integration uploads Python and HUD coverage to Codecov (OIDC).
- README badges: Actions, Codecov, Python 3.13, MIT license.
- Platform wheels and the source distribution build on `main`, tags, and
  workflow dispatch.
- README / HUD dark mark is cream on a transparent field.

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
- Every Actions run builds Linux, macOS, and Windows wheels plus a
  source distribution (artifacts on the run).
- A version tag or manual workflow dispatch uploads those files to
  TestPyPI.

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
- `protocolVersion` is semver (`1.0.0`), independent of the product
  version. Same major keeps a live owner; a major bump is the only
  incompatible handshake change.
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
