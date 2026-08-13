//! Type scale and faces from icedtea. No bundled font files.
//!
//! icedtea maps sans and mono to installed platform families (including a
//! real bold so headings do not fall through to Menlo on macOS).

pub use icedtea::typo::{BODY, META, MONO, PAGE, TITLE, UI, UI_BOLD, UI_ITALIC};

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
