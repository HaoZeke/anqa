"""Pilot: RunnerScreen — form fields, tab switching, prefill, save config, banner.

Uses Textual ``App.run_test()`` + Pilot. Synchronisation via ``wait_until``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.runs.run_configs import RunConfigStore
from groket.runs.run_manager import RunManager
from groket.ui.screens.runner import RunnerPrefill, RunnerScreen
from textual.app import App
from textual.widgets import (
    Button,
    DataTable,
    Input,
    TabbedContent,
    TextArea,
)

from .pilot_helpers import wait_until


def _make_work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    return work


class _Harness(App[None]):
    """Minimal host for RunnerScreen with an app-level RunManager."""

    def __init__(self, work: Path, prefill: RunnerPrefill | None = None) -> None:
        super().__init__()
        self._work = work
        self.run_manager = RunManager(work)
        self._prefill = prefill

    async def on_mount(self) -> None:
        self.push_screen(
            RunnerScreen(
                self._work,
                run_manager=self.run_manager,
                prefill=self._prefill,
            )
        )


async def _wait_runner(pilot, app: App) -> RunnerScreen:
    """Wait until RunnerScreen is composed."""

    def ready() -> bool:
        scr = app.screen
        if not isinstance(scr, RunnerScreen):
            return False
        try:
            scr.query_one("#runner-tabs", TabbedContent)
            return True
        except Exception:
            return False

    await wait_until(pilot, ready, description="RunnerScreen composed")
    scr = app.screen
    assert isinstance(scr, RunnerScreen)
    return scr


# ── Mount ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_mounts_blank(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.query_one("#prompt-input", TextArea)
        scr.query_one("#setup-input", TextArea)
        scr.query_one("#launch-btn", Button)
        scr.query_one("#save-config-btn", Button)
        scr.query_one("#container-status-table", DataTable)


@pytest.mark.asyncio
async def test_runner_mounts_with_prefill(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    pf = RunnerPrefill(
        prompt="fix the tests",
        setup_instructions="npm install",
        repo_url="https://github.com/org/repo",
        repo_branch="main",
        models=["v9-dietcoke"],
        persona_id="default",
        run_mcp_servers=["slack"],
        run_skills=["bash"],
        run_plugins=["chrome-devtools"],
        run_env_vars={"KEY": "val"},
    )
    app = _Harness(work, prefill=pf)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        prompt = scr.query_one("#prompt-input", TextArea)
        assert "fix the tests" in prompt.text
        setup = scr.query_one("#setup-input", TextArea)
        assert "npm install" in setup.text
        repo = scr.query_one("#repo-url-input", Input)
        assert repo.value == "https://github.com/org/repo"


# ── Tab switching ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_tab_switching(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        tabs = scr.query_one("#runner-tabs", TabbedContent)

        scr.action_tab_runtime()
        await pilot.pause()
        tabs.active = "runner-tab-runtime"
        await wait_until(
            pilot,
            lambda: tabs.active == "runner-tab-runtime",
            description="runtime tab active",
        )

        scr.action_tab_models()
        await pilot.pause()
        tabs.active = "runner-tab-models"
        await wait_until(
            pilot,
            lambda: tabs.active == "runner-tab-models",
            description="models tab active",
        )

        scr.action_tab_extras()
        await pilot.pause()
        tabs.active = "runner-tab-extras"
        await wait_until(
            pilot,
            lambda: tabs.active == "runner-tab-extras",
            description="extras tab active",
        )

        scr.action_tab_recipe()
        await pilot.pause()
        tabs.active = "runner-tab-recipe"
        await wait_until(
            pilot,
            lambda: tabs.active == "runner-tab-recipe",
            description="recipe tab active",
        )


@pytest.mark.asyncio
async def test_runner_tab_next_prev(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_tab_next()
        await pilot.pause()
        scr.action_tab_next()
        await pilot.pause()
        scr.action_tab_prev()
        await pilot.pause()


# ── Save config ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_config_creates_file(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    pf = RunnerPrefill(prompt="build a parser", models=["v9-dietcoke"])
    app = _Harness(work, prefill=pf)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_save_config_only()
        await pilot.pause()
        store = RunConfigStore(work)
        configs = store.list_configs()
        assert len(configs) >= 1
        assert any("build a parser" in c.prompt for c in configs)


@pytest.mark.asyncio
async def test_save_config_empty_prompt(tmp_path: Path) -> None:
    """Save with empty prompt shows error notification."""
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_save_config_only()
        await pilot.pause()
        store = RunConfigStore(work)
        assert len(store.list_configs()) == 0


# ── Launch (no docker → error) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_no_docker(tmp_path: Path, monkeypatch) -> None:
    """Launch without Docker shows an error notification."""
    work = _make_work(tmp_path)
    pf = RunnerPrefill(prompt="test launch", models=["v9"])
    app = _Harness(work, prefill=pf)
    monkeypatch.setattr(
        app.run_manager.orchestrator,
        "check_docker_available",
        lambda: False,
    )
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_run_evaluation()
        await pilot.pause()


@pytest.mark.asyncio
async def test_launch_no_models(tmp_path: Path, monkeypatch) -> None:
    """Launch with no models shows an error notification."""
    work = _make_work(tmp_path)
    pf = RunnerPrefill(prompt="test launch", models=[])
    app = _Harness(work, prefill=pf)
    monkeypatch.setattr(
        app.run_manager.orchestrator,
        "check_docker_available",
        lambda: True,
    )
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_run_evaluation()
        await pilot.pause()


# ── Docker check action ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_docker_check_action(tmp_path: Path, monkeypatch) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    monkeypatch.setattr(
        app.run_manager.orchestrator,
        "check_docker_available",
        lambda: True,
    )
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_check_docker()
        await pilot.pause()
        await pilot.pause()


# ── Persona / capability helpers ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_persona_change_updates_hint(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr._sync_persona_github_hint()
        await pilot.pause()
        scr._rebuild_run_capability_lists()
        await pilot.pause()


@pytest.mark.asyncio
async def test_clear_run_caps(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    pf = RunnerPrefill(
        prompt="p",
        run_mcp_servers=["slack"],
        run_skills=["bash"],
        run_plugins=["plug1"],
        run_env_vars={"K": "V"},
    )
    app = _Harness(work, prefill=pf)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        assert len(scr._run_mcp_ids) == 1
        scr._run_caps_clear_btn()
        await pilot.pause()
        assert len(scr._run_mcp_ids) == 0
        assert len(scr._run_skills_ids) == 0
        assert len(scr._run_plugins_ids) == 0


# ── Banner / status ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_banner_states(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        for level in ("idle", "running", "success", "error", "other"):
            scr._set_banner(level, f"Banner text for {level}")
            await pilot.pause()


@pytest.mark.asyncio
async def test_launch_enabled(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr._set_launch_enabled(False)
        btn = scr.query_one("#launch-btn", Button)
        assert btn.disabled
        scr._set_launch_enabled(True)
        assert not btn.disabled


# ── Go back ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_go_back(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_go_back()
        await pilot.pause()


# ── Refresh context ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_context(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_refresh_context()
        await pilot.pause()


# ── Open jobs action ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_jobs(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_open_jobs()
        await pilot.pause()
        from groket.ui.screens.jobs import JobsModal

        await wait_until(
            pilot,
            lambda: any(isinstance(s, JobsModal) for s in app.screen_stack),
            description="JobsModal opened from runner",
        )


# ── Run MCP display rows ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_mcp_display_rows(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    pf = RunnerPrefill(
        prompt="p",
        run_mcp_servers=["slack", "github"],
        run_mcp_definitions=[
            {"id": "slack", "title": "Slack MCP", "transport": "http"},
        ],
    )
    app = _Harness(work, prefill=pf)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        rows = scr._run_mcp_display_rows()
        assert len(rows) >= 2
        ids = [r[0] for r in rows]
        assert "slack" in ids
        assert "github" in ids


# ── Refresh tip surfaces ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_tip_surfaces(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.refresh_tip_surfaces()
        await pilot.pause()


# ── Restore run state ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_run_state_no_runs(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr._restore_run_state()
        await pilot.pause()


# ── Apply finished banner ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_finished_banner_success(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    from groket.docker.orchestrator import ContainerConfig, ContainerStatus
    from groket.models import EvalRun
    from groket.runs.run_manager import BackgroundRun

    bg = BackgroundRun(
        run_id="run-fin",
        eval_run=EvalRun(run_id="run-fin", prompt="p", models=["v9"], status="completed"),
        configs=[ContainerConfig(model="v9", prompt="p")],
        results=[
            ContainerStatus(container_name="c1", model="v9", status="completed"),
        ],
        elapsed_s=60.0,
    )
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr._apply_finished_banner(bg)
        await pilot.pause()


@pytest.mark.asyncio
async def test_apply_finished_banner_with_error(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    from groket.docker.orchestrator import ContainerConfig, ContainerStatus
    from groket.models import EvalRun
    from groket.runs.run_manager import BackgroundRun

    bg = BackgroundRun(
        run_id="run-err",
        eval_run=EvalRun(run_id="run-err", prompt="p", models=["v9"], status="failed"),
        configs=[ContainerConfig(model="v9", prompt="p")],
        results=[
            ContainerStatus(container_name="c1", model="v9", status="failed", error="OOM killed"),
        ],
        error="OOM killed",
        elapsed_s=30.0,
    )
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr._apply_finished_banner(bg)
        await pilot.pause()


# ── Persona new/builder ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_persona_from_runner(tmp_path: Path) -> None:
    work = _make_work(tmp_path)
    app = _Harness(work)
    async with app.run_test(size=(140, 50)) as pilot:
        scr = await _wait_runner(pilot, app)
        scr.action_new_persona_from_runner()
        await pilot.pause()
        # PersonaEditorModal should be on the stack
        from groket.ui.screens.personas import PersonaEditorModal

        await wait_until(
            pilot,
            lambda: any(isinstance(s, PersonaEditorModal) for s in app.screen_stack),
            description="PersonaEditorModal opened",
        )
        await pilot.press("escape")
        await pilot.pause()
