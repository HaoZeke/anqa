"""Mount major screens and modals; assert user-visible chrome."""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Static

from .pilot_helpers import static_plain


@pytest.mark.asyncio
async def test_help_modal_mount():
    from anqa.ui.widgets.help_modal import HelpModal

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(HelpModal())

    async with H().run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        from anqa.ui.widgets.help_modal import HelpModal as HM

        assert isinstance(pilot.app.screen, HM)
        body = "\n".join(static_plain(w) for w in pilot.app.screen.query(Static))
        # Help content is non-empty prose (bindings / overview).
        assert len(body.strip()) > 20


@pytest.mark.asyncio
async def test_detail_view_mount():
    from anqa.ui.widgets.detail_view import DetailView

    class H(App[None]):
        def compose(self):
            yield DetailView(id="dv")

    async with H().run_test() as pilot:
        await pilot.pause()
        dv = pilot.app.query_one("#dv", DetailView)
        assert dv.visible_plain().strip() == ""
