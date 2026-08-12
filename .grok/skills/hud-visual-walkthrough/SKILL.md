---
name: hud-visual-walkthrough
description: >
  Launch groket-hud, open a specific session, click through Overview /
  Turns / Events / Findings / Notes, capture timed screenshots, measure
  UI and control timings, then visually review each grab for polish,
  layout, and broken state using multimodal image inspection. Use when
  the user runs /hud-visual-walkthrough, asks for HUD screenshot
  review, pixel perfection, visual QA, or "is the HUD ugly / broken".
metadata:
  short-description: "HUD timed walkthrough + visual QA"
---

# HUD visual walkthrough

End-to-end **visual** review of the iced desktop HUD, plus control-plane
RPC samples. Product eyes on real pixels — not a unit suite.

## Prerequisites

- Host X11 `DISPLAY` (Xephyr nests on it).
- **`Xephyr`**, **`metacity`**, **`wmctrl`**, **`import`** (ImageMagick).
- In-tree `groket-hud` binary: prefers **`target/release/groket-hud`**, then
  debug, then `groket hud` (document release for snappier walks).
- Control: `groket serve -d` (or the walkthrough starts it).
- Auto keys: **`xdotool`** (or `--manual-keys`).

## Isolation (default)

Matches icedtea `gallery-gif.sh` ideas:

1. **Xephyr** nested display  
2. **metacity** WM inside it  
3. **`GROKET_HUD_WINDOW=1`** — normal managed window (not overlay)  
4. **wmctrl place** + **`import -window root -crop`** of the client  

Does **not** `--restart` the host HUD. Overlay mode stays off unless
`--overlay`.

| Backend | Flag | Notes |
|---------|------|--------|
| Xephyr | `--backend xephyr` (default) | Isolated; window mode paints |
| Host | `--backend host` | Interferes; still works |
| Xvfb | `--backend xvfb` | Often black |

## Inputs

| Arg / env | Meaning |
|-----------|---------|
| Session | Substring or full `sessionId` (required) |
| Out dir | Default `tmp/hud-walk/<timestamp>/` under the repo |
| `--manual-keys` | Do not inject keys; wait for Enter between steps |
| `--backend` | `xephyr` (default), `host`, or `xvfb` |
| `--overlay` | Force overlay mode (skip `GROKET_HUD_WINDOW`) |
| `--display-num N` | Nested display number |
| `--settle-ms N` | Sleep after action before settled screenshot (default 450) |
| `GROKET_CONTROL_SOCKET` | Override control socket |

## Agent procedure

### 1. Run the harness

From the **groket** repo root:

```bash
# Prefer a release HUD for less UI thrash under nested X:
#   (cd groket-hud && cargo build --release)

python3 .grok/skills/hud-visual-walkthrough/scripts/hud_walkthrough.py \
  --session '<session-id-or-title-substring>' \
  --out tmp/hud-walk/latest
```

Read stdout. Note `out_dir`, `timings.json`, `display=`, `backend=`,
`window_mode=true`, key injection live vs manual, step errors.

If the script fails before any screenshots, fix environment (serve,
binary, X) and re-run. Do not invent screenshots.

### 2. Control timings

`timings.json` includes control-plane RPC samples (`session/list`,
`session/overview`, `session/timeline`). Report as printed — do not invent.

Optional `response_ms` on a step is **first pixel delta** from action delivery
(external observation). It is **not** product instrumentation. Settled
`ms` includes settle sleep and is wall-clock only.

### 3. Visual review (mandatory — every shot)

For **each** `*.png` under `out_dir/shots/` in step order:

1. Open with the **read_file** tool (image path). You **must** inspect
   pixels with multimodal vision — **filename-only scoring fails**.
2. Score against `references/rubric.md`.
3. Human-usefulness bar (normal operator):
   - Correct pane selected (Overview / Turns / Events / Findings / Notes)
   - Readable type; primary controls not clipped or buried
   - Buttons, tabs, and chips findable without guesswork
   - No empty wrong pane when data should show
   - No overlapping labels or unusable density
4. Write one short note per shot: **ok / ugly / broken** + one sentence why.

Do **not** use `image_gen` to “fix” the UI. Prefer plain description + path.

### 4. Report

Write `out_dir/VISUAL_REPORT.md` (or `REPORT.md`) with:

1. **Environment** — branch, serve, walk `DISPLAY`, backend, release vs debug.
2. **Session** — id and title.
3. **Timings** — control RPCs from `timings.json`.
4. **Shot review** — ordered table: step · file · verdict · notes (from vision).
5. **Verdict** — `SHIPPABLE` / `POLISH` / `BROKEN`.
6. **Top fixes** — concrete product/UI asks.

Paste a short summary into chat. Keep the full report on disk.

### 5. Do not

- Commit screenshots, casts, or `tmp/hud-walk/**` unless the user asks.
- Push a demo binary or force-push.
- Touch the icedtea checkout unless the user explicitly says so.
- Claim pixel perfection without reading every shot.
- Depend on product env logs or harness-only symbols in the HUD binary.

## Step map

| Step | Action | Shot name |
|------|--------|-----------|
| 00 | Ensure serve + start HUD | `00-boot` |
| 01 | Show window (window mode) | `01-summon` |
| 02 | Search session substring | `02-search` |
| 03 | Select session (Enter) → Overview | `03-overview` |
| 04 | Ctrl+2 Turns | `04-turns` |
| 05 | Expand first turn (click) | `05-turn-open` |
| 06 | Ctrl+3 Events | `06-events` |
| 06b | `]` first Events turn pick | `06b-events-turn-pick` |
| 06c | `]` next turn (if ≥2 turns) | `06c-next-turn` |
| 07 | Ctrl+4 Findings | `07-findings` |
| 08 | Ctrl+5 Notes | `08-notes` |
| 09 | Ctrl+1 Overview | `09-overview-return` |

## Quality

- Script is plain Python 3.12+; keep **ruff** clean (`ruff check` + `ruff format`).
- Prefer release binary; document when debug is used.
- Measurement is external (pixels + control RPC) — no product file I/O hooks.

## Related

- Rubric: `references/rubric.md`
- Harness: `scripts/hud_walkthrough.py`
- Product keys: `groket-hud/README.md`
