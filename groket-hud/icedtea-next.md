# icedtea pin

The HUD uses icedtea **0.4.0** (card ``list_view``, expander inset,
``command_bar``, ``list_detail``). Overlay, hotkey, ``iced::daemon``,
and the Textual ``config.json`` → ``tea_tokens`` map stay groket-owned.

## Done on this pin

- Detail panes use `themed_scroll` (timeline keeps its scroll id).
- `ListScroll` stores the window and `scroll_to` the list id.
- Escape hide goes through `icedtea::window::should_hide`.
- Timeline type is `themed_pick_list`; event filter is
  `themed_text_input`.
- Loading / empty / control-down use `placeholder_skeleton`,
  `status_page`, and `info_bar`.
- JSON/code uses `code_block`; saved images use `image_slot`.
- Overview status is a `badge`.
- Session list search is the jump path.
- Context fill is Overview-only (rail uses compact % text, no meters).
- Findings expanders by severity with a timeline command.
- Awaiting banner with follow-up / Done (`session/follow_up`, `session/done`).
- Catalog warmup uses `busy_overlay`. Toasts for save, copy, errors.
- Copy path lives on the overview command row.
- Timeline loads one page (40 rows). Scroll fetches more. Type/text
  filters run on the owner over the full session, not the local buffer.

## Still groket-owned

macOS placement stays in `place.rs` (AppKit y-up → winit). Do not
swap in `place_centered` without that flip.

Search stays a custom row (rocket, field, hotkey, pop-out).

Session rail uses icedtea `list_view` + `RowFace::Card` +
`RowHeights::PerRow`. Heights live on `Hud` next to the meta lines.

Turns / Timeline use icedtea `widget::virtual_column` with
`collection::expand_card_heights` (closed estimate + open rows).
Chrome keys while typing use `KeyContext::chrome_over_input`.
Selectable bodies use `Selectables::ensure` / `retain` / `unbind`.

Tab label **Timeline** matches the TUI; jump control **Go to Timeline**.

## Do not

- Pin a local checkout by path in published crates (dev may path-pin 0.4.x).
- Drop overlay / hotkey / daemon into `icedtea::run!`.
- Treat gallery-only pages (keys, colors) as HUD work.
