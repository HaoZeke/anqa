"""Session walk and ``updates.jsonl`` keep/skip (Python + optional ``anqa._scan``).

:func:`find_sessions`, :func:`looks_like_session_dir`,
:func:`keep_updates_line`, and :func:`filter_updates` always return concrete
values. ``ANQA_SCAN=0`` uses the Python body even when the extension is
installed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import ModuleType

WALK_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "anqa-plugins",
        "anqa-skills",
        "subagents",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
        ".cache",
        ".tox",
        ".anqa-resume-seed",
        ".anqa-workspace-seed",
        "workspace",
    }
)

_OFF = frozenset({"0", "false", "off", "no"})

_TU_BYTES = b"tool_call_update"
_TERM_BYTES = (
    b'"status":"completed"',
    b'"status": "completed"',
    b'"status":"failed"',
    b'"status": "failed"',
    b'"isError":true',
    b'"isError": true',
)

_mod: ModuleType | None = None


def scan_forced_off() -> bool:
    """True when ``ANQA_SCAN`` selects the Python implementation."""
    return os.environ.get("ANQA_SCAN", "1").strip().lower() in _OFF


def scan_module() -> ModuleType | None:
    """Loaded ``anqa._scan``, or ``None`` when missing or forced off."""
    global _mod
    if scan_forced_off():
        return None
    if _mod is None:
        try:
            from anqa import _scan as loaded
        except ImportError:
            return None
        _mod = loaded
    return _mod


def using_scan() -> bool:
    """True when the compiled module is loaded and not forced off."""
    return scan_module() is not None


def skip_dir_name(name: str) -> bool:
    """Return True when *name* is not descended into."""
    return name in WALK_SKIP_DIRS or name.endswith(".stage")


def looks_like_session_dir(path: Path | str) -> bool:
    """Whether *path* has session artifacts.

    :param path: Directory to inspect.
    :returns: ``True`` when ``updates.jsonl`` or ``summary.json`` exists as a
        non-directory entry, or ``events.jsonl`` exists with size greater
        than zero. Missing paths and OS errors return ``False``.
    """
    ext = scan_module()
    if ext is not None:
        return bool(ext.looks_like_session_dir(str(path)))
    directory = Path(path)

    def _nonsdir(name: str) -> bool:
        entry = directory / name
        try:
            return not stat.S_ISDIR(entry.lstat().st_mode)
        except OSError:
            return False

    try:
        if _nonsdir("updates.jsonl") or _nonsdir("summary.json"):
            return True
        if _nonsdir("events.jsonl"):
            return (directory / "events.jsonl").stat().st_size > 0
    except OSError:
        return False
    return False


def find_files_py(root: Path | str, *, suffix: str, name_prefix: str = "") -> list[Path]:
    """Python twin of :func:`find_files`."""
    found: list[Path] = []
    start = Path(root)
    if not start.exists():
        return found
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        dirnames[:] = [d for d in dirnames if not skip_dir_name(d)]
        path = Path(dirpath)
        if "subagents" in path.parts:
            dirnames.clear()
            continue
        for name in filenames:
            if name.endswith(suffix) and name.startswith(name_prefix):
                found.append(path / name)
    return found


def find_files(root: Path | str, *, suffix: str, name_prefix: str = "") -> list[Path]:
    """Discover files under *root* whose name ends with *suffix*.

    Optional *name_prefix* must also match. Skips the same junk directories
    as :func:`find_sessions`. Uses ``anqa._scan`` when loaded.

    :param root: Store tree.
    :param suffix: Filename suffix (``".jsonl"``).
    :param name_prefix: Optional filename prefix (``"session-"``).
    :returns: Matching files (empty when *root* is missing).
    """
    ext = scan_module()
    fn = getattr(ext, "find_files", None) if ext is not None else None
    if callable(fn):
        return [Path(p) for p in fn(str(root), suffix, name_prefix)]
    return find_files_py(root, suffix=suffix, name_prefix=name_prefix)


def find_sessions(root: Path | str) -> list[Path]:
    """Discover session directories under *root*.

    :param root: Traces tree or other directory to walk.
    :returns: Session paths (empty when *root* is missing).
    """
    ext = scan_module()
    if ext is not None:
        return [Path(p) for p in ext.find_sessions(str(root))]
    found: list[Path] = []
    start = Path(root)
    if not start.exists():
        return found
    for dirpath, dirnames, _filenames in os.walk(start, followlinks=False):
        dirnames[:] = [d for d in dirnames if not skip_dir_name(d)]
        path = Path(dirpath)
        if "subagents" in path.parts:
            dirnames.clear()
            continue
        if looks_like_session_dir(path):
            found.append(path)
            dirnames.clear()
    return found


def keep_updates_line_py(line: bytes) -> bool:
    """Return True when *line* should be JSON-parsed (Python body)."""
    if _TU_BYTES in line and not any(m in line for m in _TERM_BYTES):
        return False
    return True


def filter_updates_py(data: bytes) -> list[bytes]:
    """Split *data* on ``\\n`` and keep lines :func:`keep_updates_line_py` accepts."""
    out: list[bytes] = []
    start = 0
    for i, byte in enumerate(data):
        if byte != 0x0A:
            continue
        line = data[start:i]
        if line.endswith(b"\r"):
            line = line[:-1]
        if keep_updates_line_py(line):
            out.append(line)
        start = i + 1
    if start < len(data):
        line = data[start:]
        if line.endswith(b"\r"):
            line = line[:-1]
        if line and keep_updates_line_py(line):
            out.append(line)
    return out


def keep_updates_line(line: bytes) -> bool:
    """Keep/skip one ``updates.jsonl`` row."""
    ext = scan_module()
    if ext is not None:
        return bool(ext.keep_updates_line(line))
    return keep_updates_line_py(line)


def filter_updates(data: bytes) -> list[bytes]:
    """Filter raw ``updates.jsonl`` bytes to kept line bodies."""
    ext = scan_module()
    if ext is not None:
        return [bytes(row) for row in ext.filter_updates(data)]
    return filter_updates_py(data)
