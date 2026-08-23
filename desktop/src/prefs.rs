//! Read ``~/.groket/config.toml`` (same shape as Python ``groket.config``).

use serde::Deserialize;
use std::env;
use std::fs;
use std::path::PathBuf;
use toml_edit::{value, DocumentMut};

fn config_path() -> Option<PathBuf> {
    let home = env::var_os("HOME")?;
    Some(PathBuf::from(home).join(".groket").join("config.toml"))
}

#[derive(Debug, Default, Deserialize)]
struct File {
    #[serde(default)]
    theme: Option<String>,
    #[serde(default)]
    follow_os: Option<bool>,
    #[serde(default)]
    hud: HudFile,
}

#[derive(Debug, Default, Deserialize)]
struct HudFile {
    #[serde(default)]
    window_mode: Option<bool>,
    #[serde(default)]
    global_shortcut: Option<String>,
    #[serde(default)]
    desktop_notifications: Option<bool>,
}

fn read_file() -> File {
    let Some(path) = config_path() else {
        return File::default();
    };
    let Ok(text) = fs::read_to_string(path) else {
        return File::default();
    };
    toml::from_str(&text).unwrap_or_default()
}

/// Desktop notifications (dunst / mako / Notification Center / toasts).
///
/// Default on. ``hud.desktop_notifications`` in config.toml overrides.
pub fn desktop_notifications() -> bool {
    read_file().hud.desktop_notifications.unwrap_or(true)
}

/// Persistent window (not overlay) from ``hud.window_mode``.
pub fn window_mode() -> bool {
    read_file().hud.window_mode.unwrap_or(false)
}

/// When true, paired colorways follow the host light/dark setting.
pub fn follow_os() -> bool {
    read_file().follow_os.unwrap_or(false)
}

/// Theme name from config, or ``auto`` (host light/dark).
pub fn theme_name() -> String {
    read_file()
        .theme
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("auto")
        .to_string()
}

/// ``~/.groket/themes`` drop-in colorways.
pub fn themes_dir() -> Option<PathBuf> {
    let home = env::var_os("HOME")?;
    Some(PathBuf::from(home).join(".groket").join("themes"))
}

/// HUD summon chord from ``hud.global_shortcut`` (empty = binary default).
pub fn global_shortcut() -> String {
    read_file()
        .hud
        .global_shortcut
        .unwrap_or_default()
        .trim()
        .to_string()
}

/// Write ``hud.window_mode`` into ``config.toml`` (other keys and comments kept).
pub fn save_window_mode(window_mode: bool) {
    let Some(path) = config_path() else {
        return;
    };
    let mut doc = fs::read_to_string(&path)
        .ok()
        .and_then(|text| text.parse::<DocumentMut>().ok())
        .unwrap_or_default();
    doc["hud"]["window_mode"] = value(window_mode);
    let _ = fs::write(path, doc.to_string());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_defaults_are_empty() {
        let f = File::default();
        assert!(f.theme.is_none());
        assert!(f.hud.window_mode.is_none());
    }

    #[test]
    fn parse_hud_table() {
        let f: File = toml::from_str(
            "theme = \"nord\"\nfollow_os = true\n[hud]\nwindow_mode = true\nglobal_shortcut = \"Ctrl+K\"\n",
        )
        .expect("toml");
        assert_eq!(f.theme.as_deref(), Some("nord"));
        assert_eq!(f.follow_os, Some(true));
        assert_eq!(f.hud.window_mode, Some(true));
        assert_eq!(f.hud.global_shortcut.as_deref(), Some("Ctrl+K"));
    }

    #[test]
    fn parse_shipped_example() {
        let text = include_str!("../../examples/config/config.toml");
        let f: File = toml::from_str(text).expect("example toml");
        assert_eq!(f.theme.as_deref(), Some("auto"));
        assert_eq!(f.follow_os, Some(false));
        assert_eq!(f.hud.window_mode, Some(false));
        assert_eq!(f.hud.global_shortcut.as_deref(), Some(""));
        assert_eq!(f.hud.desktop_notifications, Some(true));
    }
}
