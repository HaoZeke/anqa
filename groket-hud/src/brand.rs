//! groket identity for the HUD (window / Dock icon and in-chrome mark).

use std::sync::OnceLock;

use iced::widget::image;
use iced::window::icon;
use iced::Length;

/// Dock / window icon. Brand: macOS / Linux dock, HUD app.
pub const APP_ICON_PNG: &[u8] = include_bytes!("../../brand/png/groket-app-icon-1024.png");

/// Light dock tile for search chrome on light surfaces.
pub const CHROME_LIGHT_PNG: &[u8] = include_bytes!("../../brand/png/groket-app-icon-256.png");

/// Dark dock tile for search chrome on dark surfaces.
pub const CHROME_DARK_PNG: &[u8] = include_bytes!("../../brand/png/groket-app-icon-dark-1024.png");

/// Square toolbar mark (not the wide landscape lockup mark).
pub const MARK_H: f32 = 28.0;
pub const MARK_W: f32 = MARK_H;

/// Decode the app icon for the window titlebar / taskbar / Dock.
pub fn window_icon() -> Option<iced::window::Icon> {
    icon::from_file_data(APP_ICON_PNG, None).ok()
}

/// Search-bar mark: square dock tile matched to canvas (light / dark).
pub fn chrome_handle(dark_canvas: bool) -> image::Handle {
    if dark_canvas {
        dark_chrome_handle()
    } else {
        light_chrome_handle()
    }
}

pub fn chrome_width() -> Length {
    Length::Fixed(MARK_W)
}

pub fn chrome_height() -> Length {
    Length::Fixed(MARK_H)
}

fn light_chrome_handle() -> image::Handle {
    static HANDLE: OnceLock<image::Handle> = OnceLock::new();
    HANDLE
        .get_or_init(|| image::Handle::from_bytes(CHROME_LIGHT_PNG))
        .clone()
}

fn dark_chrome_handle() -> image::Handle {
    static HANDLE: OnceLock<image::Handle> = OnceLock::new();
    HANDLE
        .get_or_init(|| image::Handle::from_bytes(CHROME_DARK_PNG))
        .clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_icon_decodes_1024() {
        let icon = window_icon().expect("groket-app-icon-1024.png");
        let (rgba, size) = icon.into_raw();
        assert_eq!(size.width, 1024);
        assert_eq!(size.height, 1024);
        assert_eq!(rgba.len(), 1024 * 1024 * 4);
    }

    #[test]
    fn chrome_mark_is_square_dock_tile() {
        assert_eq!(CHROME_LIGHT_PNG[1..4], *b"PNG");
        assert_eq!(CHROME_DARK_PNG[1..4], *b"PNG");
        assert_eq!(MARK_W, MARK_H);
        let _ = (chrome_width(), chrome_height());
        let _ = chrome_handle(false);
        let _ = chrome_handle(true);
    }
}
