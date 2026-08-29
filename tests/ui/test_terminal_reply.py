"""Leftover terminal probe replies must not become TUI input."""

from __future__ import annotations

from anqa.ui.terminal_reply import drain_pending_stdin, is_terminal_probe_text


def test_device_attributes_reply_is_a_probe() -> None:
    assert is_terminal_probe_text("?62;52;c")
    assert is_terminal_probe_text("?62;52;c^_Gi=1768635629;OK^\\")


def test_kitty_graphics_ack_is_a_probe() -> None:
    assert is_terminal_probe_text("^_Gi=1768635629;OK^\\")
    assert is_terminal_probe_text("Gi=1768635629;OK")


def test_catalog_query_is_not_a_probe() -> None:
    assert not is_terminal_probe_text("")
    assert not is_terminal_probe_text("harness:grok")
    assert not is_terminal_probe_text("status:running Isolate")
    assert not is_terminal_probe_text("c")


def test_drain_pending_stdin_is_quiet_without_a_tty() -> None:
    drain_pending_stdin()
