# icedtea pin

HUD uses icedtea **0.4.0** (path pin for development). Overlay, hotkey,
`iced::daemon`, and Textual `config.json` → tokens stay groket-owned.

## Library surfaces in use

- Session rail: `list_view` + `RowFace::Card` + `RowHeights::PerRow`
- Turns / Timeline: `virtual_column` + `expand_card_heights`
- Chrome keys while typing: `KeyContext::chrome_over_input`
- Bodies: `Selectables::ensure` / `retain` / `unbind`
- Layout chrome: `list_detail`, `command_bar`, `themed_scroll` (Overview /
  Findings / Notes), `status_page`, `busy_overlay`, toasts

## Still groket-owned

- macOS placement (`place.rs` AppKit y-up)
- Search row (brand mark, field, hotkey, pop-out)
- Control plane, tray, hotkey, window modes

## Do not

- Publish a path pin to a local checkout
- Fold overlay / hotkey / daemon into `icedtea::run!`
