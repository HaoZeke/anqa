"""scripts/bump_version.py updates every product version and the changelog."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bump_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(root: Path, *, version: str = "0.2.0") -> None:
    (root / "pyproject.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    (root / "anqa").mkdir()
    (root / "anqa" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    hud = root / "desktop"
    hud.mkdir()
    (hud / "Cargo.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    scan = root / "scan"
    scan.mkdir()
    (scan / "Cargo.toml").write_text(f'version = "{version}"\n', encoding="utf-8")
    (root / "Cargo.lock").write_text(
        f'name = "anqa-hud"\nversion = "{version}"\nname = "anqa-scan"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n### Added\n\n- a thing\n",
        encoding="utf-8",
    )


def test_promote_changelog_moves_unreleased_notes() -> None:
    bump = _load()
    out = bump.promote_changelog(
        "# Changelog\n\n## Unreleased\n\n### Added\n\n- a thing\n",
        "0.2.1",
        "2026-08-15",
    )
    assert out.startswith("# Changelog\n\n## Unreleased\n\n## 0.2.1 - 2026-08-15\n")
    assert "### Added\n\n- a thing\n" in out
    assert out.index("## Unreleased") < out.index("## 0.2.1")


def test_bump_rewrites_declarations_and_changelog(tmp_path: Path) -> None:
    _seed(tmp_path)
    bump = _load()
    bump.bump(tmp_path, "0.2.1", "2026-08-15")
    assert 'version = "0.2.1"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "0.2.1"' in (tmp_path / "anqa" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert 'version = "0.2.1"' in (tmp_path / "desktop" / "Cargo.toml").read_text(encoding="utf-8")
    lock = (tmp_path / "Cargo.lock").read_text(encoding="utf-8")
    assert 'name = "anqa-hud"\nversion = "0.2.1"' in lock
    assert 'name = "anqa-scan"\nversion = "0.2.1"' in lock
    assert 'version = "0.2.1"' in (tmp_path / "scan" / "Cargo.toml").read_text(encoding="utf-8")
    log = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.2.1 - 2026-08-15" in log
    assert "- a thing" in log


def test_bump_rejects_same_version(tmp_path: Path) -> None:
    _seed(tmp_path)
    bump = _load()
    with pytest.raises(SystemExit, match="already at"):
        bump.bump(tmp_path, "0.2.0", "2026-08-15")


def test_bump_rejects_invalid_version(tmp_path: Path) -> None:
    _seed(tmp_path)
    bump = _load()
    with pytest.raises(SystemExit, match="invalid version"):
        bump.bump(tmp_path, "v2", "2026-08-15")
