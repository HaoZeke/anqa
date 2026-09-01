"""Cheap list-meta for one-jsonl stores.

A catalog row reads the first object and a 64 KiB tail. It never loads
the full transcript. Adapters still parse the whole file on open.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import JsonObject, SessionMeta, as_json_object
from ..stamp import Stamp


class JsonlFile:
    """One jsonl transcript. List-meta reads a header and a 64 KiB tail."""

    TAIL_BYTES = 64 * 1024
    HEAD_LIMIT = 16
    TAIL_LIMIT = 16

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def object_line(line: str | bytes) -> JsonObject | None:
        """Parse one jsonl line as an object.

        :param line: One raw line, text or bytes.
        :return: The object, or ``None`` when the line is not a JSON object.
        """
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            return None
        return as_json_object(val) if isinstance(val, dict) else None

    def first_objects(self, *, limit: int | None = None) -> list[JsonObject]:
        """JSON objects from the start of the file.

        :param limit: Maximum objects to return. ``None`` uses :attr:`HEAD_LIMIT`.
        :return: Objects in file order.
        """
        cap = self.HEAD_LIMIT if limit is None else limit
        out: list[JsonObject] = []
        try:
            with self.path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    row = self.object_line(line)
                    if row is None:
                        continue
                    out.append(row)
                    if len(out) >= cap:
                        break
        except OSError:
            return []
        return out

    def first_object(self) -> JsonObject | None:
        """First JSON object, or ``None``.

        :return: The first object in the file.
        """
        rows = self.first_objects(limit=1)
        return rows[0] if rows else None

    def last_objects(self, *, limit: int | None = None) -> list[JsonObject]:
        """JSON objects from the last :attr:`TAIL_BYTES`.

        :param limit: Maximum objects to keep. ``None`` uses :attr:`TAIL_LIMIT`.
        :return: The last objects in file order.
        """
        cap = self.TAIL_LIMIT if limit is None else limit
        try:
            size = self.path.stat().st_size
            with self.path.open("rb") as handle:
                if size > self.TAIL_BYTES:
                    handle.seek(size - self.TAIL_BYTES)
                    handle.readline()
                raw = handle.read()
        except OSError:
            return []
        out: list[JsonObject] = []
        for line in raw.split(b"\n"):
            if not line.strip():
                continue
            row = self.object_line(line)
            if row is not None:
                out.append(row)
        return out[-cap:]

    def window(self, *, head: int | None = None, tail: int | None = None) -> list[JsonObject]:
        """Header and tail objects. Files at or under :attr:`TAIL_BYTES` are read once.

        :param head: Objects from the start. ``None`` uses :attr:`HEAD_LIMIT`.
        :param tail: Objects from the end. ``None`` uses :attr:`TAIL_LIMIT`.
        :return: Header objects followed by tail objects.
        """
        head_n = self.HEAD_LIMIT if head is None else head
        tail_n = self.TAIL_LIMIT if tail is None else tail
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size <= self.TAIL_BYTES:
            return self.last_objects(limit=max(head_n + tail_n, 10_000))
        return self.first_objects(limit=head_n) + self.last_objects(limit=tail_n)

    def list_meta(
        self,
        *,
        session_id: str,
        harness: str,
        title: str = "",
        model_id: str = "",
        created_at: str = "",
        updated_at: str = "",
        turn_outcome: str = "",
        tool_call_count: int = 0,
        harness_version: str = "",
        run_dir: str = "",
        has_subagents: bool = False,
        subagent_count: int = 0,
    ) -> SessionMeta:
        """List-grade meta with file mtime as the updated stamp when missing.

        :param session_id: Store session id.
        :param harness: Adapter id.
        :param title: List title when the adapter already knows it.
        :param model_id: Model id when the adapter already knows it.
        :param created_at: ISO created stamp, or empty.
        :param updated_at: ISO updated stamp, or empty to use file mtime.
        :param turn_outcome: Last-turn outcome token, or empty.
        :param tool_call_count: Tool calls the adapter counted in the window.
        :param harness_version: Adapter version string, or empty.
        :param run_dir: Workspace the session was started in, or empty.
        :param has_subagents: True when the window shows a child run.
        :param subagent_count: Child runs the adapter counted.
        :return: List-grade :class:`~anqa.models.SessionMeta`.
        """
        stamp = Stamp.file(self.path)
        updated = updated_at or Stamp.iso(stamp[0]) or ""
        start = Stamp.epoch(created_at)
        end = Stamp.epoch(updated) or int(stamp[0] or 0)
        duration = float(max(0, end - start)) if start else 0.0
        return SessionMeta(
            session_id=session_id,
            session_dir=self.path,
            model_id=model_id or "unknown",
            title=title,
            created_at=created_at,
            updated_at=updated,
            duration_seconds=duration,
            tool_call_count=tool_call_count,
            run_dir=run_dir,
            turn_outcome=turn_outcome,
            harness=harness,
            harness_version=harness_version,
            has_subagents=has_subagents,
            subagent_count=subagent_count,
        )
