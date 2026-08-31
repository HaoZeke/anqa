# anqa identity 2.0

Truck-art mark for the anqa name. The bird is the approved painting in
`source/approved.jpg` — not a geometric redraw. The word is Fira Sans
ExtraBold. Open **[guidelines.html](guidelines.html)** for colour, type,
sizes, and use cases.

## Quick use

| Job | File |
|-----|------|
| README / GitHub light | `png/anqa-mark.png` (colour, transparent) |
| README / GitHub dark | `png/anqa-mark-on-dark.png` (cream bird, transparent) |
| HUD search chrome | `png/anqa-mark-64.png` / `png/anqa-mark-on-dark-64.png` |
| Mark on an ink plate | `png/anqa-mark-reverse.png` (solid ink field) |
| Poster / merch | `png/anqa-lockup-stacked.png` |
| Dock / HUD icon | `png/anqa-app-icon-1024.png` (cream plate, square) |
| Favicon | `png/anqa-favicon-32.png` |
| One-colour print | `png/anqa-mark-mono.png` |

`anqa-mark-on-dark.png` is the reverse bird with the ink field knocked
out, so it sits on a dark page. The solid reverse and the dock tiles are
the only filled fields: reverse for print on ink, dock tiles so the OS
icon slot is a cream square.

Wordmark type (when setting the name in product chrome) is **Fira Sans ExtraBold 800**, tracking −0.04 em, lowercase only. Code is **Fira Code**. Files and SIL licenses in `fonts/` (`OFL.txt` for Sans, `FiraCode-OFL.txt` for Code). The decorated letters under the bird live in `source/word-ornament.png`.

Rebuild after replacing `source/approved.jpg` or `source/word-ornament.png` (fonttools, numpy, pillow):

```bash
uv run --with fonttools --with numpy --with pillow python build.py
```
