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

// TODO(remove-json-config): Delete with the Python importer once every
// install has config.toml. Do not add more JSON prefs readers.
fn ensure_toml(toml_path: &std::path::Path) {
    if toml_path.is_file() {
        return;
    }
    let json_path = toml_path.with_file_name("config.json");
    let Ok(text) = fs::read_to_string(&json_path) else {
        return;
    };
    let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return;
    };
    fold_flat_shortcut(&mut v);
    let Ok(rendered) = toml::to_string_pretty(&v) else {
        return;
    };
    if fs::write(toml_path, rendered).is_ok() && toml_path.is_file() {
        let _ = fs::remove_file(json_path);
    }
}

fn fold_flat_shortcut(root: &mut serde_json::Value) {
    let Some(obj) = root.as_object_mut() else {
        return;
    };
    let flat = obj
        .get("hud_global_shortcut")
        .and_then(|x| x.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let Some(flat) = flat else {
        return;
    };
    let hud = obj.entry("hud").or_insert_with(|| serde_json::json!({}));
    if let Some(hud_obj) = hud.as_object_mut() {
        let empty = hud_obj
            .get("global_shortcut")
            .and_then(|x| x.as_str())
            .map(str::trim)
            .unwrap_or("")
            .is_empty();
        if empty {
            hud_obj.insert("global_shortcut".into(), serde_json::Value::String(flat));
        }
    }
    obj.remove("hud_global_shortcut");
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
    ensure_toml(&path);
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

/// Theme name from config, or ``groket``.
pub fn theme_name() -> String {
    read_file()
        .theme
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("groket")
        .to_string()
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
    ensure_toml(&path);
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
    fn fold_flat_shortcut_into_hud() {
        let mut v = serde_json::json!({
            "theme": "nord",
            "hud_global_shortcut": "Ctrl+K",
            "hud": { "window_mode": true }
        });
        fold_flat_shortcut(&mut v);
        assert_eq!(v["hud"]["global_shortcut"], "Ctrl+K");
        assert!(v.get("hud_global_shortcut").is_none());
    }
}
