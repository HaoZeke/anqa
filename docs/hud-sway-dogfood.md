# anqa — Sway dogfood checklist

Manual seat checks. **Not** a CI job: GitHub Actions never starts Sway,
Xephyr, or iced on a compositor.

Interactive **100ms** bar stays in the HUD walkthrough skill (Xephyr /
pixel delta). This list is compositor truth: float, summon, focus,
multi-output.

## Preconditions

- Release binary (`anqa hud` / `ANQA_HUD_BIN`)
- `anqa serve -d` (or HUD auto-serve)
- `SWAYSOCK` set
- `include ~/.config/anqa/sway-hud.conf` after `anqa hud --install-desktop`

## Matrix

| Step | Action | Pass |
|------|--------|------|
| 1 | `anqa hud` (no `--show`) | Process + tray; overlay maps once (iced needs a window). With ``sway-hud.conf`` included it is a **floating_con**, not a tile |
| 2 | `anqa doctor` | Wayland line; summon socket listening |
| 3 | `anqa hud --toggle` | Overlay floats, 780×560, not tiled. Compositor-issued `XDG_ACTIVATION_TOKEN` is forwarded and applied with `xdg_activation_v1` |
| 4 | Type immediately after a compositor bind | Spotlight filters without clicking. A terminal `--toggle` has no token and does not steal focus |
| 5 | Esc | Overlay gone; process still running |
| 6 | `--toggle` again | Remap; still floating; focused output |
| 7 | Pointer on second output, toggle | Overlay centers on that output |
| 8 | Pop-out (if used) | Decorated window **can** tile; overlay `app_id` unchanged |
| 9 | Kill HUD, `--toggle` | Starts HUD and shows |
| 10 | `swaymsg -t get_tree` | Overlay node `app_id` is `dev.indynull.anqa-hud.overlay` |

## Not in CI

- Sway / wlroots / seatd
- Real Wayland window or screenshots
- Latency timers
- StatusNotifier host
