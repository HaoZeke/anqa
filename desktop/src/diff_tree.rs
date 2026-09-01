//! Collapsed directory tree rows for Diff file lists.

use std::collections::HashSet;
use std::hash::{Hash, Hasher};

use icedtea::collection::{RowSlot, TreeNode};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiffTreeRow {
    pub kind: DiffTreeKind,
    pub label: String,
    pub depth: usize,
    pub path: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiffTreeKind {
    Dir,
    File,
}

#[derive(Default)]
struct Node {
    children: std::collections::BTreeMap<String, Node>,
    is_file: bool,
}

/// Build directory tree rows from path strings.
///
/// Every row label is a single path segment (basename for files, one
/// folder name for dirs). Multi-segment labels clip mid-name in the
/// narrow Diff files pane (``…/src/vie``).
pub fn tree_rows(paths: impl IntoIterator<Item = impl AsRef<str>>) -> Vec<DiffTreeRow> {
    let mut root = Node::default();
    for raw in paths {
        let parts: Vec<String> = raw
            .as_ref()
            .replace('\\', "/")
            .trim()
            .trim_matches('/')
            .split('/')
            .filter(|p| !p.is_empty())
            .map(str::to_string)
            .collect();
        if parts.is_empty() {
            continue;
        }
        let mut node = &mut root;
        for part in parts {
            node = node.children.entry(part).or_default();
        }
        node.is_file = true;
    }
    let mut out = Vec::new();
    walk(&root, "", 0, "", &mut out);
    out
}

fn walk(node: &Node, name: &str, depth: usize, prefix: &str, out: &mut Vec<DiffTreeRow>) {
    if name.is_empty() {
        for (child_name, child) in &node.children {
            walk(child, child_name, depth, prefix, out);
        }
        return;
    }
    let path = if prefix.is_empty() {
        name.to_string()
    } else {
        format!("{prefix}{name}")
    };
    if node.is_file && node.children.is_empty() {
        out.push(DiffTreeRow {
            kind: DiffTreeKind::File,
            label: name.to_string(),
            depth,
            path,
        });
        return;
    }
    // Directory (or a path component that is both a file and a parent).
    if !node.children.is_empty() {
        out.push(DiffTreeRow {
            kind: DiffTreeKind::Dir,
            label: format!("{name}/"),
            depth,
            path: format!("{path}/"),
        });
        let next = format!("{path}/");
        for (child_name, child) in &node.children {
            walk(child, child_name, depth + 1, &next, out);
        }
    }
    if node.is_file {
        out.push(DiffTreeRow {
            kind: DiffTreeKind::File,
            label: name.to_string(),
            depth,
            path,
        });
    }
}

pub fn path_id(path: &str) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    stripped_path(path).hash(&mut h);
    h.finish()
}

fn stripped_path(path: &str) -> String {
    path.replace('\\', "/").trim().trim_matches('/').to_string()
}

/// icedtea [`TreeNode`] for Diff files. Directories start expanded unless
/// *collapsed* contains their [`path_id`].
pub fn file_tree(
    paths: impl IntoIterator<Item = impl AsRef<str>>,
    collapsed: &HashSet<u64>,
) -> TreeNode {
    let rows = tree_rows(paths);
    let mut root = TreeNode::branch(0, "files", Vec::new());
    for row in rows {
        let id = path_id(&row.path);
        let node = match row.kind {
            DiffTreeKind::Dir => TreeNode {
                id,
                label: row.label,
                expanded: !collapsed.contains(&id),
                children: Vec::new(),
                dir: true,
                trailing: RowSlot::Empty,
            },
            DiffTreeKind::File => TreeNode::leaf(id, row.label),
        };
        insert_at_depth(&mut root, row.depth, node);
    }
    root
}

pub fn file_path_for_id(
    paths: impl IntoIterator<Item = impl AsRef<str>>,
    id: u64,
) -> Option<String> {
    let originals: Vec<String> = paths.into_iter().map(|p| p.as_ref().to_string()).collect();
    originals.into_iter().find(|orig| path_id(orig) == id)
}

fn insert_at_depth(parent: &mut TreeNode, depth: usize, node: TreeNode) {
    if depth == 0 {
        parent.children.push(node);
        return;
    }
    let last = parent
        .children
        .last_mut()
        .expect("dir row precedes children");
    insert_at_depth(last, depth - 1, node);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn root_files_stay_flat() {
        assert_eq!(
            tree_rows(["app.py", "added.py"]),
            vec![
                row(DiffTreeKind::File, "added.py", 0, "added.py"),
                row(DiffTreeKind::File, "app.py", 0, "app.py"),
            ]
        );
    }

    #[test]
    fn nested_dirs_one_segment_per_row() {
        assert_eq!(
            tree_rows(["src/anqa/ui/app.py", "src/anqa/ui/widgets.py"]),
            vec![
                row(DiffTreeKind::Dir, "src/", 0, "src/"),
                row(DiffTreeKind::Dir, "anqa/", 1, "src/anqa/"),
                row(DiffTreeKind::Dir, "ui/", 2, "src/anqa/ui/"),
                row(DiffTreeKind::File, "app.py", 3, "src/anqa/ui/app.py"),
                row(
                    DiffTreeKind::File,
                    "widgets.py",
                    3,
                    "src/anqa/ui/widgets.py"
                ),
            ]
        );
    }

    #[test]
    fn single_nested_file_keeps_basename_leaf() {
        assert_eq!(
            tree_rows(["src/a.py"]),
            vec![
                row(DiffTreeKind::Dir, "src/", 0, "src/"),
                row(DiffTreeKind::File, "a.py", 1, "src/a.py"),
            ]
        );
        assert_eq!(
            tree_rows(["crates/vissue-hud/src/view.rs"]),
            vec![
                row(DiffTreeKind::Dir, "crates/", 0, "crates/"),
                row(DiffTreeKind::Dir, "vissue-hud/", 1, "crates/vissue-hud/"),
                row(DiffTreeKind::Dir, "src/", 2, "crates/vissue-hud/src/"),
                row(
                    DiffTreeKind::File,
                    "view.rs",
                    3,
                    "crates/vissue-hud/src/view.rs"
                ),
            ]
        );
    }

    #[test]
    fn file_tree_nests_under_dir() {
        let root = file_tree(["src/a.py", "src/b.py"], &HashSet::new());
        assert_eq!(root.children.len(), 1);
        assert!(root.children[0].dir);
        assert_eq!(root.children[0].label, "src/");
        assert_eq!(root.children[0].children.len(), 2);
        assert_eq!(root.children[0].children[0].label, "a.py");
        let id = path_id("src/a.py");
        assert_eq!(
            file_path_for_id(["src/a.py", "src/b.py"], id).as_deref(),
            Some("src/a.py")
        );
    }

    #[test]
    fn absolute_path_click_returns_original() {
        let orig = "/home/ali/.pi/agent/agents/reviewer.md";
        let id = path_id(orig);
        assert_eq!(path_id("home/ali/.pi/agent/agents/reviewer.md"), id);
        assert_eq!(file_path_for_id([orig], id).as_deref(), Some(orig));
    }

    #[test]
    fn mixed_root_and_nested() {
        assert_eq!(
            tree_rows(["README.md", "src/a.py", "src/b/c.py"]),
            vec![
                row(DiffTreeKind::File, "README.md", 0, "README.md"),
                row(DiffTreeKind::Dir, "src/", 0, "src/"),
                row(DiffTreeKind::File, "a.py", 1, "src/a.py"),
                row(DiffTreeKind::Dir, "b/", 1, "src/b/"),
                row(DiffTreeKind::File, "c.py", 2, "src/b/c.py"),
            ]
        );
    }

    #[test]
    fn row_labels_never_contain_path_separators() {
        for path in [
            "view.rs",
            "src/a.py",
            "crates/vissue-hud/src/view.rs",
            "a/b/c/d/e.rs",
        ] {
            for row in tree_rows([path]) {
                let bare = row.label.trim_end_matches('/');
                assert!(
                    !bare.contains('/'),
                    "label must be one segment, got {:?}",
                    row.label
                );
            }
        }
    }

    fn row(kind: DiffTreeKind, label: &str, depth: usize, path: &str) -> DiffTreeRow {
        DiffTreeRow {
            kind,
            label: label.into(),
            depth,
            path: path.into(),
        }
    }
}
