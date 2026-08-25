"""Collapsed directory tree rows for Diff file lists."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiffTreeRow:
    """One directory header or file leaf in a Diff file tree."""

    kind: str
    label: str
    depth: int
    path: str


@dataclass
class _Node:
    children: dict[str, _Node] = field(default_factory=dict)
    is_file: bool = False


def tree_rows(paths: list[str] | tuple[str, ...]) -> list[DiffTreeRow]:
    """Build collapsed tree rows from path strings.

    Unary directory chains merge (``src/anqa/ui/``). A directory that
    holds only one file becomes that file's label (``src/a.py``).

    :param paths: File paths, any order. Backslashes become slashes.
    :returns: Depth-first rows, directories before their children.
    """
    root = _Node()
    for raw in paths:
        parts = [p for p in raw.replace("\\", "/").strip().strip("/").split("/") if p]
        if not parts:
            continue
        node = root
        for part in parts:
            node = node.children.setdefault(part, _Node())
        node.is_file = True
    out: list[DiffTreeRow] = []
    _walk(root, "", 0, "", out)
    return out


def _walk(node: _Node, name: str, depth: int, prefix: str, out: list[DiffTreeRow]) -> None:
    if not name:
        for child_name in sorted(node.children):
            _walk(node.children[child_name], child_name, depth, prefix, out)
        return
    label, node = _collapse(node, name)
    path = f"{prefix}{label}"
    if node.is_file:
        out.append(DiffTreeRow("file", label, depth, path))
        if node.children:
            for child_name in sorted(node.children):
                _walk(node.children[child_name], child_name, depth + 1, f"{path}/", out)
        return
    files_only = node.children and all(
        child.is_file and not child.children for child in node.children.values()
    )
    if files_only and len(node.children) == 1:
        child_name = next(iter(node.children))
        out.append(DiffTreeRow("file", f"{label}/{child_name}", depth, f"{path}/{child_name}"))
        return
    out.append(DiffTreeRow("dir", f"{label}/", depth, f"{path}/"))
    for child_name in sorted(node.children):
        _walk(node.children[child_name], child_name, depth + 1, f"{path}/", out)


def _collapse(node: _Node, name: str) -> tuple[str, _Node]:
    parts = [name]
    cur = node
    while not cur.is_file and len(cur.children) == 1:
        child_name, child = next(iter(cur.children.items()))
        if not child.children:
            break
        parts.append(child_name)
        cur = child
    return "/".join(parts), cur
