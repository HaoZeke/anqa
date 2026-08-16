//! Type scale and faces from icedtea. No bundled font files.
//!
//! icedtea maps sans and mono to installed platform families (including a
//! real bold so headings do not fall through to Menlo on macOS).
//!
//! HUD overlay (~780×560): ``META`` (12) for chrome and reading, ``BODY``
//! (14) bold for card titles, ``TITLE`` (16) only for the Overview session
//! name. Do not use ``PAGE`` (22).

pub use icedtea::typo::{BODY, CODE, META, MONO, PAGE, TITLE, UI, UI_BOLD, UI_ITALIC};

#[cfg(test)]
mod tests {
    #[test]
    fn faces_are_icedtea_platform_generics() {
        assert_eq!(super::UI, icedtea::typo::UI);
        assert_eq!(super::UI_BOLD, icedtea::typo::UI_BOLD);
        assert_eq!(super::MONO, icedtea::typo::MONO);
        assert_eq!(super::BODY, icedtea::typo::BODY);
    }
}
