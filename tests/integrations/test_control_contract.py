"""Control contract inventory is the source for initialize, dispatch, docs, schema."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from groket.integrations.control import (
    PROTOCOL_VERSION,
    ControlServer,
    dispatched_method_names,
)
from groket.integrations.control_contract import (
    capability_names,
    emit_control_doc,
    emit_control_schema,
    method_names,
    notification_names,
    render_control_doc,
)

ROOT = Path(__file__).resolve().parents[2]
CONTROL_DOC = ROOT / "docs" / "control.md"
CONTROL_SCHEMA = ROOT / "schemas" / "control.schema.json"


def _short_sock(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix="groket-ctl-")) / name


async def _initialize(server: ControlServer) -> dict[str, object]:
    reader, writer = await asyncio.open_unix_connection(server.socket_path)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "contract"},
        },
    }
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()
    response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    writer.close()
    await writer.wait_closed()
    assert isinstance(response, dict)
    return response


# Shipped methods and notifications. Deleting one from the inventory fails here.
REQUIRED_METHODS = (
    "initialize",
    "session/list",
    "session/get",
    "session/overview",
    "session/timeline",
    "session/turns",
    "session/usage",
    "session/findings",
    "session/diff",
    "session/open",
    "session/render",
    "session/follow_up",
    "session/done",
    "notes/list",
    "notes/upsert",
    "notes/delete",
    "analysis/run",
    "analysis/status",
)
REQUIRED_NOTIFICATIONS = (
    "session/selected",
    "session/changed",
    "notes/changed",
    "analysis/changed",
)


def test_inventory_covers_shipped_methods_and_notifications() -> None:
    names = method_names()
    notes = notification_names()
    missing_methods = [name for name in REQUIRED_METHODS if name not in names]
    missing_notes = [name for name in REQUIRED_NOTIFICATIONS if name not in notes]
    assert missing_methods == []
    assert missing_notes == []
    extra_methods = [name for name in names if name not in REQUIRED_METHODS]
    extra_notes = [name for name in notes if name not in REQUIRED_NOTIFICATIONS]
    assert extra_methods == []
    assert extra_notes == []


def test_dispatch_keys_match_contract_methods() -> None:
    assert dispatched_method_names() == frozenset(method_names())


def test_capabilities_are_contract_methods_except_initialize() -> None:
    assert capability_names() == tuple(name for name in method_names() if name != "initialize")
    assert "initialize" not in capability_names()


def test_emit_doc_contains_version_methods_framing() -> None:
    text = render_control_doc()
    assert PROTOCOL_VERSION in text
    assert f'protocolVersion: "{PROTOCOL_VERSION}"' in text
    for name in REQUIRED_METHODS:
        assert f"`{name}`" in text, name
    for name in REQUIRED_NOTIFICATIONS:
        assert f"`{name}`" in text, name
    assert "groket serve" in text
    assert "GROKET_CONTROL_SOCKET" in text
    assert "Content-Length" in text
    assert "control_contract.py" in text


def test_committed_doc_and_schema_match_emit() -> None:
    assert emit_control_doc() == CONTROL_DOC.read_text(encoding="utf-8")
    assert emit_control_schema() == CONTROL_SCHEMA.read_text(encoding="utf-8")


def test_emit_writes_paths(tmp_path: Path) -> None:
    doc = tmp_path / "control.md"
    schema = tmp_path / "control.schema.json"
    emit_control_doc(doc)
    emit_control_schema(schema)
    assert doc.read_text(encoding="utf-8") == emit_control_doc()
    assert schema.read_text(encoding="utf-8") == emit_control_schema()
    body = schema.read_text(encoding="utf-8")
    assert PROTOCOL_VERSION in body
    assert "session/timeline" in body
    assert "session/selected" in body


def test_justfile_and_pages_list_control_schema() -> None:
    just = (ROOT / "justfile").read_text(encoding="utf-8")
    pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "emit_control_schema" in just
    assert "emit_control_doc" in just
    assert "schemas/control.schema.json" in just
    assert "docs/control.md" in just
    assert "schemas/control.schema.json" in pages
    assert "control_contract.py" in pages


@pytest.mark.asyncio
async def test_initialize_advertises_contract_capabilities() -> None:
    """Real owner initialize result matches the in-code inventory."""
    server = ControlServer(socket_path=_short_sock("contract-init.sock"))
    await server.start()
    try:
        initialized = await _initialize(server)
        result = initialized["result"]
        assert isinstance(result, dict)
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["capabilities"] == list(capability_names())
        assert set(result["capabilities"]) == set(method_names()) - {"initialize"}
    finally:
        await server.close()
