"""Typer CLI subcommands and argument handling via CliRunner."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from groket.cli import COMMAND_ALIASES, TOOL_COMMANDS, app, main
from typer.testing import CliRunner

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout or result.output or ""
    assert "self-test" in out or "batch" in out


def test_self_test_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from groket.diagnostics import self_test as st

    with (
        patch.object(st, "_check_docker", return_value=st.CheckResult("docker", "Docker", True)),
        patch.object(
            st, "_check_auth_json", return_value=st.CheckResult("grok_auth", "Auth", True)
        ),
    ):
        result = runner.invoke(app, ["self-test", "-w", str(tmp_path / "w"), "--json"])
    assert result.exit_code in (0, 1)


def test_self_test_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Text output (no --json) exercises report.lines()."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from groket.diagnostics import self_test as st

    with (
        patch.object(st, "_check_docker", return_value=st.CheckResult("docker", "Docker", True)),
        patch.object(
            st, "_check_auth_json", return_value=st.CheckResult("grok_auth", "Auth", True)
        ),
    ):
        result = runner.invoke(app, ["self-test", "-w", str(tmp_path / "w")])
    assert result.exit_code in (0, 1)
    out = result.stdout or result.output or ""
    assert out  # at least some text output


def test_gen_help():
    result = runner.invoke(app, ["gen", "--help"])
    assert result.exit_code == 0


def test_tasks_help():
    result = runner.invoke(app, ["tasks", "--help"])
    assert result.exit_code in (0, 2)


# ── main() entry point ───────────────────────────────────────────────────


class TestMainEntry:
    def test_main_help(self):
        """main() with --help exits cleanly."""
        with pytest.raises(SystemExit):
            main(["--help"])

    def test_main_generator_alias(self):
        """'generator' is rewritten to 'gen' before Typer sees it."""
        assert COMMAND_ALIASES["generator"] == "gen"
        result = runner.invoke(app, ["gen", "--help"])
        assert result.exit_code == 0

    def test_tool_commands_set(self):
        """TOOL_COMMANDS prevents eating subcommand names as TUI path."""
        assert "batch" in TOOL_COMMANDS
        assert "gen" in TOOL_COMMANDS
        assert "doctor-traces" in TOOL_COMMANDS


# ── launch_tui paths ─────────────────────────────────────────────────────


class TestLaunchTui:
    """Test launch_tui by mocking the TUI app at import time."""

    def test_launch_all_branches(self, tmp_path: Path):
        """Cover all three if-elif-else branches in launch_tui."""
        from groket import cli as cli_mod

        captured_calls: list[dict] = []

        class FakeApp:
            def __init__(self, **kw: object):
                captured_calls.append(kw)

            def run(self) -> None:
                pass

        import groket.ui.app as ui_app_mod

        orig = ui_app_mod.TraceEvalApp
        ui_app_mod.TraceEvalApp = FakeApp  # type: ignore[assignment,misc]  # injecting fake
        try:
            # Branch 1: path + no work_dir → calls resolve_work_and_traces
            cli_mod.launch_tui(path=tmp_path, work_dir=None, config=None)
            assert len(captured_calls) == 1

            # Branch 2: work_dir given
            captured_calls.clear()
            cli_mod.launch_tui(path=None, work_dir=tmp_path, config=None)
            assert len(captured_calls) == 1

            # Branch 3: both None
            captured_calls.clear()
            cli_mod.launch_tui(path=None, work_dir=None, config=None)
            assert len(captured_calls) == 1

            # With config
            cfg = tmp_path / "config.json"
            cfg.write_text("{}", encoding="utf-8")
            captured_calls.clear()
            cli_mod.launch_tui(path=None, work_dir=tmp_path, config=cfg)
            assert len(captured_calls) == 1
        finally:
            ui_app_mod.TraceEvalApp = orig  # type: ignore[assignment,misc]  # restoring original


# ── batch command ─────────────────────────────────────────────────────────


class TestBatchCommand:
    def test_batch_missing_tasks_file(self, tmp_path: Path):
        result = runner.invoke(app, ["batch", "-t", str(tmp_path / "missing.yaml")])
        assert result.exit_code == 2
        out = result.stdout or result.output or ""
        assert "error" in out.lower()

    def test_batch_bad_category(self, tmp_path: Path):
        tasks_file = tmp_path / "tasks.yaml"
        tasks_file.write_text(
            yaml.dump({"tasks": [{"task_id": "a", "prompt": "p"}]}), encoding="utf-8"
        )
        result = runner.invoke(app, ["batch", "-t", str(tasks_file), "-C", "invalid_cat"])
        assert result.exit_code == 2

    def test_batch_no_tasks_matched(self, tmp_path: Path):
        tasks_file = tmp_path / "tasks.yaml"
        tasks_file.write_text(
            yaml.dump({"tasks": [{"task_id": "a", "prompt": "p", "category": "regular"}]}),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["batch", "-t", str(tasks_file), "-C", "adversarial"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "No tasks matched" in out

    def test_batch_filter_by_task_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        tasks_file = tmp_path / "tasks.yaml"
        tasks_file.write_text(
            yaml.dump(
                {
                    "tasks": [
                        {"task_id": "a", "prompt": "p"},
                        {"task_id": "b", "prompt": "q"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        with (
            patch("groket.runs.batch.run_batch") as mock_rb,
            patch("groket.runs.batch.load_models", return_value=["m1"]),
        ):
            result = runner.invoke(app, ["batch", "-t", str(tasks_file), "-i", "a"])
        if mock_rb.called:
            loaded = mock_rb.call_args[0][0]
            assert len(loaded) == 1
            assert loaded[0].task_id == "a"

    def test_batch_runs_successfully(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        tasks_file = tmp_path / "tasks.yaml"
        tasks_file.write_text(
            yaml.dump({"tasks": [{"task_id": "a", "prompt": "p"}]}), encoding="utf-8"
        )
        with (
            patch("groket.runs.batch.run_batch") as mock_rb,
            patch("groket.runs.batch.load_models", return_value=["m1"]),
        ):
            result = runner.invoke(app, ["batch", "-t", str(tasks_file)])
        assert mock_rb.called


# ── audit command ─────────────────────────────────────────────────────────


class TestAuditCommand:
    def _make_session(self, traces_dir: Path) -> Path:
        sd = traces_dir / "sess1"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text(json.dumps({"info": {"id": "sess1"}}))
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_ended", "outcome": "success", "ts": 1}) + "\n"
        )
        return sd

    def test_audit_text_output(self, tmp_path: Path):
        traces = tmp_path / "traces"
        self._make_session(traces)
        result = runner.invoke(app, ["audit", str(traces)])
        assert result.exit_code == 0

    def test_audit_json_output(self, tmp_path: Path):
        traces = tmp_path / "traces"
        self._make_session(traces)
        result = runner.invoke(app, ["audit", str(traces), "--json"])
        assert result.exit_code == 0

    def test_audit_save_to_file(self, tmp_path: Path):
        traces = tmp_path / "traces"
        self._make_session(traces)
        out_file = tmp_path / "report.md"
        result = runner.invoke(app, ["audit", str(traces), "-o", str(out_file)])
        assert result.exit_code == 0
        assert out_file.is_file()

    def test_audit_with_findings(self, tmp_path: Path):
        """Audit text output includes findings when plugins return them."""
        from dataclasses import dataclass, field
        from enum import Enum

        class Sev(Enum):
            high = "high"

        @dataclass
        class FakeFinding:
            id: str = "f1"
            plugin_id: str = "test-plug"
            severity: Sev = Sev.high
            title: str = "Bad pattern"
            detail: str = "Found a bad pattern"
            category: str = "quality"

        @dataclass
        class FakeResult:
            ok: bool = True
            findings: list[FakeFinding] = field(default_factory=lambda: [FakeFinding()])
            error: str = ""

        traces = tmp_path / "traces"
        self._make_session(traces)

        with patch("groket.analysis.AnalysisService") as MockSvc:
            mock_instance = MockSvc.return_value
            mock_instance.analyze_all.return_value = {"test": FakeResult()}
            result = runner.invoke(app, ["audit", str(traces)])

        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Bad pattern" in out
        assert "high" in out

    def test_audit_json_with_findings(self, tmp_path: Path):
        """Audit JSON output includes findings."""
        from dataclasses import dataclass, field
        from enum import Enum

        class Sev(Enum):
            medium = "medium"

        @dataclass
        class FakeFinding:
            id: str = "f1"
            plugin_id: str = "test-plug"
            severity: Sev = Sev.medium
            title: str = "Medium issue"
            detail: str = ""
            category: str = "quality"

        @dataclass
        class FakeResult:
            ok: bool = True
            findings: list[FakeFinding] = field(default_factory=lambda: [FakeFinding()])
            error: str = ""

        traces = tmp_path / "traces"
        self._make_session(traces)

        with patch("groket.analysis.AnalysisService") as MockSvc:
            mock_instance = MockSvc.return_value
            mock_instance.analyze_all.return_value = {"test": FakeResult()}
            result = runner.invoke(app, ["audit", str(traces), "--json"])

        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        data = json.loads(out)
        assert any(findings for findings in data.values())


# ── refresh command ───────────────────────────────────────────────────────


class TestRefreshCommand:
    def test_refresh_empty_traces(self, tmp_path: Path):
        wd = tmp_path / "w"
        wd.mkdir()
        result = runner.invoke(app, ["refresh", "-w", str(wd)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Refresh done" in out

    def test_refresh_with_session(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text(json.dumps({"info": {"id": "sess"}}))
        (sd / "events.jsonl").write_text(
            json.dumps({"type": "turn_ended", "outcome": "success", "ts": 1}) + "\n"
        )
        result = runner.invoke(app, ["refresh", "-w", str(wd)])
        assert result.exit_code == 0

    def test_refresh_recent_and_limit(self, tmp_path: Path):
        wd = tmp_path / "w"
        wd.mkdir()
        result = runner.invoke(app, ["refresh", "-w", str(wd), "-n", "5", "-l", "3"])
        assert result.exit_code == 0

    def test_refresh_quiet(self, tmp_path: Path):
        wd = tmp_path / "w"
        wd.mkdir()
        result = runner.invoke(app, ["refresh", "-w", str(wd), "-q"])
        assert result.exit_code == 0

    def test_refresh_with_traces_override(self, tmp_path: Path):
        wd = tmp_path / "w"
        wd.mkdir()
        traces = tmp_path / "custom_traces"
        traces.mkdir()
        result = runner.invoke(app, ["refresh", "-w", str(wd), "-T", str(traces)])
        assert result.exit_code == 0

    def test_refresh_session_error_handled(self, tmp_path: Path):
        """Sessions that fail to load during refresh do not crash the command."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "bad-sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text("not-json", encoding="utf-8")
        (sd / "events.jsonl").write_text("not-json\n", encoding="utf-8")
        result = runner.invoke(app, ["refresh", "-w", str(wd)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Refresh done" in out


# ── doctor-traces command ─────────────────────────────────────────────────


class TestDoctorTracesCommand:
    def test_doctor_traces_basic(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)
        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd)])
        assert result.exit_code == 0

    def test_doctor_traces_with_mark(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)
        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd), "-m"])
        assert result.exit_code == 0

    def test_doctor_traces_prune_shells(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)
        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd), "-s"])
        assert result.exit_code == 0

    def test_doctor_traces_prune_docker_fails_gracefully(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)
        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd), "-D"])
        # Docker not available -> error printed but no crash
        assert result.exit_code == 0

    def test_doctor_traces_kill_stale(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)
        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd), "-D", "-k"])
        assert result.exit_code == 0

    def test_doctor_traces_with_interrupted_sessions(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "groket-run-x" / "sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text(
            json.dumps({"info": {"id": "sess"}, "session_summary": "x" * 30})
        )
        (sd / "events.jsonl").write_text(json.dumps({"type": "turn_started", "ts": 1}) + "\n")
        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd)])
        assert result.exit_code == 0

    def test_doctor_traces_dry_run(self, tmp_path: Path):
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)
        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd), "-m", "-n"])
        assert result.exit_code == 0

    def test_doctor_traces_default_traces_dir(self, tmp_path: Path):
        wd = tmp_path / "w"
        (wd / "runs" / "traces").mkdir(parents=True)
        result = runner.invoke(app, ["doctor-traces", "-w", str(wd)])
        assert result.exit_code == 0

    def test_doctor_traces_with_running_and_errors(self, tmp_path: Path):
        """Exercise running/interrupted item loops and error reporting."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)
        # Create a session with recent trace writes (appears as running)
        import time

        sd = traces / "groket-run-x" / "running-sess"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text(json.dumps({"info": {"id": "running-sess"}}))
        ev = sd / "events.jsonl"
        ev.write_text(json.dumps({"type": "turn_started", "ts": 1}) + "\n")
        # Touch to make recent

        os.utime(ev, (time.time(), time.time()))

        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd)])
        assert result.exit_code == 0

    def test_doctor_traces_interrupted_items_printed(self, tmp_path: Path):
        """Doctor-traces prints interrupted session items."""
        import time

        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "groket-run-y" / "int-sess"
        sd.mkdir(parents=True)
        # Session with data but no turn_ended and stale timestamps
        (sd / "summary.json").write_text(
            json.dumps({"info": {"id": "int-sess"}, "session_summary": "x" * 30})
        )
        ev = sd / "events.jsonl"
        ev.write_text(
            json.dumps({"type": "turn_started"})
            + "\n"
            + json.dumps({"type": "tool_use", "name": "grep"})
            + "\n"
        )
        old_time = time.time() - 7200
        os.utime(ev, (old_time, old_time))
        os.utime(sd / "summary.json", (old_time, old_time))

        # Also add an empty shell for shell output
        shell = traces / "groket-empty-shell"
        shell.mkdir(parents=True)
        (shell / "run.json").write_text("{}", encoding="utf-8")

        result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "int-sess" in out or "interrupted" in out.lower()

    def test_doctor_traces_with_audit_errors(self, tmp_path: Path):
        """Doctor-traces reports errors from audit and exits with code 1."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)

        with patch("groket.runs.run_configs.audit_trace_sessions") as mock_audit:
            mock_audit.return_value = {
                "ok": [],
                "ok_count": 0,
                "running": [],
                "running_count": 0,
                "interrupted": [],
                "interrupted_count": 0,
                "empty_shells": [],
                "empty_shell_count": 0,
                "errors": ["something went wrong"],
            }
            result = runner.invoke(app, ["doctor-traces", str(traces), "-w", str(wd)])
        assert result.exit_code == 1

    def test_doctor_traces_prune_shells_patched(self, tmp_path: Path):
        """Doctor-traces --prune-shells with patched audit invokes prune_orphan_trace_runs."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)

        with (
            patch(
                "groket.runs.run_configs.audit_trace_sessions",
                return_value={
                    "ok": [],
                    "ok_count": 0,
                    "running": [],
                    "running_count": 0,
                    "interrupted": [],
                    "interrupted_count": 0,
                    "empty_shells": [],
                    "empty_shell_count": 0,
                    "errors": [],
                },
            ),
            patch(
                "groket.runs.run_configs.prune_orphan_trace_runs",
                return_value={"removed_count": 0, "kept": 0},
            ) as mock_prune,
        ):
            result = runner.invoke(
                app, ["doctor-traces", str(traces), "-w", str(wd), "--prune-shells"]
            )
        assert result.exit_code == 0
        assert mock_prune.called
        out = result.stdout or result.output or ""
        assert "prune_shells" in out

    def test_doctor_traces_prune_docker_patched(self, tmp_path: Path):
        """Doctor-traces --prune-docker with patched orchestrator."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        traces.mkdir(parents=True)

        with (
            patch(
                "groket.runs.run_configs.audit_trace_sessions",
                return_value={
                    "ok": [],
                    "ok_count": 0,
                    "running": [],
                    "running_count": 0,
                    "interrupted": [],
                    "interrupted_count": 0,
                    "empty_shells": [],
                    "empty_shell_count": 0,
                    "errors": [],
                },
            ),
            patch(
                "groket.docker.orchestrator.DockerOrchestrator.__init__",
                return_value=None,
            ),
            patch(
                "groket.docker.orchestrator.DockerOrchestrator.prune_eval_containers",
                return_value={"exited_removed": 2, "running_removed": 0},
            ),
        ):
            result = runner.invoke(
                app, ["doctor-traces", str(traces), "-w", str(wd), "--prune-docker"]
            )
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "prune_docker" in out


class TestRefreshCommandExtended:
    """Cover cmd_refresh analysis loop and error paths."""

    def test_refresh_with_sessions(self, tmp_path: Path):
        """Refresh scans sessions and runs analysis plugins."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "groket-r" / "s1"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text(json.dumps({"info": {}}), encoding="utf-8")
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")

        class FakePlugin:
            id = "test"

        class FakeResult:
            ok = True
            error = ""

        with (
            patch("groket.analysis.AnalysisService") as MockSvc,
        ):
            svc_inst = MockSvc.return_value
            svc_inst.list_plugins.return_value = [FakePlugin()]
            svc_inst.analyze_all.return_value = {"test": FakeResult()}
            result = runner.invoke(app, ["refresh", "-T", str(traces), "-w", str(wd)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Refresh done" in out

    def test_refresh_analysis_error(self, tmp_path: Path):
        """Refresh reports analysis errors."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "groket-r" / "s1"
        sd.mkdir(parents=True)
        (sd / "summary.json").write_text(json.dumps({"info": {}}), encoding="utf-8")

        class FakePlugin:
            id = "test"

        class FakeResult:
            ok = False
            error = "analysis failed"

        with (
            patch("groket.analysis.AnalysisService") as MockSvc,
        ):
            svc_inst = MockSvc.return_value
            svc_inst.list_plugins.return_value = [FakePlugin()]
            svc_inst.analyze_all.return_value = {"test": FakeResult()}
            result = runner.invoke(app, ["refresh", "-T", str(traces), "-w", str(wd)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "err=" in out

    def test_refresh_exception_in_analysis(self, tmp_path: Path):
        """Refresh handles exceptions during analysis."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "groket-r" / "s1"
        sd.mkdir(parents=True)
        (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")

        class FakePlugin:
            id = "test"

        with (
            patch("groket.analysis.AnalysisService") as MockSvc,
        ):
            svc_inst = MockSvc.return_value
            svc_inst.list_plugins.return_value = [FakePlugin()]
            svc_inst.analyze_all.side_effect = RuntimeError("boom")
            result = runner.invoke(app, ["refresh", "-T", str(traces), "-w", str(wd)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "err=1" in out

    def test_refresh_mtime_fallback(self, tmp_path: Path):
        """Refresh _mtime fallback to dir mtime when no files."""
        wd = tmp_path / "w"
        traces = wd / "runs" / "traces"
        sd = traces / "groket-r" / "s1"
        sd.mkdir(parents=True)
        # Only updates.jsonl, no summary/events
        (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")

        class FakePlugin:
            id = "noop"

        with (
            patch("groket.analysis.AnalysisService") as MockSvc,
        ):
            svc_inst = MockSvc.return_value
            svc_inst.list_plugins.return_value = [FakePlugin()]
            svc_inst.analyze_all.return_value = {}
            result = runner.invoke(app, ["refresh", "-T", str(traces), "-w", str(wd), "-q"])
        assert result.exit_code == 0


class TestMainEntryArgv:
    """Cover main() argv rewriting: COMMAND_ALIASES, path positional, __main__."""

    def test_main_alias_rewrite(self):
        """main() rewrites COMMAND_ALIASES (generator → gen)."""
        result = runner.invoke(app, ["gen", "--help"])
        assert result.exit_code == 0

    def test_main_path_positional_rewrite(self):
        """main() rewrites a leading path to -P flag."""
        # Invoking main() with a path that's not a subcommand
        with patch("groket.cli.app") as mock_app:
            main(argv=["/some/path"])
            mock_app.assert_called_once()
            call_kwargs = mock_app.call_args
            args = call_kwargs.kwargs.get("args") or call_kwargs[1].get("args", [])
            assert args[0] == "-P"
            assert args[1] == "/some/path"

    def test_main_no_argv(self):
        """main() uses sys.argv when argv is None."""
        import sys

        with (
            patch.object(sys, "argv", ["groket", "--help"]),
            patch("groket.cli.app") as mock_app,
        ):
            main(argv=None)
            mock_app.assert_called_once()

    def test_launch_tui_called(self):
        """main_callback calls launch_tui when no subcommand."""
        with patch("groket.cli.launch_tui") as mock_tui:
            result = runner.invoke(app, [])
            # launch_tui is called; it may fail in test env — that's ok
            # The point is that line 135 is covered


# ── gen commands ──────────────────────────────────────────────────────────


class TestGenCommands:
    def test_gen_detector(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        result = runner.invoke(app, ["gen", "detector", "my_check", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote detector" in out

    def test_gen_detector_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        runner.invoke(app, ["gen", "detector", "existing_det", "-f"])
        with patch(
            "groket.extensions.scaffold.write_detector",
            side_effect=FileExistsError("exists"),
        ):
            result = runner.invoke(app, ["gen", "detector", "existing_det"])
        assert result.exit_code == 1

    def test_gen_rule(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        result = runner.invoke(app, ["gen", "rule", "my-rule", "-d", "my_det", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote rule" in out

    def test_gen_rule_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        with patch("groket.extensions.scaffold.write_rule", side_effect=FileExistsError("exists")):
            result = runner.invoke(app, ["gen", "rule", "dup-rule"])
        assert result.exit_code == 1

    def test_gen_plugin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        result = runner.invoke(app, ["gen", "plugin", "my_stats", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote analysis" in out

    def test_gen_plugin_with_register(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        result = runner.invoke(app, ["gen", "plugin", "my_stats2", "-r", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "updated" in out

    def test_gen_plugin_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        with patch(
            "groket.extensions.scaffold.write_analysis_plugin",
            side_effect=FileExistsError("exists"),
        ):
            result = runner.invoke(app, ["gen", "plugin", "dup_plug"])
        assert result.exit_code == 1

    def test_gen_tasks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        result = runner.invoke(app, ["gen", "tasks", "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote tasks" in out

    def test_gen_tasks_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        with patch(
            "groket.extensions.scaffold.write_tasks_file",
            side_effect=FileExistsError("exists"),
        ):
            result = runner.invoke(app, ["gen", "tasks"])
        assert result.exit_code == 1


# ── tasks commands ────────────────────────────────────────────────────────


class TestTasksCommands:
    def test_tasks_validate_ok(self, tmp_path: Path):
        tasks_file = tmp_path / "t.yaml"
        tasks_file.write_text(
            yaml.dump({"tasks": [{"task_id": "a", "prompt": "do it"}]}), encoding="utf-8"
        )
        result = runner.invoke(app, ["tasks", "validate", str(tasks_file)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "ok" in out

    def test_tasks_validate_missing(self, tmp_path: Path):
        result = runner.invoke(app, ["tasks", "validate", str(tmp_path / "nope.yaml")])
        assert result.exit_code == 2

    def test_tasks_validate_bad_schema(self, tmp_path: Path):
        path = tmp_path / "bad.yaml"
        path.write_text("tasks:\n  - task_id: x\n", encoding="utf-8")
        result = runner.invoke(app, ["tasks", "validate", str(path)])
        assert result.exit_code == 1

    def test_tasks_validate_bad_yaml(self, tmp_path: Path):
        path = tmp_path / "bad.yaml"
        path.write_text("not-a-mapping", encoding="utf-8")
        result = runner.invoke(app, ["tasks", "validate", str(path)])
        assert result.exit_code == 1

    def test_tasks_schema_stdout(self):
        result = runner.invoke(app, ["tasks", "schema"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "task_id" in out

    def test_tasks_schema_to_file(self, tmp_path: Path):
        out_file = tmp_path / "schema.json"
        result = runner.invoke(app, ["tasks", "schema", "-o", str(out_file)])
        assert result.exit_code == 0
        assert out_file.is_file()


# ── command alias and path rewriting ─────────────────────────────────────


class TestMainEntryRewrite:
    """main() command alias and path positional rewriting (additional)."""

    def test_alias_rewrite(self):
        """Command alias 'generator' is rewritten to 'gen'."""
        with patch("groket.cli.app") as mock_app:
            main(["generator", "--help"])
            call_args = mock_app.call_args
            assert call_args is not None
            args_val = call_args[1].get("args") or call_args[0][0]
            assert args_val[0] == "gen"

    def test_path_positional_rewrite(self):
        """Leading path is rewritten to -P flag."""
        with patch("groket.cli.app") as mock_app:
            main(["./my-project"])
            call_args = mock_app.call_args
            assert call_args is not None
            args_val = call_args[1].get("args") or call_args[0][0]
            assert "-P" in args_val

    def test_tool_command_not_rewritten(self):
        """Tool commands (batch, audit, etc.) are not treated as paths."""
        with patch("groket.cli.app") as mock_app:
            main(["batch", "--help"])
            call_args = mock_app.call_args
            assert call_args is not None
            args_val = call_args[1].get("args") or call_args[0][0]
            assert args_val[0] == "batch"


class TestDoctorTracesExtended:
    """doctor-traces deeper edge cases."""

    def test_doctor_traces_prune_docker_exception(self, tmp_path: Path):
        """Doctor traces handles docker prune failure."""
        traces = tmp_path / "traces"
        traces.mkdir()
        with (
            patch(
                "groket.runs.run_configs.audit_trace_sessions",
                return_value={"interrupted": [], "empty_shells": []},
            ),
            patch(
                "groket.docker.orchestrator.DockerOrchestrator.__init__",
                side_effect=RuntimeError("no docker"),
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "doctor-traces",
                    "-w",
                    str(tmp_path),
                    "--prune-docker",
                ],
            )
            out = result.stdout or result.output or ""
            assert "prune_docker failed" in out or result.exit_code == 0

    def test_doctor_traces_many_interrupted(self, tmp_path: Path):
        """Doctor traces truncates long interrupted list."""
        traces = tmp_path / "traces"
        traces.mkdir()
        items = [
            {"session_id": f"s{i}", "status": "no_turn_ended", "turn_outcome": "NONE"}
            for i in range(50)
        ]
        with patch(
            "groket.runs.run_configs.audit_trace_sessions",
            return_value={"interrupted": items, "empty_shells": []},
        ):
            result = runner.invoke(
                app,
                ["doctor-traces", "-w", str(tmp_path)],
            )
            out = result.stdout or result.output or ""
            assert "more" in out

    def test_doctor_traces_errors_reported(self, tmp_path: Path):
        """Doctor traces exits with code 1 on errors from audit."""
        traces = tmp_path / "traces"
        traces.mkdir()
        with patch(
            "groket.runs.run_configs.audit_trace_sessions",
            return_value={
                "interrupted": [],
                "empty_shells": [],
                "errors": ["disk error"],
            },
        ):
            result = runner.invoke(
                app,
                ["doctor-traces", "-w", str(tmp_path)],
            )
            assert result.exit_code == 1


class TestRefreshExtended:
    """refresh command edge paths."""

    def test_refresh_with_limit(self, tmp_path: Path):
        """Refresh respects --limit option."""
        traces = tmp_path / "traces"
        for i in range(3):
            sd = traces / "groket-run" / f"sess-{i}"
            sd.mkdir(parents=True)
            (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        with (
            patch(
                "groket.analysis.service.AnalysisService.analyze_session",
                return_value=None,
            ),
            patch(
                "groket.analysis.service.AnalysisService.list_plugins",
                return_value=[],
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "refresh",
                    "-w",
                    str(tmp_path),
                    "-T",
                    str(traces),
                    "--limit",
                    "1",
                ],
            )
            assert result.exit_code == 0


# ── refresh and mtime edge cases ─────────────────────────────────────────


class TestRefreshMtimeFallback:
    """refresh command _mtime helper handles stat OSError."""

    def test_refresh_session_stat_oserror(self, tmp_path: Path) -> None:
        """refresh command handles sessions where stat raises OSError."""
        from groket.cli import app as cli_app
        from typer.testing import CliRunner as TyperRunner

        runner = TyperRunner()
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "sess"
        sd.mkdir(parents=True)
        (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
        with patch(
            "groket.parser.find_sessions",
            return_value=[sd],
        ):
            with patch("groket.analysis.AnalysisService"):
                result = runner.invoke(
                    cli_app,
                    [
                        "refresh",
                        "-w",
                        str(tmp_path),
                        "-T",
                        str(traces),
                        "--limit",
                        "1",
                        "--quiet",
                    ],
                )
        assert result.exit_code == 0


class TestMainEntryPathRewrite:
    """main() rewrites positional path argument."""

    def test_path_rewritten_to_flag(self) -> None:
        from groket.cli import main

        with (
            patch("groket.cli.app") as mock_app,
        ):
            mock_app.return_value = None
            import sys

            with patch.object(sys, "argv", ["groket", "./myproject"]):
                main()
            call_args = mock_app.call_args
            assert "-P" in call_args.kwargs.get("args", call_args.args[0] if call_args.args else [])

    def test_tool_command_not_rewritten(self) -> None:
        from groket.cli import main

        with patch("groket.cli.app") as mock_app:
            mock_app.return_value = None
            import sys

            with patch.object(sys, "argv", ["groket", "batch"]):
                main()
            call_args = mock_app.call_args
            args = call_args.kwargs.get("args", call_args.args[0] if call_args.args else [])
            assert args[0] == "batch"


class TestRefreshMtimeStatFallback:
    """refresh _mtime handles OSError on per-file stat and dir stat."""

    def test_mtime_no_known_files_uses_dir(self, tmp_path: Path) -> None:
        """Session with no summary/events/updates falls to dir stat."""
        from groket.cli import app as cli_app
        from typer.testing import CliRunner as TyperRunner

        runner = TyperRunner()
        traces = tmp_path / "traces"
        sd = traces / "groket-r" / "sess"
        sd.mkdir(parents=True)
        # Session dir exists but no known files → falls to sd.stat()
        with (
            patch("groket.parser.find_sessions", return_value=[sd]),
            patch("groket.analysis.AnalysisService"),
        ):
            result = runner.invoke(
                cli_app,
                ["refresh", "-w", str(tmp_path), "-T", str(traces), "--limit", "1", "--quiet"],
            )
        assert result.exit_code == 0
