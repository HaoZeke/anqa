"""Collapsed Diff file-tree rows."""

from __future__ import annotations

from anqa.diff_tree import DiffTreeRow, tree_rows


def test_root_files_stay_flat() -> None:
    assert tree_rows(["app.py", "added.py"]) == [
        DiffTreeRow("file", "added.py", 0, "added.py"),
        DiffTreeRow("file", "app.py", 0, "app.py"),
    ]


def test_unary_dirs_collapse_to_one_header() -> None:
    rows = tree_rows(["src/anqa/ui/app.py", "src/anqa/ui/widgets.py"])
    assert rows == [
        DiffTreeRow("dir", "src/anqa/ui/", 0, "src/anqa/ui/"),
        DiffTreeRow("file", "app.py", 1, "src/anqa/ui/app.py"),
        DiffTreeRow("file", "widgets.py", 1, "src/anqa/ui/widgets.py"),
    ]


def test_single_nested_file_is_one_row() -> None:
    assert tree_rows(["src/a.py"]) == [
        DiffTreeRow("file", "src/a.py", 0, "src/a.py"),
    ]


def test_mixed_root_and_nested() -> None:
    rows = tree_rows(["README.md", "src/a.py", "src/b/c.py"])
    assert rows == [
        DiffTreeRow("file", "README.md", 0, "README.md"),
        DiffTreeRow("dir", "src/", 0, "src/"),
        DiffTreeRow("file", "a.py", 1, "src/a.py"),
        DiffTreeRow("file", "b/c.py", 1, "src/b/c.py"),
    ]


def test_file_and_dir_share_a_name() -> None:
    rows = tree_rows(["foo", "foo/bar.py"])
    assert rows == [
        DiffTreeRow("file", "foo", 0, "foo"),
        DiffTreeRow("file", "bar.py", 1, "foo/bar.py"),
    ]


def test_empty_and_blank_paths() -> None:
    assert tree_rows([]) == []
    assert tree_rows(["", "/", "  "]) == []
