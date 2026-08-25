//! Portable session-directory walk (same rules as ``anqa.scan``).

use std::fs;
use std::path::{Path, PathBuf};

const SKIP_DIRS: &[&str] = &[
    "anqa-plugins",
    "anqa-skills",
    "groket-plugins",
    "groket-skills",
    "subagents",
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "target",
    "dist",
    "build",
    ".cache",
    ".tox",
    ".anqa-resume-seed",
    ".anqa-workspace-seed",
    ".groket-resume-seed",
    ".groket-workspace-seed",
    "workspace",
];

pub fn skip_dir_name(name: &str) -> bool {
    SKIP_DIRS.contains(&name) || name.ends_with(".stage")
}

fn is_nonsdir(path: &Path) -> bool {
    path.symlink_metadata()
        .map(|m| !m.is_dir())
        .unwrap_or(false)
}

fn events_nonempty(path: &Path) -> bool {
    if path.symlink_metadata().map(|m| m.is_dir()).unwrap_or(true) {
        return false;
    }
    path.metadata().map(|m| m.len() > 0).unwrap_or(false)
}

pub fn looks_like_session_dir(path: &Path) -> bool {
    is_nonsdir(&path.join("updates.jsonl"))
        || is_nonsdir(&path.join("summary.json"))
        || events_nonempty(&path.join("events.jsonl"))
}

fn has_subagents_segment(path: &Path) -> bool {
    path.iter().any(|part| part == "subagents")
}

/// Session dirs under *root*. Does not follow directory symlinks.
pub fn find_sessions(root: &Path) -> Vec<PathBuf> {
    if !root.exists() {
        return Vec::new();
    }
    let mut stack = vec![root.to_path_buf()];
    let mut found = Vec::new();
    while let Some(path) = stack.pop() {
        if has_subagents_segment(&path) {
            continue;
        }
        if looks_like_session_dir(&path) {
            found.push(path);
            continue;
        }
        let Ok(entries) = fs::read_dir(&path) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                continue;
            };
            if name == "." || name == ".." || skip_dir_name(name) {
                continue;
            }
            let child = entry.path();
            if child
                .symlink_metadata()
                .map(|m| m.is_dir())
                .unwrap_or(false)
            {
                stack.push(child);
            }
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;

    #[test]
    fn skip_stage_and_workspace() {
        assert!(skip_dir_name("foo.stage"));
        assert!(skip_dir_name("workspace"));
        assert!(!skip_dir_name("keep"));
    }

    #[test]
    fn looks_like_summary_and_empty_events() {
        let dir = std::env::temp_dir().join(format!("anqa-walk-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("summary.json"), b"{}").unwrap();
        assert!(looks_like_session_dir(&dir));
        fs::remove_file(dir.join("summary.json")).unwrap();
        let mut f = fs::File::create(dir.join("events.jsonl")).unwrap();
        assert!(!looks_like_session_dir(&dir));
        f.write_all(b"{}\n").unwrap();
        drop(f);
        assert!(looks_like_session_dir(&dir));
        fs::remove_dir_all(&dir).unwrap();
    }
}
