# Changelog

Notable product state for groket. One first-release section until 0.1.0
is tagged.

## Unreleased

### Feature

- Session, Timeline, and Notes lists share one selected-row face:
  click and `j`/`k` move the same highlight. The terminal cursor
  uses the theme primary wash (readable without zebra stripes).
- Footer and `?` help use the same action words on both clients
  (Follow-up, Filter, Recipes, Open, Go). Recipes launch selected is
  `L`. Catalog search tokens in `?` come from the published schema and
  wrap as a short list on the terminal and the desktop palette.
- Default look follows the host (`theme = "auto"`): the terminal app
  picks `ansi-light` / `ansi-dark` from the terminal (`COLORFGBG`, then
  the desktop). The desktop palette uses the system light/dark pair
  and system paper when the OS reports it. Named catalog themes
  (Catppuccin, Nord, GitHub, Everforest, Kanagawa, and others) and a
  drop-in `~/.groket/themes/` file pin a colorway on both clients.
  Status uses success / caution / danger / quiet.
- Timeline search accepts `tool:`, `turn:`, `user:`, and `duration:`
  (same seconds as Dur) as well as `is:` / `has:error`. Turns and
  Timeline show last-token hints. The terminal search box is a
  full-width row under Filter / Turn / Tail. Typing in those boxes
  keeps the caret (no remount / loading flash).
- Catalog, Timeline, and Turns search apply after 0.28s idle on both
  clients. The palette sends the committed query to `groket serve`.
  The terminal box matches loaded events (the first page, then the
  rest once they arrive). Structured tokens do not wait on full-text.
  Unfinished `AND` / `is:err` keep the last complete clause. A
  zero-hit desktop search shows an empty list, not a spinner.
  `AND` clause order does not change the match.
- A failed Timeline tool keeps its family color. Failure is a mark after
  the name (terminal warning sign, desktop icedtea error icon).
- Search tokens for the current list are on the box tooltip. Last-token
  completions still appear while you type. `?` no longer lists every token.
- Catalog search understands a query language (`is:host`,
  `has:plan`, `plans:>=2`, `errors:>=5`, `goals:2`, `in:~/path`,
  `AND` / `OR`). `has:` is presence. Counts use a written pair
  (`has:plan` / `plans:>=2`); the schema lists both words. The same
  language filters Turns and Timeline. `has:goal` is distinct goal ids
  in the trace. `has:plan` is times plan mode was entered. Session
  stats stay `turns:` `tools:` `events:` `duration:`. Tokens live in
  the published control schema (`catalogQuery`). `?` lists them. Host
  sessions always load. `in:` is the directory the session was run in.
  Known tokens color in the search box.
- Session Overview and Summary share Session, Tasks, Workflows,
  Subagents, and Stats tabs (click the strip). Session is the glance.
  Tasks is shells, monitors, and schedules. Timeline filter Background
  / Workflows. Enter on a job bookend shows the host ``terminal/`` log
  (up to 50,000 characters). A workflow child or subagent opens that
  session. Failed workflows and jobs list on Summary.
- Workflow inspect uses the same facts on TUI and HUD: Asked,
  Happened, Failed, and an Agents list. Child status is `complete`,
  `failed`, `cancelled`, or `running`. A child without a session
  directory is dim and does not open. Overview glance children include
  the session path when it exists.
- `session/overview` includes event-type and tool counts. HUD and TUI
  Stats read those fields for the whole session.
- Timeline Filter and Turn stack. Flags paint on the row.
  Live append keeps the filter. HUD turn pick keeps Filter and search.
- Diff lists rewind snapshots, Prompt/Assistant tabs, and a files/hunk
  split on both surfaces. ``/`` finds path or hunk text.
- Live Timeline has a Tail switch on the trailing end of the Filter
  row (compact label + track on both clients). Opening an event asks
  for the 50,000-character body, including the paired tool result.
- Report keeps flags and notes. Session export writes the trace, notes,
  and flags.
- Every note write needs a `source`. Clients may send fields that are
  not in the in-app schema; Report and HUD Notes show those fields and
  the source. A new note uses `notes_schema.toml`. Editing a note also
  shows extra stored fields as free-text.

### Chore

- groket no longer ships an analyzer or a rules engine. The
  ``groket analyzer`` command, plugin pipeline, detectors, rules YAML,
  and the Rules screen are gone.
- Desktop palette uses icedtea 0.13. Stats table passes a scroll id so
  clip jumps stay on the body.
- HUD notes form uses a pick list for one-of schema fields (severity)
  and filter chips for many-select. Tab / Shift+Tab walk the text
  fields while composing; Ctrl+Tab or Ctrl+1–5 still change panes.
- HUD uses icedtea 0.13.0: search, badges, tabs, selectable bodies,
  Diff hunks, an F12 Look drawer, and `virtual_clip` pixel scroll on
  Turns, Timeline, Overview lists, Stats, and the session picker.
  Keyboard jumps use `scroll_to` on the named clip. A pixel wheel
  redraws in the clip; layout runs when the mounted range changes.
- HUD Turns and Timeline closed rows use the same title-plus-badge
  tile as Recent.
- HUD growing lists scroll on icedtea `virtual_column` / `data_table`:
  Recent, closed Turns, closed Timeline, Overview Tasks / Workflows /
  Subagents, Overview Stats, Notes cards, and workflow-event
  agent children. One-document panes stay `themed_scroll`: open
  Timeline event body (Asked / Happened), Diff hunk, Overview Session.
- TUI Session glance puts status, model, Host or Eval, and duration on
  one badge row. Last-turn says `complete`.
- HUD loading uses the same spinner overlay for the catalog, an opening
  session, Timeline, and Stats.
- Serve watches membership directories and four session files with
  watchfiles. Workspace is not subscribed. An open session tails new
  ``updates.jsonl`` bytes. Catalog warms once at start. The HUD list
  follows socket notifications.
- Clicking a Turns card focuses that turn. Overview footer stays on
  one row.
- Host catalog list uses a stamp-gated snapshot. ``groket export-host``
  writes that snapshot.
- Control methods and notifications generate from
  ``control_contract.py`` (``just schema``).
- Session walk uses ``groket._scan``. ``GROKET_SCAN=0`` uses the
  Python body.
- ``examples/keys`` ships with the other reference packs.
- Removing a control method or handshake field without a protocol
  major bump fails the contract inventory check.
- Continuous integration uploads Python and HUD coverage to Codecov.
- Platform wheels and the source distribution build on ``main``,
  tags, and workflow dispatch.

## 0.1.0

First release. Groket evaluates Grok Build sessions: timeline,
workspace diffs, Docker evals, and personas.

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
  desktop HUD). Schema at the published config schema URL.
- Optional `~/.groket/keys.toml` remaps chords (`groket keys`).

### Sessions

- Eval sessions are Docker launches under `work/runs/traces`.
- Host sessions are native Grok trees at `~/.grok/sessions` (always
  loaded; `is:host` filters the list).
- Subagent runs stay off the top list; open them from the parent.
- Follow-up (`n`) and Done (`e`) apply while a session is awaiting.
- Fork (`f`) continues an ended session as a new Docker launch.
- Re-run (`R`) launches again from the same recipe fields.

### Terminal app

- `groket` / `groket tui` is the full eval client: session list,
  browser, runner, recipes, personas, and export.
- Browser panes are Timeline, Summary, Diff, and Report.
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
