"""Delete session directories on disk."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..models import JsonObject, as_json_object

logger = logging.getLogger(__name__)


def rmtree_robust(path: Path) -> None:
    """Remove *path* with ``shutil.rmtree``.

    :raises PermissionError: when the tree is not writable (e.g. leftover
        root-owned session trees).
    """
    path = Path(path).expanduser()
    if not path.exists():
        return
    shutil.rmtree(path)


def session_dirs_for_delete(session_dirs: list[Path]) -> list[Path]:
    """Normalize and de-dupe paths before delete."""
    seen: set[str] = set()
    out: list[Path] = []
    for sd in session_dirs:
        try:
            key = str(Path(sd).expanduser().resolve())
        except OSError:
            key = str(sd)
        if key in seen:
            continue
        seen.add(key)
        out.append(Path(key))
    return out


def prune_empty_parents_after_session_delete(
    session_dir: Path,
    *,
    stop_at: Path | None = None,
) -> list[Path]:
    """Remove empty parent directories up to *stop_at* (not including it)."""
    removed: list[Path] = []
    try:
        cur = Path(session_dir).expanduser()
        cur = cur.parent if cur.exists() else cur.parent
        try:
            cur = cur.resolve()
        except OSError:
            pass
        stop: Path | None = None
        if stop_at is not None:
            try:
                stop = Path(stop_at).expanduser().resolve()
            except OSError:
                stop = Path(stop_at).expanduser()
    except OSError:
        return removed

    for _ in range(32):
        if not cur.is_dir():
            break
        if stop is not None and cur == stop:
            break
        if stop is not None:
            try:
                cur.relative_to(stop)
            except ValueError:
                break
        try:
            children = list(cur.iterdir())
        except OSError:
            break
        if children:
            break
        parent = cur.parent
        try:
            cur.rmdir()
            removed.append(cur)
        except OSError:
            break
        cur = parent
    return removed


def delete_session_dirs(
    session_dirs: list[Path],
    *,
    traces_root: Path | None = None,
    prune_empty_parents: bool = True,
) -> JsonObject:
    """Delete session directories and optional empty parents.

    :returns: Counts and error strings.
    """
    deleted = 0
    errors: list[str] = []
    parents_pruned: list[str] = []

    stop_at: Path | None = None
    if traces_root is not None:
        try:
            stop_at = Path(traces_root).expanduser().resolve()
        except OSError:
            stop_at = Path(traces_root)

    for sd in session_dirs:
        p = Path(sd)
        try:
            if not p.exists():
                errors.append(f"missing: {p}")
                continue
            if not p.is_dir():
                errors.append(f"not a dir: {p}")
                continue
            parent_before = p.parent
            rmtree_robust(p)
            deleted += 1
            if prune_empty_parents:
                for gone in prune_empty_parents_after_session_delete(
                    parent_before, stop_at=stop_at
                ):
                    parents_pruned.append(str(gone))
        except OSError as exc:
            errors.append(f"{p}: {exc}")

    return as_json_object(
        {
            "deleted": deleted,
            "parents_pruned": parents_pruned,
            "parents_pruned_count": len(parents_pruned),
            "errors": errors,
            "requested": len(session_dirs),
        }
    )
