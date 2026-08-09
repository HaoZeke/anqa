//! Embedded UI fonts and a single type scale.

use iced::font::{Family, Stretch, Style, Weight};
use iced::Font;

/// Fira Sans — default body/UI face.
pub const UI: Font = Font::with_name("Fira Sans");
/// Titles / selected labels.
pub const UI_BOLD: Font = Font {
    family: Family::Name("Fira Sans"),
    weight: Weight::Bold,
    stretch: Stretch::Normal,
    style: Style::Normal,
};
/// Thought / dim prose (TUI italic).
pub const UI_ITALIC: Font = Font {
    family: Family::Name("Fira Sans"),
    weight: Weight::Normal,
    stretch: Stretch::Normal,
    style: Style::Italic,
};
/// JetBrains Mono — ids, paths, code.
pub const MONO: Font = Font::with_name("JetBrains Mono");

pub const UI_BYTES: &[u8] = include_bytes!("../assets/fonts/FiraSans-Regular.ttf");
pub const MONO_BYTES: &[u8] = include_bytes!("../assets/fonts/JetBrainsMono-Regular.ttf");

/// Page title (overview heading).
pub const PAGE: u16 = 18;
/// Section / card title.
pub const TITLE: u16 = 15;
/// Body copy (also iced default_text_size).
pub const BODY: u16 = 14;
/// Meta, tabs, footer, kv keys.
pub const META: u16 = 12;
