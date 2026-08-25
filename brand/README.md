# anqa identity 2.0

Truck-art mark for the anqa name. The bird is the approved painting in
`source/approved.jpg` — not a geometric redraw. The word is Fira Sans
ExtraBold. Open **[guidelines.html](guidelines.html)** for colour, type,
sizes, and use cases.

## Quick use

| Job | File |
|-----|------|
| Site / README header | `png/anqa-lockup-horizontal.png` |
| Poster / merch | `png/anqa-lockup-stacked.png` |
| Dock / HUD icon | `png/anqa-app-icon-1024.png` |
| Favicon | `png/anqa-favicon-32.png` |
| One-colour print | `png/anqa-mark-mono.png` |

Wordmark type (when setting the name in product chrome) is **Fira Sans ExtraBold 800**, tracking −0.04 em, lowercase only. Files and SIL license in `fonts/`. The decorated letters under the bird live in `source/word-ornament.png`.

Rebuild after replacing `source/approved.jpg` or `source/word-ornament.png` (fonttools, numpy, pillow):

```bash
uv run --with fonttools --with numpy --with pillow python build.py
```
