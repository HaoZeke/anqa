"""Control methods for saved search filters."""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib import import_module
from pathlib import Path

import pytest
from anqa.filters import load_filters


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="anqa-ctl-"))
    return root / name


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
    return json.loads(await asyncio.wait_for(reader.readline(), timeout=2))


async def _handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    from anqa.control.server import PROTOCOL_VERSION

    init = await _request(
        reader,
        writer,
        1,
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "test"}},
    )
    assert "result" in init


@pytest.mark.asyncio
async def test_filters_round_trip() -> None:
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("filters.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        await _handshake(reader, writer)
        saved = await _request(
            reader,
            writer,
            2,
            "filters/upsert",
            {
                "name": "Awaiting notes",
                "scope": "catalog",
                "query": "has:note AND is:awaiting",
            },
        )
        assert saved["result"]["filter"]["name"] == "Awaiting notes"
        listed = await _request(reader, writer, 3, "filters/list", {"scope": "catalog"})
        names = [row["name"] for row in listed["result"]["filters"]]
        assert names == ["Awaiting notes"]
        expanded = await _request(
            reader,
            writer,
            4,
            "filters/expand",
            {"query": "harness:{grok,claude}", "answers": {"harness": "grok"}},
        )
        assert expanded["result"]["query"] == "harness:grok"
        removed = await _request(
            reader,
            writer,
            5,
            "filters/remove",
            {"name": "Awaiting notes", "scope": "catalog"},
        )
        assert removed["result"]["removed"] is True
        assert load_filters() == []
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_filters_upsert_rejects_bad_scope() -> None:
    control = import_module("anqa.control.server")
    server = control.ControlServer(socket_path=_short_sock("filters-bad.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        await _handshake(reader, writer)
        reply = await _request(
            reader,
            writer,
            2,
            "filters/upsert",
            {"name": "x", "scope": "diff", "query": "has:note"},
        )
        assert "error" in reply
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
