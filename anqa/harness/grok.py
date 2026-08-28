"""Grok Build on-disk session adapter.

Public harness contract for harness id ``grok``. Implementation lives in
:mod:`anqa.parser` and :mod:`anqa.session.sources`; this module wraps
those APIs.
"""

from __future__ import annotations

import os
import tarfile
from collections.abc import Sequence
from pathlib import Path

from ..fs_watch import TRACE_FILE_HINTS
from ..models import SessionMeta, TraceEvent
from ..parser import _looks_like_session_dir, find_sessions, load_session_meta_list
from ..parser import parse_timeline as parse_grok_timeline
from ..session.sources import ORIGIN_HOST, collect_host_session_dirs
from .ref import SessionRef

GROK_HARNESS_ID = "grok"

# Host planes on a session directory; not part of the inspectable trace.
_ARCHIVE_SKIP_DIRS = frozenset({"workspace", "terminal"})


def default_sessions_root() -> Path:
    """Native Grok Build store: ``~/.grok/sessions``."""
    return Path.home() / ".grok" / "sessions"


def _resolved(path: Path) -> Path:
    p = Path(path).expanduser()
    try:
        return p.resolve()
    except OSError:
        return p


def _is_native_store(root: Path) -> bool:
    return _resolved(root) == _resolved(default_sessions_root())


def _ref_for_dir(path: Path) -> SessionRef:
    origin = ORIGIN_HOST
    loc = Path(path)
    try:
        loc = loc.resolve()
    except OSError:
        pass
    return SessionRef(
        harness=GROK_HARNESS_ID,
        session_id=loc.name,
        origin=origin,
        locator=loc,
    )


def discover(roots: Sequence[Path | str]) -> list[SessionRef]:
    """List unique Grok sessions under *roots*.

    Host ``~/.grok/sessions`` uses
    :func:`~anqa.session.sources.collect_host_session_dirs`. Every other
    root uses :func:`anqa.parser.find_sessions`. Duplicate resolved paths
    are dropped (first-seen wins).

    :param roots: Trees to scan.
    :returns: Session refs in first-seen order.
    """
    found: list[SessionRef] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if _is_native_store(root):
            dirs = collect_host_session_dirs(root)
        else:
            dirs = find_sessions(root)
        for sd in dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append(_ref_for_dir(sd))
    return found


def looks_like(ref: Path | str) -> bool:
    """True when *ref* is a Grok session directory.

    Wraps :func:`anqa.parser._looks_like_session_dir`.

    :param ref: Session directory path.
    :returns: True when the directory has Grok session artifacts.
    """
    path = Path(ref).expanduser()
    if not path.is_dir():
        return False
    names: set[str] = set()
    try:
        with os.scandir(path) as it:
            for ent in it:
                if ent.is_file(follow_symlinks=False):
                    names.add(ent.name)
    except OSError:
        return False
    return _looks_like_session_dir(path, names)


def load_meta(ref: Path | str) -> SessionMeta:
    """Load list-grade metadata for a Grok session directory.

    Wraps :func:`anqa.parser.load_session_meta_list`. Origin is the host store.

    :param ref: Session directory path.
    :returns: Populated :class:`~anqa.models.SessionMeta`.
    """
    path = Path(ref).expanduser()
    origin = ORIGIN_HOST
    meta = load_session_meta_list(path, origin=origin)
    meta.harness = GROK_HARNESS_ID
    return meta


def parse_timeline(ref: Path | str) -> list[TraceEvent]:
    """Parse a Grok session directory into a linear timeline.

    Wraps :func:`anqa.parser.parse_timeline`.

    :param ref: Session directory path.
    :returns: Coalesced :class:`~anqa.models.TraceEvent` rows.
    """
    return parse_grok_timeline(Path(ref).expanduser())


def watch_hints() -> tuple[str, ...]:
    """Filenames that should trigger a live reload for Grok sessions.

    Same names as :data:`anqa.fs_watch.TRACE_FILE_HINTS`.

    :returns: Basename hints (``updates.jsonl``, ``events.jsonl``, …).
    """
    return TRACE_FILE_HINTS


class GrokAdapter:
    """Directory-shaped Grok Build store."""

    id: str = GROK_HARNESS_ID
    product: str = "Grok Build"
    supported_version: str = "1.0.5"

    def default_host_roots(self) -> list[Path]:
        return [default_sessions_root()]

    def discover(self, roots: Sequence[Path | str] | None = None) -> list[SessionRef]:
        return discover(self.default_host_roots() if roots is None else roots)

    def looks_like(self, ref: SessionRef | Path | str) -> bool:
        if isinstance(ref, SessionRef):
            return ref.harness == GROK_HARNESS_ID and looks_like(ref.locator)
        return looks_like(ref)

    def bind_locator(self, locator: Path) -> SessionRef | None:
        path = Path(locator).expanduser()
        if not looks_like(path):
            return None
        return _ref_for_dir(path)

    def load_meta(self, ref: SessionRef | Path | str) -> SessionMeta:
        if isinstance(ref, SessionRef):
            return load_meta(ref.locator)
        return load_meta(ref)

    def parse_timeline(self, ref: SessionRef | Path | str) -> list[TraceEvent]:
        if isinstance(ref, SessionRef):
            return parse_timeline(ref.locator)
        return parse_timeline(ref)

    def ref_for_id(self, session_id: str) -> SessionRef | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        for ref in self.discover():
            if ref.session_id == sid:
                return ref
        return None

    def watch_hints(self) -> tuple[str, ...]:
        return watch_hints()

    def write_archive(self, ref: SessionRef | Path | str, dest: Path) -> list[str]:
        path = ref.locator if isinstance(ref, SessionRef) else Path(ref).expanduser()
        return write_directory_archive(path, dest)


def _add_archive_tree(tf: tarfile.TarFile, src: Path, arc_prefix: str) -> list[str]:
    names: list[str] = []
    src = Path(src)
    if not src.exists():
        return names
    if src.is_file():
        arc = arc_prefix.rstrip("/")
        tf.add(src, arcname=arc)
        names.append(arc)
        return names
    for path in sorted(src.rglob("*")):
        if path.is_symlink() or path.is_file():
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
        elif path.is_dir() and not any(path.iterdir()):
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
    return names


def write_directory_archive(session_dir: Path, dest: Path) -> list[str]:
    """Pack *session_dir* into *dest* as ``<session_id>/…`` (gzip tar).

    Omits ``workspace/`` and ``terminal/``.
    """
    sid = Path(session_dir).name.strip() or "session"
    session_dir = Path(session_dir).expanduser()
    try:
        session_dir = session_dir.resolve()
    except OSError:
        pass
    if not session_dir.is_dir():
        raise RuntimeError(f"session directory not found: {session_dir}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    packed = False
    try:
        names: list[str] = []
        with tarfile.open(tmp, "w:gz") as tf:
            for path in sorted(session_dir.iterdir()):
                if path.name in _ARCHIVE_SKIP_DIRS:
                    continue
                names.extend(_add_archive_tree(tf, path, f"{sid}/{path.name}"))
        if not names:
            raise RuntimeError(f"session directory has no files to export: {session_dir}")
        tmp.replace(dest)
        packed = True
    except (OSError, tarfile.TarError) as exc:
        raise RuntimeError(f"failed to pack session archive: {exc}") from exc
    finally:
        if not packed:
            tmp.unlink(missing_ok=True)
    return names


__all__ = [
    "GROK_HARNESS_ID",
    "GrokAdapter",
    "default_sessions_root",
    "discover",
    "load_meta",
    "looks_like",
    "parse_timeline",
    "watch_hints",
    "write_directory_archive",
]
