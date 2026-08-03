"""Control socket singleton: second owner soft-fails instead of crashing."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest
from groket.ui.app import TraceEvalApp


def _short_sock(name: str) -> Path:
    path = Path("/tmp") / f"groket-test-{name}.sock"
    path.unlink(missing_ok=True)
    return path


@pytest.mark.asyncio
async def test_second_control_server_raises_socket_in_use() -> None:
    control = import_module("groket.integrations.control")
    sock = _short_sock("singleton")
    first = control.ControlServer(socket_path=sock)
    second = control.ControlServer(socket_path=sock)
    await first.start()
    try:
        with pytest.raises(control.ControlSocketInUse) as exc_info:
            await second.start()
        assert exc_info.value.socket_path == sock
    finally:
        await first.close()


@pytest.mark.asyncio
async def test_tui_continues_when_control_socket_already_owned(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    sock = _short_sock("tui-singleton")
    owner = control.ControlServer(socket_path=sock)
    await owner.start()
    try:
        work = tmp_path / "work"
        traces = work / "runs" / "traces"
        traces.mkdir(parents=True)
        app = TraceEvalApp(
            work_dir=work,
            traces_path=traces,
            control_socket=sock,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            for _ in range(200):
                await pilot.pause()
                server = app._control_server
                # Soft-fail clears the app handle, or leaves an unstarted server.
                if server is None or server._server is None:
                    break
            assert app.is_running
            server = app._control_server
            assert server is None or server._server is None
            # Original owner still holds the socket.
            assert sock.exists()
    finally:
        await owner.close()
