"""Drop leftover terminal probe replies so they never become TUI keys."""

from __future__ import annotations

import fcntl
import os
import re
import sys

# CSI Device Attributes (``ESC [ ? 62 ; 52 ; c``) after ESC is stripped.
_DEVICE_ATTRIBUTES = re.compile(r"\?\d[\d;]*c")
# Kitty graphics APC ack (``ESC _ Gi=<id>;OK ESC \``), with or without C0 marks.
_KITTY_GRAPHICS_ACK = re.compile(r"Gi=\d+;OK")


def is_terminal_probe_text(text: str) -> bool:
    """True when *text* is a Device Attributes or Kitty graphics reply.

    :param text: Candidate Input value or key burst.
    :returns: Whether the string is a terminal reply, not a catalog query.
    """
    raw = (text or "").replace("\x1b", "").replace("^_", "").replace("^\\", "")
    if not raw:
        return False
    return bool(_DEVICE_ATTRIBUTES.search(raw) or _KITTY_GRAPHICS_ACK.search(raw))


def drain_pending_stdin() -> None:
    """Discard bytes already queued on stdin before the TUI starts.

    Terminals reply to Device Attributes and graphics probes. Those bytes
    sit on stdin and Textual types them into the first focusable widget.
    """
    stdin = sys.stdin
    if not stdin.isatty():
        return
    fd = stdin.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
