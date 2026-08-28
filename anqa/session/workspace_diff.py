"""Workspace diff extraction from session traces.

Reads every ``rewind_points.jsonl`` record (``prompt_index``, before/after
snapshots). When that file is missing or empty, reconstructs approximate
per-path patches from ``search_replace`` tool calls in ``updates.jsonl``.
"""

from __future__ import annotations

import difflib
import json
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject, JsonValue, TraceEvent, json_as_int

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiffHunk:
    """One path in a rewind snapshot or a stacked ``search_replace`` edit."""

    path: str
    kind: str
    added: int
    removed: int
    unified: str


@dataclass(frozen=True)
class DiffPoint:
    """One ``rewind_points.jsonl`` record, or the approximate-edits bag."""

    key: str
    source: str
    prompt_index: int | None
    created_at: str | None
    files: tuple[DiffHunk, ...]
    prompt_text: str = ""
    assistant_text: str = ""

    @property
    def files_changed(self) -> int:
        return len(self.files)

    @property
    def lines_added(self) -> int:
        return sum(h.added for h in self.files)

    @property
    def lines_removed(self) -> int:
        return sum(h.removed for h in self.files)


@dataclass(frozen=True)
class WorkspaceDiff:
    """Rewind snapshots when Grok wrote them; otherwise ``search_replace`` edits."""

    points: tuple[DiffPoint, ...]

    @property
    def source(self) -> str | None:
        if not self.points:
            return None
        return self.points[-1].source

    def point(self, key: str) -> DiffPoint | None:
        """Return the point with *key*, or ``None``."""
        for item in self.points:
            if item.key == key:
                return item
        return None

    def last(self) -> DiffPoint | None:
        """Latest rewind snapshot, or the approximate-edits point."""
        return self.points[-1] if self.points else None

    def meta(self) -> JsonObject:
        """Counts for the last point (same shape as :func:`load_workspace_diff`)."""
        item = self.last()
        if item is None:
            return _meta_dict(source=None, files_changed=0, lines_added=0, lines_removed=0)
        return _meta_dict(
            source=item.source,
            files_changed=item.files_changed,
            lines_added=item.lines_added,
            lines_removed=item.lines_removed,
        )


def _snap_map(block: Mapping[str, JsonValue] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (block or {}).items():
        if isinstance(v, dict):
            content = v.get("content")
            if content is None:
                continue
            path = str(v.get("path") or k).lstrip("/")
            out[path] = content if isinstance(content, str) else str(content)
        elif isinstance(v, str):
            out[str(k).lstrip("/")] = v
    return out


def _unified_diff(old: str | None, new: str | None, path: str) -> tuple[str, int, int]:
    if old is None:
        a, b = "/dev/null", f"b/{path}"
    elif new is None:
        a, b = f"a/{path}", "/dev/null"
    else:
        a, b = f"a/{path}", f"b/{path}"
    ud = list(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile=a,
            tofile=b,
            lineterm="",
        )
    )
    added = sum(1 for ln in ud if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in ud if ln.startswith("-") and not ln.startswith("---"))
    return ("\n".join(ud) if ud else f"(no textual diff for {path})"), added, removed


def _iter_updates(session_dir: Path) -> Iterator[JsonObject]:
    p = session_dir / "updates.jsonl"
    if not p.exists():
        return
    try:
        with open(p) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _meta_dict(
    *,
    source: str | None,
    files_changed: int,
    lines_added: int,
    lines_removed: int,
) -> JsonObject:
    return {
        "source": source,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }


def _hunks_from_snaps(before: dict[str, str], after: dict[str, str]) -> tuple[DiffHunk, ...]:
    out: list[DiffHunk] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if old is None:
            kind = "added"
        elif new is None:
            kind = "removed"
        else:
            kind = "changed"
        text, add_n, del_n = _unified_diff(old, new, path)
        out.append(DiffHunk(path=path, kind=kind, added=add_n, removed=del_n, unified=text))
    return tuple(out)


def _prompt_index(row: Mapping[str, JsonValue]) -> int | None:
    raw = row.get("prompt_index")
    if raw is None:
        return None
    return json_as_int(raw)


def _created_at(row: Mapping[str, JsonValue]) -> str | None:
    raw = row.get("created_at")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _rewind_points(session_dir: Path) -> tuple[DiffPoint, ...]:
    rp_path = session_dir / "rewind_points.jsonl"
    if not rp_path.exists():
        return ()
    try:
        raw_lines = [ln for ln in rp_path.read_text().splitlines() if ln.strip()]
    except OSError as exc:
        logger.warning("rewind_points read failed for %s: %s", session_dir, exc)
        return ()
    out: list[DiffPoint] = []
    for i, line in enumerate(raw_lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        before = _snap_map(
            row.get("file_snapshots") if isinstance(row.get("file_snapshots"), dict) else None
        )
        after = _snap_map(
            row.get("after_snapshots") if isinstance(row.get("after_snapshots"), dict) else None
        )
        if not before and not after:
            continue
        out.append(
            DiffPoint(
                key=str(i),
                source="rewind_points",
                prompt_index=_prompt_index(row),
                created_at=_created_at(row),
                files=_hunks_from_snaps(before, after),
            )
        )
    return tuple(out)


def _search_replace_raw(upd: Mapping[str, JsonValue]) -> tuple[str, str, str] | None:
    if upd.get("sessionUpdate") != "tool_call" or (upd.get("title") or "") != "search_replace":
        return None
    ri = upd.get("rawInput") or {}
    if not isinstance(ri, dict):
        return None
    path = ri.get("file_path") or ri.get("target_file") or "?"
    old_s = ri.get("old_string") or ""
    new_s = ri.get("new_string") or ""
    if not old_s and not new_s:
        return None
    return str(path), str(old_s), str(new_s)


def _search_replace_point(session_dir: Path) -> DiffPoint | None:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for ev in _iter_updates(session_dir):
        params = ev.get("params")
        if not isinstance(params, dict):
            continue
        upd = params.get("update")
        if not isinstance(upd, dict):
            continue
        parsed = _search_replace_raw(upd)
        if parsed is None:
            continue
        path, old_s, new_s = parsed
        grouped.setdefault(path, []).append((old_s, new_s))
    if not grouped:
        return None
    files: list[DiffHunk] = []
    for path in sorted(grouped):
        parts: list[str] = []
        add_n = 0
        del_n = 0
        for old_s, new_s in grouped[path]:
            text, a, r = _unified_diff(old_s, new_s, path)
            parts.append(text)
            add_n += a
            del_n += r
        files.append(
            DiffHunk(
                path=path,
                kind="edit",
                added=add_n,
                removed=del_n,
                unified="\n".join(parts),
            )
        )
    return DiffPoint(
        key="edits",
        source="search_replace",
        prompt_index=None,
        created_at=None,
        files=tuple(files),
    )


def _point_markdown(point: DiffPoint) -> str:
    if point.source == "search_replace":
        if not point.files:
            return (
                "# Workspace diff\n\n"
                "_No rewind snapshots or `search_replace` edits in this session._\n"
            )
        blocks = [f"### edit `{h.path}`\n\n```diff\n{h.unified}\n```\n" for h in point.files]
        return (
            f"# Workspace diff (approximate)\n\n"
            f"_Per-edit `search_replace` patches — may not equal final files._\n\n"
            f"- **Edits:** {point.files_changed}\n"
            f"- **Lines:** +{point.lines_added} / -{point.lines_removed}\n\n---\n\n"
            + "\n".join(blocks)
        )
    if not point.files:
        return "# Workspace diff\n\n_No content differences in rewind snapshots._\n"
    blocks = [f"### `{h.path}`\n\n```diff\n{h.unified}\n```\n" for h in point.files]
    return (
        f"# Workspace diff\n\n"
        f"_From `rewind_points` snapshots (not live git)._\n\n"
        f"- **Files:** {point.files_changed}\n"
        f"- **Lines:** +{point.lines_added} / -{point.lines_removed}\n\n---\n\n" + "\n".join(blocks)
    )


def _turn_context(
    session_dir: Path, timeline: list[TraceEvent] | None = None
) -> dict[int, tuple[str, str]]:
    """prompt_index → (operator prompt, last assistant body) from the timeline."""
    from .. import event_types as et
    from ..harness.grok_parse import parse_timeline
    from .tagged_blocks import operator_prompt_text, unwrap_for_display
    from .turns import is_operator_user_event, segment_timeline_turns

    out: dict[int, tuple[str, str]] = {}
    try:
        events = timeline if timeline is not None else parse_timeline(session_dir)
        segs = segment_timeline_turns(events)
    except (OSError, ValueError, TypeError):
        return out
    for seg in segs:
        if seg.prompt_index is None:
            continue
        prompt = ""
        assistant = ""
        for ev in seg.events:
            if is_operator_user_event(ev) and not prompt:
                prompt = operator_prompt_text(ev.content or "")
            elif ev.event_type in et.AGENT_TYPES:
                text = unwrap_for_display(ev.content or "").strip()
                if text:
                    assistant = text
        out[int(seg.prompt_index)] = (prompt, assistant)
    return out


def _with_turn_context(
    session_dir: Path,
    points: tuple[DiffPoint, ...],
    timeline: list[TraceEvent] | None = None,
) -> tuple[DiffPoint, ...]:
    ctx = _turn_context(session_dir, timeline)
    if not ctx:
        return points
    filled: list[DiffPoint] = []
    for point in points:
        if point.prompt_index is None:
            filled.append(point)
            continue
        prompt, assistant = ctx.get(int(point.prompt_index), ("", ""))
        filled.append(
            DiffPoint(
                key=point.key,
                source=point.source,
                prompt_index=point.prompt_index,
                created_at=point.created_at,
                files=point.files,
                prompt_text=prompt,
                assistant_text=assistant,
            )
        )
    return tuple(filled)


def load_workspace_diff_doc(
    session_dir: Path, timeline: list[TraceEvent] | None = None
) -> WorkspaceDiff:
    """Load every rewind snapshot, or one approximate-edits point.

    :param session_dir: Session directory with ``rewind_points.jsonl`` / ``updates.jsonl``.
    :param timeline: Already-parsed events. When omitted, the session is parsed again.
    :returns: Structured diff. Empty ``points`` when Grok wrote neither source.
    """
    rewind = _rewind_points(session_dir)
    if rewind:
        return WorkspaceDiff(_with_turn_context(session_dir, rewind, timeline))
    edits = _search_replace_point(session_dir)
    if edits is not None:
        return WorkspaceDiff((edits,))
    return WorkspaceDiff(())


def load_workspace_diff(session_dir: Path) -> tuple[str, JsonObject]:
    """Return ``(markdown_body, meta)`` for the last snapshot or approximate edits.

    Meta keys: ``source`` (``rewind_points`` | ``search_replace`` | ``None``),
    ``files_changed``, ``lines_added``, ``lines_removed``.
    """
    doc = load_workspace_diff_doc(session_dir)
    last = doc.last()
    if last is None:
        return (
            "# Workspace diff\n\n_No rewind snapshots or `search_replace` edits in this session._\n",
            doc.meta(),
        )
    return _point_markdown(last), doc.meta()


def format_diff_meta_line(meta: JsonObject) -> str:
    """One-line status for titles / stats."""
    src = meta.get("source")
    if src == "rewind_points":
        return (
            f"rewind: {json_as_int(meta.get('files_changed'), 0)} files "
            f"+{json_as_int(meta.get('lines_added'), 0)}/-{json_as_int(meta.get('lines_removed'), 0)}"
        )
    if src == "search_replace":
        return (
            f"~{json_as_int(meta.get('files_changed'), 0)} search_replace edits "
            f"+{json_as_int(meta.get('lines_added'), 0)}/-{json_as_int(meta.get('lines_removed'), 0)}"
        )
    return "no diff data"
