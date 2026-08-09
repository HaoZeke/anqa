//! anqa identity for the HUD (window / Dock icon and in-chrome mark).

use std::sync::OnceLock;

use iced::widget::image;
use iced::window::icon;
use iced::Length;

/// Dock / window icon. Brand: macOS / Linux dock, HUD app.
pub const APP_ICON_PNG: &[u8] = include_bytes!("../../brand/anqa/png/anqa-app-icon-1024.png");

/// Colour mark (transparent). Light ``$surface``.
pub const MARK_PNG: &[u8] = include_bytes!("../../brand/anqa/png/anqa-mark.png");

/// Reverse mark (ink field). Dark ``$surface`` after knocking out near-black.
pub const MARK_REVERSE_PNG: &[u8] = include_bytes!("../../brand/anqa/png/anqa-mark-reverse.png");

/// Mark viewBox 536×445. Height is the brand preferred chrome size.
pub const MARK_H: f32 = 32.0;
pub const MARK_W: f32 = MARK_H * 536.0 / 445.0;

/// Decode the app icon for the window titlebar / taskbar / Dock.
pub fn window_icon() -> Option<iced::window::Icon> {
    icon::from_file_data(APP_ICON_PNG, None).ok()
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

/// Reverse PNG is a solid ink field. Drop near-black so the bird sits on
/// ``$surface`` (gruvbox grey, not only true black).
fn knocked_out_reverse() -> image::Handle {
    let decoded = icon::from_file_data(MARK_REVERSE_PNG, None).expect("anqa-mark-reverse.png");
    let (mut rgba, size) = decoded.into_raw();
    for px in rgba.chunks_exact_mut(4) {
        if px[0] < 24 && px[1] < 24 && px[2] < 24 {
            px[3] = 0;
        }
    }
    image::Handle::from_rgba(size.width, size.height, rgba)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_icon_decodes_1024() {
        let icon = window_icon().expect("anqa-app-icon-1024.png");
        let (rgba, size) = icon.into_raw();
        assert_eq!(size.width, 1024);
        assert_eq!(size.height, 1024);
        assert_eq!(rgba.len(), 1024 * 1024 * 4);
    }

    #[test]
    fn chrome_mark_is_embedded() {
        assert_eq!(MARK_PNG[1..4], *b"PNG");
        assert_eq!(MARK_REVERSE_PNG[1..4], *b"PNG");
        let _ = (chrome_width(), chrome_height());
    }

    #[test]
    fn reverse_knockout_clears_ink_field() {
        let h = chrome_handle(true);
        match h {
            image::Handle::Rgba {
                width,
                height,
                pixels,
                ..
            } => {
                assert_eq!(width, 536);
                assert_eq!(height, 445);
                let n = pixels.len() / 4;
                let clear = pixels.chunks_exact(4).filter(|p| p[3] == 0).count();
                assert!(clear * 2 > n, "expected most of the ink field transparent");
            }
            _ => panic!("reverse chrome should be decoded RGBA"),
        }
    }
}
