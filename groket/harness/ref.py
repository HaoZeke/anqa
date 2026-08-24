"""Session locator that is not always a directory.

Grok sessions are directories. OpenCode (and later Copilot) sessions live in
SQLite. Catalog ``path`` for a non-directory store is ``harness:session_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..paths import APP_HOME

# Known ids from docs/harness-disk-view.md. Parse does not import the registry.
HARNESS_IDS: frozenset[str] = frozenset(
    {
        "grok",
        "claude",
        "codex",
        "gemini",
        "opencode",
        "cursor",
        "aider",
        "pi",
        "copilot",
        "kimi",
    }
)

ORIGIN_WORK = "work"
ORIGIN_HOST = "host"


@dataclass(frozen=True)
class SessionRef:
    """One session the catalog or control plane can reopen.

    :ivar harness: Adapter id (``grok``, ``opencode``, …).
    :ivar session_id: Stable product id (directory name or sqlite row id).
    :ivar origin: ``work`` (groket-launched) or ``host`` (native store).
    :ivar locator: Directory (Grok) or database/file the adapter reads.
    :ivar cwd: Workspace path when the store recorded one.
    """

    harness: str
    session_id: str
    origin: str
    locator: Path
    cwd: str = ""

    def ref_string(self) -> str:
        """Control / catalog path: resolved directory, or ``harness:id``."""
        if self.harness == "grok":
            try:
                return str(self.locator.expanduser().resolve())
            except OSError:
                return str(self.locator)
        return f"{self.harness}:{self.session_id}"

    def overlay_dir(self) -> Path:
        """Groket-owned notes directory (never a foreign sqlite tree)."""
        if self.harness == "grok":
            return self.locator
        return APP_HOME / "notes" / self.harness / self.session_id


def parse_session_ref_string(raw: str) -> tuple[str, str] | None:
    """Return ``(harness, session_id)`` for ``harness:id``, else None.

    Rejects filesystem paths (``/``, ``~``, ``\\``) so a Windows drive or
    a POSIX path is never treated as a harness id.

    :param raw: Catalog path or control ``session`` argument.
    :returns: Pair when *raw* is a known ``harness:id``.
    """
    text = (raw or "").strip()
    if not text or text.startswith("~") or "/" in text or "\\" in text:
        return None
    head, sep, tail = text.partition(":")
    if not sep or not tail or head not in HARNESS_IDS:
        return None
    return head, tail


__all__ = [
    "HARNESS_IDS",
    "ORIGIN_HOST",
    "ORIGIN_WORK",
    "SessionRef",
    "parse_session_ref_string",
]
