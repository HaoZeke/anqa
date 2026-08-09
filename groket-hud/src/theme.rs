//! Map Textual theme tokens (``config.json`` ``theme``) onto iced.

use iced::theme::{Palette, Theme};
use iced::Color;
use serde_json::Value;

const CATALOG: &str = include_str!("../assets/textual-themes.json");

/// Subset of Textual ColorSystem tokens the HUD paints with.
///
/// Matches TUI usage: Screen is ``$surface``, cards are ``$surface`` with a
/// ``$primary-background`` border, chrome is ``$panel``.
/// All channels are **opaque** (Textual muted hex often ends in ``99``).
#[derive(Debug, Clone, Copy)]
pub struct Tokens {
    pub canvas: Color,
    pub panel: Color,
    pub card: Color,
    pub selected: Color,
    pub selected_text: Color,
    pub text: Color,
    pub muted: Color,
    pub primary: Color,
    pub accent: Color,
    #[allow(dead_code)]
    pub secondary: Color,
    pub success: Color,
    pub warning: Color,
    pub error: Color,
    pub border: Color,
}

/// Rec. 709 luma in 0..1 (for picking light vs dark text on a fill).
pub fn relative_luma(c: Color) -> f32 {
    0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b
}

impl Tokens {
    /// True when ``$surface`` is a dark canvas (gruvbox, nord, …).
    pub fn canvas_is_dark(self) -> bool {
        relative_luma(self.canvas) < 0.45
    }
}

/// Blend ``fg`` over ``bg`` by ``amount`` (0 = bg, 1 = fg). Result is opaque.
pub fn mix(fg: Color, bg: Color, amount: f32) -> Color {
    let t = amount.clamp(0.0, 1.0);
    Color::from_rgb(
        fg.r * t + bg.r * (1.0 - t),
        fg.g * t + bg.g * (1.0 - t),
        fg.b * t + bg.b * (1.0 - t),
    )
}

fn parse_hex(s: &str) -> Option<Color> {
    let t = s.trim().trim_start_matches('#');
    if t.len() < 6 || !t.as_bytes()[..6].iter().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let r = u8::from_str_radix(&t[0..2], 16).ok()?;
    let g = u8::from_str_radix(&t[2..4], 16).ok()?;
    let b = u8::from_str_radix(&t[4..6], 16).ok()?;
    Some(Color::from_rgb8(r, g, b))
}

fn color_of(colors: &Value, key: &str, fallback: Color) -> Color {
    colors
        .get(key)
        .and_then(Value::as_str)
        .and_then(parse_hex)
        .unwrap_or(fallback)
}

fn catalog_colors(name: &str) -> (String, Value) {
    let Ok(root) = serde_json::from_str::<Value>(CATALOG) else {
        return ("textual-dark".into(), Value::Null);
    };
    let key = if name.trim().is_empty() {
        "textual-dark"
    } else {
        name.trim()
    };
    let theme = root
        .get(key)
        .or_else(|| root.get("textual-dark"))
        .cloned()
        .unwrap_or(Value::Null);
    let resolved = if root.get(key).is_some() {
        key.to_string()
    } else {
        "textual-dark".into()
    };
    let colors = theme.get("colors").cloned().unwrap_or(Value::Null);
    (resolved, colors)
}

/// Tokens for ``theme`` in ``~/.groket/config.json`` (TUI name).
pub fn tokens(name: &str) -> Tokens {
    let (_key, colors) = catalog_colors(name);
    let fallback_bg = Color::from_rgb8(18, 18, 20);
    let canvas = color_of(&colors, "surface", fallback_bg);
    let text = color_of(&colors, "foreground", Color::from_rgb8(224, 224, 224));
    let muted = color_of(
        &colors,
        "foreground-darken-2",
        color_of(&colors, "foreground-muted", Color::from_rgb8(160, 160, 160)),
    );
    let primary = color_of(&colors, "primary", Color::from_rgb8(1, 120, 212));
    let accent = color_of(&colors, "accent", Color::from_rgb8(254, 166, 43));
    // List selection is a primary wash over the screen, not full $accent.
    // Accent fills (violet / gold) fight $foreground on solarized-light / gruvbox.
    let selected = mix(primary, canvas, 0.28);
    let highlight = color_of(&colors, "primary-background", mix(primary, canvas, 0.35));
    Tokens {
        canvas,
        panel: color_of(&colors, "panel", mix(text, canvas, 0.10)),
        card: canvas,
        selected,
        selected_text: text,
        text,
        muted,
        primary,
        accent,
        secondary: color_of(&colors, "secondary", muted),
        success: color_of(
            &colors,
            "text-success",
            color_of(&colors, "success", Color::from_rgb8(78, 191, 113)),
        ),
        warning: color_of(
            &colors,
            "text-warning",
            color_of(&colors, "warning", Color::from_rgb8(254, 166, 43)),
        ),
        error: color_of(
            &colors,
            "text-error",
            color_of(&colors, "error", Color::from_rgb8(185, 60, 91)),
        ),
        border: highlight,
    }
}

pub fn iced_theme(name: &str) -> Theme {
    let (key, _) = catalog_colors(name);
    let t = tokens(name);
    Theme::custom(
        key,
        Palette {
            background: t.canvas,
            text: t.text,
            primary: t.primary,
            success: t.success,
            danger: t.error,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn textual_dark_uses_screen_surface_not_void_background() {
        let t = tokens("textual-dark");
        assert_eq!(t.canvas, Color::from_rgb8(0x1E, 0x1E, 0x1E));
        assert_ne!(t.canvas, Color::from_rgb8(0x12, 0x12, 0x12));
        assert_eq!(t.primary, Color::from_rgb8(0x01, 0x78, 0xD4));
        assert_eq!(t.selected, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selected_text, t.text);
        assert_eq!(t.border, Color::from_rgb8(0x33, 0x42, 0x4E));
    }

    #[test]
    fn gruvbox_is_in_catalog() {
        let t = tokens("gruvbox");
        assert_ne!(t.canvas, tokens("textual-dark").canvas);
        assert_ne!(t.canvas, tokens("nord").canvas);
        assert!(t.canvas_is_dark());
        assert!(tokens("textual-dark").canvas_is_dark());
        assert!(!tokens("solarized-light").canvas_is_dark());
    }

    #[test]
    fn flexoki_matches_tui_screen_surface() {
        let t = tokens("flexoki");
        assert_eq!(t.canvas, Color::from_rgb8(0x1C, 0x1B, 0x1A));
        assert_ne!(t.canvas, Color::from_rgb8(0x10, 0x0F, 0x0F));
        assert_eq!(t.selected, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selected_text, t.text);
        assert_eq!(t.panel, Color::from_rgb8(0x28, 0x27, 0x26));
    }

    #[test]
    fn solarized_light_selected_row_keeps_readable_ink() {
        let t = tokens("solarized-light");
        assert_ne!(t.selected, t.accent);
        assert_eq!(t.selected_text, t.text);
        assert!((relative_luma(t.selected) - relative_luma(t.text)).abs() > 0.20);
        assert_eq!(t.selected, mix(t.primary, t.canvas, 0.28));
    }

    #[test]
    fn catalog_hex_drops_textual_alpha_suffix() {
        let t = tokens("gruvbox");
        assert_eq!(t.text, Color::from_rgb8(0xFB, 0xF1, 0xC7));
        assert_eq!(t.text.a, 1.0);
        assert_eq!(t.muted.a, 1.0);
        assert_eq!(t.muted, Color::from_rgb8(0xD0, 0xC6, 0x9E));
        assert_eq!(t.accent, Color::from_rgb8(0xF9, 0xBD, 0x2F));
        assert_eq!(t.warning, Color::from_rgb8(0xFE, 0xAB, 0x67));
        assert_eq!(t.error, Color::from_rgb8(0xFC, 0x86, 0x79));
    }

    #[test]
    fn mix_is_opaque_between_endpoints() {
        let a = Color::from_rgb8(255, 0, 0);
        let b = Color::from_rgb8(0, 0, 0);
        let m = mix(a, b, 0.5);
        assert!((m.r - 0.5).abs() < 0.01);
        assert_eq!(m.a, 1.0);
    }
}
