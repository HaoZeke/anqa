//! Brand UI face: Fira Sans from ``brand/fonts/`` (SIL Open Font License).
//!
//! Type *sizes* come from [`Tokens`] (`body` / `meta` / `title` / `code`).
//! Mono stays the platform generic (`install_platform_faces`).

use std::borrow::Cow;

use iced::font::{Family, Font, Stretch, Style, Weight};

pub use icedtea::typo::MONO;

/// Family name inside the Fira Sans TTF files.
pub const FAMILY: &str = "Fira Sans";

/// Body and chrome.
pub const UI: Font = Font {
    family: Family::Name(FAMILY),
    weight: Weight::Normal,
    stretch: Stretch::Normal,
    style: Style::Normal,
};

/// Selected list rows and section titles (SemiBold 600).
pub const UI_BOLD: Font = Font {
    weight: Weight::Semibold,
    ..UI
};

/// Wordmark weight when the name is set in chrome (ExtraBold 800).
pub const UI_EXTRABOLD: Font = Font {
    weight: Weight::ExtraBold,
    ..UI
};

/// Bytes iced must load before the first frame.
pub fn files() -> Vec<Cow<'static, [u8]>> {
    vec![
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-Regular.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-Medium.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-SemiBold.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-ExtraBold.ttf")),
    ]
}

/// Platform mono, then Fira Sans as the UI generic.
///
/// Call once before the iced loop. Overwrites the SansSerif bind from
/// [`icedtea::typo::install_platform_faces`] so icedtea chrome that uses
/// `Font::DEFAULT` matches the HUD body.
pub fn install() {
    icedtea::typo::install_platform_faces();
    let lock = iced::advanced::graphics::text::font_system();
    let mut system = lock.write().expect("font system");
    let db = system.raw().db_mut();
    for file in files() {
        db.load_font_data(file.into_owned());
    }
    db.set_sans_serif_family(FAMILY);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ui_is_fira_sans() {
        assert_eq!(UI.family, Family::Name("Fira Sans"));
        assert_eq!(UI.weight, Weight::Normal);
        assert_eq!(UI_BOLD.weight, Weight::Semibold);
        assert_eq!(UI_EXTRABOLD.weight, Weight::ExtraBold);
        assert_eq!(files().len(), 4);
        assert_eq!(MONO, icedtea::typo::MONO);
    }
}
