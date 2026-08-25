//! anqa identity for the HUD (window icon, tray, search chrome).
//!
//! - **Search chrome** → truck-art mark at 32px (colour / reverse).
//! - **Window / tray** → square app icon and favicon tiles from ``brand/png``.
//! - **Desktop notify** → favicon on Linux (small freedesktop slot);
//!   square app icon on macOS / Windows (Notification Center / toast face).
//! - **Dock / desktop install** → square app icons (``anqa-app-icon-*.png``).

use std::sync::OnceLock;

use iced::widget::image;
use iced::window::icon;
use iced::Length;

/// Taskbar / Alt-Tab / tray: favicon bird on cream.
pub const TRAY_32_PNG: &[u8] = include_bytes!("../../brand/png/anqa-favicon-32.png");
pub const TRAY_64_PNG: &[u8] = include_bytes!("../../brand/png/anqa-favicon-64.png");

/// Browser-tab / 16px theme slot (favicon art).
pub const FAVICON_16_PNG: &[u8] = include_bytes!("../../brand/png/anqa-favicon-16.png");

/// Square dock / desktop tiles (bird on plate).
pub const APP_ICON_256_PNG: &[u8] = include_bytes!("../../brand/png/anqa-app-icon-256.png");
pub const APP_ICON_512_PNG: &[u8] = include_bytes!("../../brand/png/anqa-app-icon-512.png");
pub const APP_ICON_1024_PNG: &[u8] = include_bytes!("../../brand/png/anqa-app-icon-1024.png");

/// macOS ``NSApplication`` / Dock tile (512 is enough without a 1024 decode hit).
pub const APP_ICON_PNG: &[u8] = APP_ICON_512_PNG;

/// Colour mark (transparent). Light ``$surface``.
pub const MARK_PNG: &[u8] = include_bytes!("../../brand/png/anqa-mark.png");

/// Reverse mark (cream bird on ink). Dark chrome knocks the field out.
pub const MARK_REVERSE_PNG: &[u8] = include_bytes!("../../brand/png/anqa-mark-reverse.png");

/// Mark is 536×445. Search chrome preferred height from brand guidelines.
pub const MARK_H: f32 = 32.0;
pub const MARK_W: f32 = MARK_H * 536.0 / 445.0;

/// Window / Alt-Tab icon (256 square app tile).
pub fn window_icon() -> Option<iced::window::Icon> {
    icon::from_file_data(APP_ICON_256_PNG, None).ok()
}

/// Tray pixmap (64px favicon).
pub fn tray_icon_png() -> &'static [u8] {
    TRAY_64_PNG
}

/// Desktop-notify pixmap for the host slot.
///
/// Linux uses the 64px favicon (small freedesktop icon). macOS Notification
/// Center and Windows toasts show a large face, so those hosts use the 256px
/// square app icon. ``notify-rust`` ``icon()`` is a no-op on macOS.
pub fn notify_icon_png() -> &'static [u8] {
    #[cfg(target_os = "linux")]
    {
        TRAY_64_PNG
    }
    #[cfg(not(target_os = "linux"))]
    {
        APP_ICON_256_PNG
    }
}

/// Theme / installer sizes: (pixel edge, PNG bytes).
pub fn desktop_icon_pngs() -> &'static [(u32, &'static [u8])] {
    &[
        (16, FAVICON_16_PNG),
        (32, TRAY_32_PNG),
        (64, TRAY_64_PNG),
        (256, APP_ICON_256_PNG),
        (512, APP_ICON_512_PNG),
        (1024, APP_ICON_1024_PNG),
    ]
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

/// Reverse PNG is cream bird on an ink field. Drop ink so the bird sits
/// on ``$surface``. Ink is ``#0B0D0C``.
fn knocked_out_reverse() -> image::Handle {
    let decoded = icon::from_file_data(MARK_REVERSE_PNG, None).expect("anqa-mark-reverse.png");
    let (mut rgba, size) = decoded.into_raw();
    for px in rgba.as_chunks_mut::<4>().0 {
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
    fn window_and_tray_use_square_bird_tiles() {
        let win = window_icon().expect("app-icon-256");
        let (rgba, size) = win.into_raw();
        assert_eq!((size.width, size.height), (256, 256));
        let cream = rgba
            .as_chunks::<4>()
            .0
            .iter()
            .filter(|p| p[3] > 200 && p[0] > 200 && p[1] > 180)
            .count();
        assert!(cream * 2 > rgba.len() / 4, "cream plate should dominate");
        let ink = rgba
            .as_chunks::<4>()
            .0
            .iter()
            .filter(|p| p[3] > 200 && p[0] < 60 && p[1] < 60 && p[2] < 60)
            .count();
        assert!(ink > 80, "ink bird should sit on the cream plate");
        assert_eq!(tray_icon_png()[1..4], *b"PNG");
        assert_eq!(tray_icon_png().len(), TRAY_64_PNG.len());
        assert_eq!(notify_icon_png()[1..4], *b"PNG");
        #[cfg(target_os = "linux")]
        assert_eq!(notify_icon_png().len(), TRAY_64_PNG.len());
        #[cfg(not(target_os = "linux"))]
        assert_eq!(notify_icon_png().len(), APP_ICON_256_PNG.len());
    }

    #[test]
    fn chrome_mark_is_truck_art_bird_at_32px() {
        assert_eq!(MARK_PNG[1..4], *b"PNG");
        assert_eq!(MARK_REVERSE_PNG[1..4], *b"PNG");
        assert!((MARK_W - MARK_H * 536.0 / 445.0).abs() < 0.01);
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
                assert_eq!((width, height), (536, 445));
                let clear = pixels
                    .as_chunks::<4>()
                    .0
                    .iter()
                    .filter(|p| p[3] == 0)
                    .count();
                assert!(
                    clear * 2 > pixels.len() / 4,
                    "reverse chrome knocks out ink field"
                );
            }
            _ => panic!("reverse chrome should be decoded RGBA"),
        }
    }
}
