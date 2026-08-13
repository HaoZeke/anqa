//! groket identity for the HUD (window icon, tray, search chrome).
//!
//! Asset map follows ``brand/guidelines.html`` §7:
//! - Window / Alt-Tab / tray / notify → **favicon** (7×3 small mark in a square)
//! - Search chrome → full **mark** at 32px tall (colour / reverse)
//! - Large dock tiles (1024) stay available for installers; not used for the
//!   live window icon (X11 often drops huge ``_NET_WM_ICON`` payloads).

use std::sync::OnceLock;

use iced::widget::image;
use iced::window::icon;
use iced::Length;

/// Small-mark square (three bars + caps). Window switcher, tray, notify.
///
/// Prefer 64 for the window property (still small on the X11 wire); tray and
/// notify use the same art so every low-res surface matches the tab favicon.
pub const FAVICON_64_PNG: &[u8] = include_bytes!("../../brand/png/groket-favicon-64.png");
pub const FAVICON_32_PNG: &[u8] = include_bytes!("../../brand/png/groket-favicon-32.png");

/// Colour mark (transparent). Light ``$surface``. Guidelines: search chrome 32px.
pub const MARK_PNG: &[u8] = include_bytes!("../../brand/png/groket-mark.png");

/// Reverse mark (cream rocket on ink). Dark chrome knocks the field out.
pub const MARK_REVERSE_PNG: &[u8] = include_bytes!("../../brand/png/groket-mark-reverse.png");

/// Mark viewBox 900×380. Search chrome preferred height from brand guidelines.
pub const MARK_H: f32 = 32.0;
pub const MARK_W: f32 = MARK_H * 900.0 / 380.0;

/// Window / Dock / Alt-Tab icon: three-bar favicon (not the 1024 dock tile).
pub fn window_icon() -> Option<iced::window::Icon> {
    icon::from_file_data(FAVICON_64_PNG, None).ok()
}

/// Tray and desktop-notify pixmap bytes (three-bar favicon; 64 matches window).
pub fn tray_icon_png() -> &'static [u8] {
    FAVICON_64_PNG
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
    fn window_icon_is_favicon_not_dock_tile() {
        let icon = window_icon().expect("favicon-64");
        let (rgba, size) = icon.into_raw();
        assert_eq!((size.width, size.height), (64, 64));
        assert_eq!(rgba.len(), 64 * 64 * 4);
        // Three-bar favicon has opaque ink + caps (not a full cream plate).
        let opaque = rgba.chunks_exact(4).filter(|p| p[3] > 200).count();
        assert!(opaque > 100 && opaque < 64 * 64, "favicon should be sparse bars, not a solid tile");
    }

    #[test]
    fn tray_icon_is_favicon_64() {
        assert_eq!(tray_icon_png()[1..4], *b"PNG");
        assert_eq!(tray_icon_png().len(), FAVICON_64_PNG.len());
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
