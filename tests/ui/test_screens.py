"""Mount major screens and modals to verify compose trees."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App


@pytest.mark.asyncio
async def test_rules_screen_mount(tmp_path: Path):
    from groket.ui.screens.rules import RulesScreen

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(RulesScreen(tmp_path))

    async with H().run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen is not None


@pytest.mark.asyncio
async def test_run_configs_screen_mount(tmp_path: Path):
    from groket.runs.run_manager import RunManager
    from groket.ui.screens.run_configs import RunConfigsScreen

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(RunConfigsScreen(tmp_path, RunManager(tmp_path)))

    async with H().run_test(size=(120, 40)) as pilot:
        await pilot.pause()


@pytest.mark.asyncio
async def test_flag_modal_mount():
    from groket.models import TraceEvent
    from groket.ui.widgets.flag_panel import FlagModal

    ev = TraceEvent(index=0, event_type="tool_call", content="x")

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(FlagModal(ev))

    async with H().run_test() as pilot:
        await pilot.pause()
        pilot.app.screen.action_save()
        await pilot.pause()


@pytest.mark.asyncio
async def test_help_modal_mount():
    from groket.ui.widgets.help_modal import HelpModal

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(HelpModal())

    async with H().run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen is not None


@pytest.mark.asyncio
async def test_personas_screen_mount(tmp_path: Path):
    from groket.ui.screens.personas import PersonasScreen

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonasScreen(tmp_path))

    async with H().run_test() as pilot:
        await pilot.pause()


@pytest.mark.asyncio
async def test_detail_view_mount():
    from groket.ui.widgets.detail_view import DetailView

    class H(App[None]):
        def compose(self):
            yield DetailView(id="dv")

    async with H().run_test() as pilot:
        await pilot.pause()
        dv = pilot.app.query_one("#dv", DetailView)
        # common update APIs
        for meth in ("update_content", "show_event", "clear", "set_content"):
            fn = getattr(dv, meth, None)
            if callable(fn):
                try:
                    fn("")
                except TypeError:
                    try:
                        fn()
                    except Exception:
                        pass
                except Exception:
                    pass


@pytest.mark.asyncio
async def test_timeline_table_mount():
    from groket.ui.widgets.timeline import TimelineTable

    class H(App[None]):
        def compose(self):
            yield TimelineTable(id="tl")

    async with H().run_test() as pilot:
        await pilot.pause()
