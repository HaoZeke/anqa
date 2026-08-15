"""Python package version and crate versions are the same product version."""

from __future__ import annotations

import tomllib
from pathlib import Path

import groket

ROOT = Path(__file__).resolve().parents[1]


def _toml_string(data: object, *keys: str) -> str:
    cur: object = data
    for key in keys:
        assert isinstance(cur, dict)
        cur = cur[key]
    assert isinstance(cur, str) and cur
    return cur


def _manifest_version(path: Path, *keys: str) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return _toml_string(data, *keys)


def test_product_versions_match() -> None:
    python = _manifest_version(ROOT / "pyproject.toml", "project", "version")
    hud = _manifest_version(ROOT / "groket-hud" / "Cargo.toml", "package", "version")
    core = _manifest_version(ROOT / "native" / "groket-core" / "Cargo.toml", "package", "version")
    assert python == hud == core == groket.__version__
