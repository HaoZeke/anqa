//! Collapsed directory tree rows for Diff file lists.

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

/// Build collapsed tree rows from path strings.
///
/// Unary directory chains merge. A directory that holds only one file
/// becomes that file's label.
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
    let (label, node) = collapse(node, name);
    let path = format!("{prefix}{label}");
    if node.is_file {
        out.push(DiffTreeRow {
            kind: DiffTreeKind::File,
            label,
            depth,
            path: path.clone(),
        });
        if !node.children.is_empty() {
            let next = format!("{path}/");
            for (child_name, child) in &node.children {
                walk(child, child_name, depth + 1, &next, out);
            }
        }
        return;
    }
    let files_only = !node.children.is_empty()
        && node
            .children
            .values()
            .all(|child| child.is_file && child.children.is_empty());
    if files_only && node.children.len() == 1 {
        let child_name = node.children.keys().next().expect("one child");
        out.push(DiffTreeRow {
            kind: DiffTreeKind::File,
            label: format!("{label}/{child_name}"),
            depth,
            path: format!("{path}/{child_name}"),
        });
        return;
    }
    out.push(DiffTreeRow {
        kind: DiffTreeKind::Dir,
        label: format!("{label}/"),
        depth,
        path: format!("{path}/"),
    });
    let next = format!("{path}/");
    for (child_name, child) in &node.children {
        walk(child, child_name, depth + 1, &next, out);
    }
}

fn collapse<'a>(mut node: &'a Node, name: &str) -> (String, &'a Node) {
    let mut parts = vec![name.to_string()];
    while !node.is_file && node.children.len() == 1 {
        let (child_name, child) = node.children.iter().next().expect("one child");
        if child.children.is_empty() {
            break;
        }
        parts.push(child_name.clone());
        node = child;
    }
    (parts.join("/"), node)
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
    fn unary_dirs_collapse_to_one_header() {
        assert_eq!(
            tree_rows(["src/groket/ui/app.py", "src/groket/ui/widgets.py"]),
            vec![
                row(DiffTreeKind::Dir, "src/groket/ui/", 0, "src/groket/ui/"),
                row(DiffTreeKind::File, "app.py", 1, "src/groket/ui/app.py"),
                row(
                    DiffTreeKind::File,
                    "widgets.py",
                    1,
                    "src/groket/ui/widgets.py"
                ),
            ]
        );
    }

    #[test]
    fn single_nested_file_is_one_row() {
        assert_eq!(
            tree_rows(["src/a.py"]),
            vec![row(DiffTreeKind::File, "src/a.py", 0, "src/a.py")]
        );
    }

    #[test]
    fn mixed_root_and_nested() {
        assert_eq!(
            tree_rows(["README.md", "src/a.py", "src/b/c.py"]),
            vec![
                row(DiffTreeKind::File, "README.md", 0, "README.md"),
                row(DiffTreeKind::Dir, "src/", 0, "src/"),
                row(DiffTreeKind::File, "a.py", 1, "src/a.py"),
                row(DiffTreeKind::File, "b/c.py", 1, "src/b/c.py"),
            ]
        );
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
