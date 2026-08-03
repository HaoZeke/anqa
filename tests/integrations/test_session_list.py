"""``session/list`` catalog filtering and control dispatch."""

from __future__ import annotations

import asyncio
import json
from importlib import import_module
from pathlib import Path

import pytest


def _catalog() -> list[dict]:
    return [
        {
            "sessionId": "alpha-1",
            "path": "/tmp/alpha-1",
            "title": "Socket review",
            "label": "Socket review",
            "model": "grok-4",
            "status": "complete",
            "outcome": "success",
            "origin": "work",
        },
        {
            "sessionId": "beta-host",
            "path": "/tmp/beta-host",
            "title": "Host debug",
            "label": "Host debug",
            "model": "grok-3",
            "status": "running",
            "outcome": "running",
            "origin": "host",
        },
    ]


def test_filter_session_catalog_query_and_limit() -> None:
    control = import_module("groket.integrations.control")
    full = control.filter_session_catalog(_catalog())
    assert full["total"] == 2
    assert full["matched"] == 2
    assert len(full["sessions"]) == 2

    host_only = control.filter_session_catalog(_catalog(), query="host")
    assert host_only["total"] == 2
    assert host_only["matched"] == 1
    assert host_only["sessions"][0]["sessionId"] == "beta-host"

    limited = control.filter_session_catalog(_catalog(), limit=1)
    assert limited["matched"] == 2
    assert len(limited["sessions"]) == 1


async def _request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict | None = None,
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()
    while True:
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        if response.get("id") == request_id:
            return response


def _short_sock(name: str) -> Path:
    """Bind under /tmp — pytest tmp paths often exceed AF_UNIX limits on macOS."""
    path = Path("/tmp") / f"groket-test-{name}.sock"
    path.unlink(missing_ok=True)
    return path


@pytest.mark.asyncio
async def test_control_server_session_list() -> None:
    control = import_module("groket.integrations.control")
    sock = _short_sock("session-list")
    server = control.ControlServer(
        socket_path=sock,
        list_sessions=_catalog,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        init = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": 1, "clientInfo": {"name": "test"}},
        )
        assert "session/list" in init["result"]["capabilities"]

        listed = await _request(reader, writer, 2, "session/list", {"query": "review"})
        assert listed["result"]["matched"] == 1
        assert listed["result"]["sessions"][0]["sessionId"] == "alpha-1"
        assert listed["result"]["sessions"][0]["path"] == "/tmp/alpha-1"

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_session_list_empty_without_lister() -> None:
    control = import_module("groket.integrations.control")
    server = control.ControlServer(socket_path=_short_sock("session-list-empty"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        listed = await _request(reader, writer, 1, "session/list", {})
        assert listed["result"] == {"sessions": [], "total": 0, "matched": 0}
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
