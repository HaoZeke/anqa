"""Unknown-method errors mean a stale anqa serve, not a dead socket."""

from __future__ import annotations

from anqa.control.server import ControlError, is_unknown_method


def test_unknown_method_from_code() -> None:
    assert is_unknown_method(ControlError(-32601, "method not found", {"method": "session/diff"}))


def test_unknown_method_from_message() -> None:
    assert is_unknown_method("method not found")
    assert is_unknown_method("JSON-RPC -32601 method not found")
    assert not is_unknown_method("connection refused")
    assert not is_unknown_method(ControlError(404, "session not found"))
