#!/usr/bin/env python3
"""Set the product version in every declaration the suite checks.

Usage: uv run python scripts/bump_version.py 0.2.1
Moves CHANGELOG.md ``## Unreleased`` notes under the new version heading.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.]+)?$")
_PYPROJECT_VERSION = re.compile(
    r'(?m)^version = "([^"]+)"$',
)


def read_pyproject_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION.search(text)
    if match is None:
        raise SystemExit(f"no project version in {root / 'pyproject.toml'}")
    return match.group(1)


def replace_assignment(path: Path, old: str, new: str, *, template: str) -> None:
    text = path.read_text(encoding="utf-8")
    needle = template.format(old)
    if needle not in text:
        raise SystemExit(f"{path}: missing {needle!r}")
    path.write_text(text.replace(needle, template.format(new), 1), encoding="utf-8")


def replace_crate_lock(root: Path, name: str, old: str, new: str) -> None:
    path = root / "Cargo.lock"
    text = path.read_text(encoding="utf-8")
    needle = f'name = "{name}"\nversion = "{old}"'
    if needle not in text:
        raise SystemExit(f"{path}: missing {name} version {old}")
    path.write_text(
        text.replace(needle, f'name = "{name}"\nversion = "{new}"', 1),
        encoding="utf-8",
    )


def promote_changelog(text: str, version: str, date: str) -> str:
    marker = "## Unreleased"
    idx = text.find(marker)
    if idx < 0:
        raise SystemExit("CHANGELOG.md has no ## Unreleased heading")
    rest = text[idx + len(marker) :]
    if rest.startswith("\n"):
        rest = rest[1:]
    next_h = re.search(r"^## ", rest, re.M)
    if next_h is None:
        body, tail = rest, ""
    else:
        body, tail = rest[: next_h.start()], rest[next_h.start() :]
    body = body.strip("\n")
    block = f"## Unreleased\n\n## {version} - {date}\n"
    if body:
        block += f"\n{body}\n\n"
    else:
        block += "\n"
    return text[:idx] + block + tail


def bump(root: Path, new: str, date: str) -> None:
    if _VERSION_RE.match(new) is None:
        raise SystemExit(f"invalid version {new!r} (want N.N.N plus optional suffix)")
    old = read_pyproject_version(root)
    if old == new:
        raise SystemExit(f"already at {new}")
    replace_assignment(root / "pyproject.toml", old, new, template='version = "{}"')
    replace_assignment(
        root / "anqa" / "__init__.py",
        old,
        new,
        template='__version__ = "{}"',
    )
    replace_assignment(
        root / "desktop" / "Cargo.toml",
        old,
        new,
        template='version = "{}"',
    )
    replace_assignment(
        root / "core" / "Cargo.toml",
        old,
        new,
        template='version = "{}"',
    )
    replace_crate_lock(root, "anqa-hud", old, new)
    replace_crate_lock(root, "anqa-core", old, new)
    log = root / "CHANGELOG.md"
    log.write_text(
        promote_changelog(log.read_text(encoding="utf-8"), new, date),
        encoding="utf-8",
    )
    sys.stdout.write(f"{old} -> {new}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="New product version (e.g. 0.2.1)")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--date",
        default=dt.date.today().isoformat(),
        help="Changelog date (YYYY-MM-DD, default: today)",
    )
    args = parser.parse_args(argv)
    bump(args.root, args.version, args.date)


if __name__ == "__main__":
    main()
