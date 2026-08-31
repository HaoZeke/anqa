# anqa identity 2.1

Truck-art mark for the anqa name. The bird is the approved painting in
`source/approved.jpg` — not a geometric redraw. Lilac wing, mango gold,
cyan eye. The word is Fira Sans ExtraBold. Open
**[guidelines.html](guidelines.html)** for colour, type, sizes, and use
cases.

## Quick use

| Job | File |
|-----|------|
| README / GitHub light | `png/anqa-lockup-stacked.png` (painted lockup) |
| README / GitHub dark | `png/anqa-lockup-stacked-on-dark.png` (cream bird, cream type) |
| HUD search chrome | `png/anqa-mark-64.png` / `png/anqa-mark-on-dark-64.png` |
| Mark on cream / ink plate | `png/anqa-mark-plate.png` / `png/anqa-mark-reverse.png` |
| Poster / merch | `png/anqa-lockup-stacked.png` (and `-on-dark`) |
| Dock / HUD icon | `png/anqa-app-icon-1024.png` / `png/anqa-app-icon-dark-1024.png` |
| Favicon | `png/anqa-favicon-32.png` / `png/anqa-favicon-dark-32.png` |
| One-colour print | `png/anqa-mark-mono.png` / `png/anqa-mark-mono-on-dark.png` |

Every cutout has a regular (black body / ink type) and `-on-dark` (cream body / cream type) pair. Plated tiles use `-dark` on ink. `anqa-mark-on-dark.png` knocks the ink field out so the bird sits on a dark page.

Wordmark type (when setting the name in product chrome) is **Fira Sans ExtraBold 800**, tracking −0.04 em, lowercase only. Code is **Fira Code**. Files and SIL licenses in `fonts/` (`OFL.txt` for Sans, `FiraCode-OFL.txt` for Code). The decorated letters under the bird live in `source/word-ornament.png`.

Rebuild after replacing `source/approved.jpg` or `source/word-ornament.png` (fonttools, numpy, pillow):

```bash
uv run --with fonttools --with numpy --with pillow python build.py
```
