"""Packaged Neovim client layout and loadable entrypoints."""

from __future__ import annotations

from pathlib import Path

from groket.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_vim_runtime_layout_matches_cli() -> None:
    result = runner.invoke(app, ["vim-path"])
    assert result.exit_code == 0
    root = Path(result.stdout.strip())
    init = (root / "lua" / "groket" / "init.lua").read_text(encoding="utf-8")
    plugin = (root / "plugin" / "groket.lua").read_text(encoding="utf-8")
    assert "session/render" in init
    assert "session/list" in init
    assert 'format = M.config.format' in init or 'format = M.config.format or "markdown"' in init
    assert "notes/upsert" in init
    assert "GroketOpenSession" in init
    assert "GroketFindSession" in init
    assert "GroketSessions" in init
    assert "float_fuzzy_pick" in init
    assert "parse_groket_comment" in init
    assert "<leader>gs" in init
    assert 'require("groket").setup()' in plugin
