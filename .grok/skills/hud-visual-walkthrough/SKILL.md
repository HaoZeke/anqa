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

End-to-end **visual and timing** review of the iced desktop HUD. Not a
unit test suite — product eyes on real pixels plus measured wait times.

## Prerequisites

- Host X11 `DISPLAY` (Xephyr nests on it).
- **`Xephyr`**, **`metacity`**, **`wmctrl`**, **`import`** (ImageMagick).
- In-tree `groket-hud` binary (or `groket hud`).
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
| `GROKET_CONTROL_SOCKET` | Override control socket |

## Agent procedure

### 1. Run the harness

From the **groket** repo root:

```bash
# once: sudo apt-get install -y xvfb xdotool scrot

uv run python .grok/skills/hud-visual-walkthrough/scripts/hud_walkthrough.py \
  --session '<session-id-or-title-substring>' \
  --out tmp/hud-walk/latest
```

Read stdout. Note:

- `out_dir` path
- `timings.json` / `steps.jsonl` paths
- `display=` and `backend=xephyr` / `window_mode=true`
- key injection live vs manual
- any step errors

If the script fails before any screenshots, fix environment (serve,
binary, Xvfb) and re-run. Do not invent screenshots.

### 2. Control timings (always)

`timings.json` includes control-plane RPC samples (`session/list`,
`session/overview`, `session/timeline` with and without `promptIndex`).
Report p50-style numbers as **printed** — do not invent.

Each step records **`response_ms`** (chrome ack and/or first visual delta,
**excluding** settle sleep) plus wall `ms` (includes settle). Report
`response_ms` against the &lt;200ms chrome budget; call out body residual when
`visual_ms` is higher after chrome already flipped.

### 3. Visual review (mandatory)

For **each** `*.png` under `out_dir/shots/` in step order:

1. Open with the **read_file** tool (image path). You must actually
   inspect the pixels — do not score from filenames alone.
2. Score against `references/rubric.md` (same directory as this skill).
3. Write one short note per shot: **ok / ugly / broken** + one sentence
   why (clipping, empty wrong pane, wrong tab, unreadable text, etc.).

Do **not** use `image_gen` to “fix” the UI. Use `image_edit` only if
you need to annotate a grab for the report (highlight a defect). Prefer
plain description + path.

### 4. Report

Write `out_dir/REPORT.md` with:

1. **Environment** — branch, serve yes/no, walk `DISPLAY`, `xvfb` yes/no.
2. **Session** — id and title used.
3. **Timings table** — control RPCs + per-step UI ms from `timings.json`.
4. **Shot review** — ordered table: step · file · verdict · notes.
5. **Verdict** — one of:
   - `SHIPPABLE` — no broken; polish nits optional
   - `POLISH` — usable but clear visual debt
   - `BROKEN` — wrong pane, empty when data expected, unusable layout
6. **Top 3 fixes** — concrete product/UI asks (not process).

Paste a short summary into chat (verdict + top fixes + slowest step).
Keep the full report on disk.

### 5. Do not

- Commit screenshots, casts, or `tmp/hud-walk/**` unless the user asks.
- Push a demo binary or force-push.
- Touch the icedtea checkout unless the user explicitly says so.
- Claim pixel perfection without reading every shot.

## Step map (what the script drives)

Keyboard-first (matches HUD bindings):

| Step | Action | Shot name |
|------|--------|-----------|
| 00 | Ensure serve + start HUD | `00-boot` |
| 01 | Summon palette (`Ctrl+Shift+G`) | `01-summon` |
| 02 | Search session substring | `02-search` |
| 03 | Select session (Enter) | `03-overview` (default tab) |
| 04 | Ctrl+2 Turns | `04-turns` |
| 05 | Expand first turn (click or keys as available) | `05-turn-open` |
| 06 | Ctrl+3 Events (empty / pick) | `06-events` |
| 07 | Ctrl+4 Findings | `07-findings` |
| 08 | Ctrl+5 Notes | `08-notes` |
| 09 | Ctrl+1 Overview again | `09-overview-return` |

If xdotool cannot focus the overlay, the script still captures the
**virtual** root window so the agent can see the Xvfb frame.

## Related

- Rubric: `references/rubric.md`
- Harness: `scripts/hud_walkthrough.py`
- Product keys: `groket-hud/README.md`
