"""session/import extracts an archive and notifies catalog clients."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from anqa.control.server import PROTOCOL_VERSION, ControlServer
from anqa.harness.grok import GrokAdapter


def _short_sock(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix="anqa-ctl-")) / name


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
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
        if response.get("id") == request_id:
            return response


@pytest.mark.asyncio
async def test_session_import_opens_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    sd = tmp_path / "src" / "imp-sid"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text('{"generated_title":"Imported"}\n', encoding="utf-8")
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    archive = tmp_path / "imp.tar.gz"
    GrokAdapter().write_archive(sd, archive)

    server = ControlServer(socket_path=_short_sock("import.sock"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        init = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "test"}},
        )
        assert "session/import" in init["result"]["capabilities"]
        imported = await _request(
            reader,
            writer,
            2,
            "session/import",
            {"path": str(archive)},
        )
        result = imported["result"]
        assert result["sessionId"] == "imp-sid"
        assert result["harness"] == "grok"
        assert result["imported"] is True
        assert result["opened"] is True
        selected = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert selected["method"] == "session/selected"
        changed = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        assert changed["method"] == "session/changed"
        assert changed["params"]["listChanged"] is True
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
