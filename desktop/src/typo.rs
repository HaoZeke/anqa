//! Faces from icedtea. No bundled font files.
//!
//! Type *sizes* come from [`Tokens`] (`body` / `meta` / `title` / `code`).
//! These aliases are the platform sans and mono only.

pub use icedtea::typo::{MONO, UI, UI_BOLD, UI_ITALIC};

#[cfg(test)]
mod tests {
    #[test]
    fn faces_are_icedtea_platform_generics() {
        assert_eq!(super::UI, icedtea::typo::UI);
        assert_eq!(super::UI_BOLD, icedtea::typo::UI_BOLD);
        assert_eq!(super::MONO, icedtea::typo::MONO);
    }
}
