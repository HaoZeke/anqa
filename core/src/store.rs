//! One store trait. Every harness implements the same typed surface.

use crate::event::{Event, FileStamp, ListMeta, SessionLocator};
use std::path::{Path, PathBuf};

pub trait Store: Send + Sync {
    fn id(&self) -> &'static str;

    fn discover(&self, roots: &[PathBuf]) -> Vec<SessionLocator>;

    fn list_meta(&self, locator: &Path, session_id: &str) -> Result<ListMeta, String>;

    fn timeline(&self, locator: &Path, session_id: &str) -> Result<Vec<Event>, String>;

    fn stamp(&self, locator: &Path) -> FileStamp {
        crate::jsonl::file_stamp(locator)
    }
}

pub fn by_id(id: &str) -> Option<&'static dyn Store> {
    match id {
        "pi" => Some(&crate::stores::pi::Pi),
        "claude" => Some(&crate::stores::claude::Claude),
        "codex" => Some(&crate::stores::codex::Codex),
        "cursor" => Some(&crate::stores::cursor::Cursor),
        "gemini" => Some(&crate::stores::gemini::Gemini),
        "grok" => Some(&crate::stores::grok::Grok),
        "opencode" => Some(&crate::stores::opencode::OpenCode),
        "copilot" => Some(&crate::stores::copilot::Copilot),
        "antigravity" => Some(&crate::stores::antigravity::Antigravity),
        _ => None,
    }
}

pub fn ids() -> &'static [&'static str] {
    &[
        "antigravity",
        "claude",
        "codex",
        "copilot",
        "cursor",
        "gemini",
        "grok",
        "opencode",
        "pi",
    ]
}
