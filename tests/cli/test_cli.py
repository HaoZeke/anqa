"""Typer CLI: TUI launch and ``groket gen`` scaffolds."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from groket.cli import TOOL_COMMANDS, app, launch_tui, main
from typer.testing import CliRunner

runner = CliRunner()


def test_help_lists_gen_and_self_test() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout or result.output or ""
    assert "gen" in out
    assert "self-test" in out
    assert "batch" not in out
    assert "audit" not in out
    result2 = runner.invoke(app, ["gen", "--help"])
    assert result2.exit_code == 0


def test_tool_commands() -> None:
    assert TOOL_COMMANDS == frozenset({"gen", "generator", "self-test"})


class TestSelfTestCommand:
    def test_self_test_json_no_tui(self, tmp_path: Path) -> None:
        """self-test runs diagnostics and never constructs TraceEvalApp."""
        from groket.diagnostics.self_test import CheckResult, SelfTestReport

        report = SelfTestReport(
            checks=[
                CheckResult(
                    id="x",
                    name="X",
                    ok=True,
                    required=True,
                    detail="fine",
                )
            ]
        )
        with (
            patch("groket.diagnostics.run_self_test", return_value=report) as mock_run,
            patch("groket.ui.app.TraceEvalApp") as mock_app,
        ):
            result = runner.invoke(app, ["self-test", "-P", str(tmp_path), "--json"])
            mock_run.assert_called_once()
            mock_app.assert_not_called()
            assert result.exit_code == 0
            out = result.stdout or result.output or ""
            assert '"ok"' in out

    def test_self_test_text(self, tmp_path: Path) -> None:
        from groket.diagnostics.self_test import CheckResult, SelfTestReport

        report = SelfTestReport(
            checks=[
                CheckResult(
                    id="x",
                    name="X",
                    ok=True,
                    required=True,
                    detail="fine",
                )
            ]
        )
        with patch("groket.diagnostics.run_self_test", return_value=report):
            result = runner.invoke(app, ["self-test", "-P", str(tmp_path)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "X" in out or "fine" in out

    def test_self_test_not_rewritten_as_path(self) -> None:
        with patch("groket.cli.app") as mock_app:
            main(argv=["self-test", "--json"])
            args = mock_app.call_args.kwargs.get("args") or mock_app.call_args[1].get("args", [])
            assert args[0] == "self-test"


class TestLaunchTui:
    def test_launch_resolves_path(self, tmp_path: Path) -> None:
        captured_calls: list[dict] = []

        class FakeApp:
            def __init__(self, **kw: object):
                captured_calls.append(kw)

            def run(self) -> None:
                pass

        import groket.ui.app as ui_app_mod

        orig = ui_app_mod.TraceEvalApp
        ui_app_mod.TraceEvalApp = FakeApp  # type: ignore[assignment,misc]
        try:
            launch_tui(path=tmp_path, config=None)
            assert len(captured_calls) == 1
            assert captured_calls[0]["work_dir"] == tmp_path.resolve()

            captured_calls.clear()
            launch_tui(path=None, config=None)
            assert len(captured_calls) == 1

            cfg = tmp_path / "config.json"
            cfg.write_text("{}", encoding="utf-8")
            captured_calls.clear()
            launch_tui(path=tmp_path, config=cfg)
            assert captured_calls[0]["config_path"] == cfg.expanduser()
        finally:
            ui_app_mod.TraceEvalApp = orig  # type: ignore[assignment,misc]


class TestMainEntryArgv:
    def test_main_alias_rewrite(self) -> None:
        with patch("groket.cli.app") as mock_app:
            main(argv=["generator", "--help"])
            mock_app.assert_called_once()
            args = mock_app.call_args.kwargs.get("args") or mock_app.call_args[1].get("args", [])
            assert args[0] == "gen"

    def test_main_path_positional_rewrite(self) -> None:
        with patch("groket.cli.app") as mock_app:
            main(argv=["/some/path"])
            mock_app.assert_called_once()
            call_kwargs = mock_app.call_args
            args = call_kwargs.kwargs.get("args") or call_kwargs[1].get("args", [])
            assert args[0] == "-P"
            assert args[1] == "/some/path"

    def test_main_no_argv(self) -> None:
        import sys

        with (
            patch.object(sys, "argv", ["groket", "--help"]),
            patch("groket.cli.app") as mock_app,
        ):
            main(argv=None)
            mock_app.assert_called_once()


class TestGenCommands:
    def test_gen_detector(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        result = runner.invoke(app, ["gen", "detector", "my_check", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote detector" in out

    def test_gen_detector_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner.invoke(app, ["gen", "detector", "existing_det", "-f"])
        with patch(
            "groket.extensions.scaffold.write_detector",
            side_effect=FileExistsError("exists"),
        ):
            result = runner.invoke(app, ["gen", "detector", "existing_det"])
        assert result.exit_code == 1

    def test_gen_rule(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        result = runner.invoke(app, ["gen", "rule", "my-rule", "-d", "my_det", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote rule" in out

    def test_gen_rule_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch("groket.extensions.scaffold.write_rule", side_effect=FileExistsError("exists")):
            result = runner.invoke(app, ["gen", "rule", "dup-rule"])
        assert result.exit_code == 1

    def test_gen_plugin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        result = runner.invoke(app, ["gen", "plugin", "my_stats", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote" in out and "plugin" in out.lower()

    def test_gen_tasks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        out_path = tmp_path / "tasks.yaml"
        result = runner.invoke(app, ["gen", "tasks", str(out_path), "-f"])
        assert result.exit_code == 0
        assert out_path.is_file() or "Wrote" in (result.stdout or result.output or "")
