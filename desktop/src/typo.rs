//! Brand type: Fira Sans (UI) and Fira Code (mono) from ``brand/fonts/``.
//!
//! Type *sizes* come from [`Tokens`] (`body` / `meta` / `title` / `code`).
//! Ligatures need iced ``advanced-shaping`` so ASCII text uses HarfBuzz.

use std::borrow::Cow;

use iced::font::{Family, Font, Stretch, Style, Weight};

/// Family name inside the Fira Sans TTF files.
pub const FAMILY: &str = "Fira Sans";

/// Family name inside the Fira Code TTF files.
pub const MONO_FAMILY: &str = "Fira Code";

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

/// Code, paths, and highlighter bodies.
pub const MONO: Font = Font {
    family: Family::Name(MONO_FAMILY),
    weight: Weight::Normal,
    stretch: Stretch::Normal,
    style: Style::Normal,
};

/// Bytes iced must load before the first frame.
pub fn files() -> Vec<Cow<'static, [u8]>> {
    vec![
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-Regular.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-Medium.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-SemiBold.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraSans-ExtraBold.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraCode-Regular.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraCode-Medium.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraCode-SemiBold.ttf")),
        Cow::Borrowed(include_bytes!("../../brand/fonts/FiraCode-Bold.ttf")),
    ]
}

/// Load Fira faces and bind them to iced's SansSerif and Monospace generics.
///
/// Call once before the iced loop. Overwrites the binds from
/// [`icedtea::typo::install_platform_faces`] so icedtea chrome that uses
/// `Font::DEFAULT` / `Font::MONOSPACE` matches the HUD.
pub fn install() {
    icedtea::typo::install_platform_faces();
    let lock = iced::advanced::graphics::text::font_system();
    let mut system = lock.write().expect("font system");
    let db = system.raw().db_mut();
    for file in files() {
        db.load_font_data(file.into_owned());
    }
    db.set_sans_serif_family(FAMILY);
    db.set_monospace_family(MONO_FAMILY);
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
    }

    #[test]
    fn mono_is_fira_code() {
        assert_eq!(MONO.family, Family::Name("Fira Code"));
        assert_eq!(MONO.weight, Weight::Normal);
        assert!(
            files().len() >= 6,
            "Sans plus Code Regular and at least one Code weight"
        );
    }

    #[test]
    fn default_shaping_is_advanced_so_ligatures_run() {
        use iced::advanced::text::Shaping;
        assert_eq!(Shaping::default(), Shaping::Advanced);
    }
}
