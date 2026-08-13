//! groket identity for the HUD (window icon, tray, search chrome).
//!
//! - **Search chrome** → full mark at 32px (colour / reverse), brand guidelines.
//! - **Window / tray / notify** → pick by host chrome contrast:
//!   - dark panel/theme → light cream dock tile (readable on dark bars)
//!   - light panel/theme → three-bar favicon (readable on light bars)
//!
//! Huge 1024 dock tiles are installer art; small PNGs keep X11 ``_NET_WM_ICON``
//! and StatusNotifier pixmaps honest.

use std::sync::OnceLock;

use iced::widget::image;
use iced::window::icon;
use iced::Length;

/// Three-bar small mark (ink + caps on transparent). Light host bars.
pub const FAVICON_64_PNG: &[u8] = include_bytes!("../../brand/png/groket-favicon-64.png");
pub const FAVICON_32_PNG: &[u8] = include_bytes!("../../brand/png/groket-favicon-32.png");

/// Cream dock tile. Dark host bars / dark HUD theme.
pub const APP_ICON_LIGHT_256_PNG: &[u8] =
    include_bytes!("../../brand/png/groket-app-icon-256.png");

/// Colour mark (transparent). Light ``$surface``. Guidelines: search chrome 32px.
pub const MARK_PNG: &[u8] = include_bytes!("../../brand/png/groket-mark.png");

/// Reverse mark (cream rocket on ink). Dark chrome knocks the field out.
pub const MARK_REVERSE_PNG: &[u8] = include_bytes!("../../brand/png/groket-mark-reverse.png");

/// Mark viewBox 900×380. Search chrome preferred height from brand guidelines.
pub const MARK_H: f32 = 32.0;
pub const MARK_W: f32 = MARK_H * 900.0 / 380.0;

/// PNG for window / tray / notify given whether the **host chrome is dark**.
///
/// Dark → cream tile. Light → three-bar favicon.
pub fn system_icon_png(dark_host: bool) -> &'static [u8] {
    if dark_host {
        APP_ICON_LIGHT_256_PNG
    } else {
        FAVICON_64_PNG
    }
}

/// Window / Alt-Tab icon for the current HUD theme (dark theme → light tile).
pub fn window_icon() -> Option<iced::window::Icon> {
    window_icon_for(crate::theme::canvas_is_dark(crate::theme::tokens(
        &crate::prefs::theme_name(),
    )))
}

/// Window icon with an explicit dark-host flag (tests / callers).
pub fn window_icon_for(dark_host: bool) -> Option<iced::window::Icon> {
    icon::from_file_data(system_icon_png(dark_host), None).ok()
}

/// Tray and desktop-notify pixmap (same contrast pick as the window icon).
pub fn tray_icon_png() -> &'static [u8] {
    system_icon_png(crate::theme::canvas_is_dark(crate::theme::tokens(
        &crate::prefs::theme_name(),
    )))
}

/// Search-bar mark: colour on light canvas, reverse (knocked-out ink) on dark.
pub fn chrome_handle(dark_canvas: bool) -> image::Handle {
    if dark_canvas {
        reverse_chrome_handle()
    } else {
        colour_chrome_handle()
    }
}

pub fn chrome_width() -> Length {
    Length::Fixed(MARK_W)
}

pub fn chrome_height() -> Length {
    Length::Fixed(MARK_H)
}

fn colour_chrome_handle() -> image::Handle {
    static HANDLE: OnceLock<image::Handle> = OnceLock::new();
    HANDLE
        .get_or_init(|| image::Handle::from_bytes(MARK_PNG))
        .clone()
}

fn reverse_chrome_handle() -> image::Handle {
    static HANDLE: OnceLock<image::Handle> = OnceLock::new();
    HANDLE.get_or_init(knocked_out_reverse).clone()
}

/// Reverse PNG is cream + caps on an ink field. Drop ink so the rocket sits
/// on ``$surface`` (gruvbox grey, not only true black). Ink is ``#282828``.
fn knocked_out_reverse() -> image::Handle {
    let decoded = icon::from_file_data(MARK_REVERSE_PNG, None).expect("groket-mark-reverse.png");
    let (mut rgba, size) = decoded.into_raw();
    for px in rgba.chunks_exact_mut(4) {
        if px[0] < 48 && px[1] < 48 && px[2] < 48 {
            px[3] = 0;
        }
    }
    image::Handle::from_rgba(size.width, size.height, rgba)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn system_icon_picks_cream_on_dark_and_favicon_on_light() {
        assert_eq!(
            system_icon_png(true).len(),
            APP_ICON_LIGHT_256_PNG.len(),
            "dark host → light cream tile"
        );
        assert_eq!(
            system_icon_png(false).len(),
            FAVICON_64_PNG.len(),
            "light host → three-bar favicon"
        );
        let dark = window_icon_for(true).expect("cream");
        let (rgba, size) = dark.into_raw();
        assert_eq!((size.width, size.height), (256, 256));
        let cream = rgba
            .chunks_exact(4)
            .filter(|p| p[3] > 200 && p[0] > 200 && p[1] > 180)
            .count();
        assert!(cream * 2 > rgba.len() / 4, "cream tile should dominate");
        let light = window_icon_for(false).expect("favicon");
        let (rgba, size) = light.into_raw();
        assert_eq!((size.width, size.height), (64, 64));
        let opaque = rgba.chunks_exact(4).filter(|p| p[3] > 200).count();
        assert!(opaque > 100 && opaque < 64 * 64);
    }

    #[test]
    fn tray_icon_png_is_valid() {
        assert_eq!(tray_icon_png()[1..4], *b"PNG");
    }

    #[test]
    fn chrome_mark_is_wide_rocket_at_32px() {
        assert_eq!(MARK_PNG[1..4], *b"PNG");
        assert_eq!(MARK_REVERSE_PNG[1..4], *b"PNG");
        assert!((MARK_W - MARK_H * 900.0 / 380.0).abs() < 0.01);
        assert_eq!(MARK_H, 32.0);
        let _ = (chrome_width(), chrome_height());
        let h = chrome_handle(true);
        match h {
            image::Handle::Rgba {
                width,
                height,
                pixels,
                ..
            } => {
                assert_eq!((width, height), (1200, 507));
                let clear = pixels.chunks_exact(4).filter(|p| p[3] == 0).count();
                assert!(clear * 2 > pixels.len() / 4, "reverse chrome knocks out ink field");
            }
            _ => panic!("reverse chrome should be decoded RGBA"),
        }
    }
}
