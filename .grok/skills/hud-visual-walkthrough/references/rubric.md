# HUD visual rubric

Score each screenshot. Prefer evidence over taste essays.

## Verdicts per shot

| Tag | Meaning |
|-----|---------|
| **ok** | Usable, hierarchy clear, no obvious defect |
| **ugly** | Usable but dense, uneven gaps, weak contrast, or chrome noise |
| **broken** | Wrong state, empty when data expected, clipped text, overlapping controls, unreadable |

## Pixel / layout checks

1. **Edges** — content inset from card edge (≈12–16px chrome rhythm). No text flush to border.
2. **Clipping** — labels, chips, badges, or buttons cut off; scroll body not under footer.
3. **Alignment** — session rail vs detail; filter row controls baseline-aligned.
4. **Density** — 8px-ish spacing rhythm; more space between sections than within a card.
5. **Contrast** — muted meta still readable; danger/status not pure decoration without meaning.
6. **Focus** — when a pane is active, primary content is obvious (not a blank void with chrome only).

## Product-state checks (validity)

1. **Tab match** — filename/step says Overview/Turns/Events/… and the tab chrome matches.
2. **Session match** — title/id in the UI is the intended session (not empty catalog).
3. **Overview** — meta, status badge, path/events counts present when session has data.
4. **Turns** — closed cards show user prompt + marks; open card shows stats (duration, tools) and assistant when complete.
5. **Events** — empty search-all copy when no turn/query; turn-scoped list when a turn is selected; not a silent blank.
6. **Findings / Notes** — empty states use icedtea empty/status patterns, not raw error dumps.
7. **Broken signals** — “control socket down”, panic, zero-size window, all-black frame = **broken**.

## “Highlight polished” bar

Polished means **calm density**: clear who/what hierarchy, consistent chip/badge language, no competing borders/glows, footer and filters quiet. Flag as **ugly** if it feels like a debug dump or terminal chrome pasted into a card.

## Timing interpretation

| Class | Guidance |
|-------|----------|
| Control RPC &lt; 100ms | Fine for local disk sessions |
| Control RPC 100ms–1s | Note size; large host sessions expected |
| Control RPC &gt; 1s | Call out; overview/timeline cost |
| UI step &gt; 2s after key | Suspect load path or main-thread stall |
| UI step &gt; 5s | Treat as performance defect unless data is huge |

UI ms include settle sleeps built into the harness — subtract the documented settle when comparing absolute numbers.
