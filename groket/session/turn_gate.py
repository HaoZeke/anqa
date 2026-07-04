"""Locate interactive multi-turn control files for a session on disk."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import JsonObject, json_as_int, json_as_object, json_as_str

logger = logging.getLogger(__name__)

# Host-side queue when the agent is mid-turn or a follow-up is already staged.
_PENDING_QUEUE = "pending-prompts.jsonl"


def traces_volume_for_session(session_dir: Path) -> Path | None:
    """Container traces volume for *session_dir* (bind mount root on the host).

    Typical layout: ``…/traces/<container_name>/<cwd-token>/<session_id>``.
    """
    p = Path(session_dir).expanduser().resolve()
    for base in (p.parent.parent, p.parent, p.parent.parent.parent):
        try:
            if base.is_dir() and any(base.glob(".groket-turn*")):
                return base
        except OSError:
            continue
    try:
        gp = p.parent.parent
        return gp if gp.is_dir() else None
    except OSError:
        return None


def turn_gate_dirs_for_session(session_dir: Path) -> list[Path]:
    """Candidate ``.groket-turn*`` dirs for this session's container volume."""
    base = traces_volume_for_session(session_dir)
    if base is None:
        return []
    dirs = [d for d in base.glob(".groket-turn*") if d.is_dir()]
    dirs.sort(key=lambda p: (0 if "-turn-" in p.name else 1, -len(p.name), p.name))
    return dirs


def _gate_command(gate: Path) -> str:
    cp = gate / "command"
    if not cp.is_file():
        return ""
    try:
        return cp.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def _read_status_file(gate: Path) -> JsonObject:
    sp = gate / "status.json"
    if not sp.is_file():
        return {}
    try:
        return json_as_object(json.loads(sp.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _write_status(gate: Path, *, state: str, session_id: str = "", turn: int = 0) -> None:
    gate.mkdir(parents=True, exist_ok=True)
    payload: JsonObject = {"state": state}
    if session_id:
        payload["session_id"] = session_id
    if turn:
        payload["turn"] = turn
    try:
        (gate / "status.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def read_turn_gate_status(session_dir: Path) -> JsonObject:
    """Return turn-gate status for *session_dir*, preferring matching session_id.

    Host ``command=done`` is *not* forced into ``state`` here: the entrypoint
    rewrites status when it finishes. Until then the UI treats the session as
    still live (running / finishing) even though no more follow-ups are accepted.
    """
    sid = Path(session_dir).name
    best: JsonObject = {}
    for gate in turn_gate_dirs_for_session(session_dir):
        data = _read_status_file(gate)
        if not data.get("state"):
            continue
        gate_sid = json_as_str(data.get("session_id"))
        if gate_sid == sid:
            return data
        if not best:
            best = data
    return best


def host_requested_done(session_dir: Path) -> bool:
    """True when the host wrote ``command=done`` (session finishing, not idle yet)."""
    for gate in turn_gate_dirs_for_session(session_dir):
        if _gate_command(gate) == "done":
            return True
    return False


def session_awaits_follow_up(session_dir: Path) -> bool:
    """True only when the gate is waiting and the host has not sent done."""
    for gate in turn_gate_dirs_for_session(session_dir):
        if _gate_command(gate) == "done":
            return False
    st = read_turn_gate_status(session_dir)
    if json_as_str(st.get("state")) == "done":
        return False
    return json_as_str(st.get("state")) == "awaiting_follow_up"


def _ensure_gate_dirs(session_dir: Path) -> list[Path]:
    dirs = turn_gate_dirs_for_session(session_dir)
    if dirs:
        return dirs
    base = traces_volume_for_session(session_dir)
    if base is None:
        raise RuntimeError("could not locate traces volume for session")
    return [base / ".groket-turn"]


def _primary_gate(session_dir: Path) -> Path:
    return _ensure_gate_dirs(session_dir)[0]


def _queue_path(gate: Path) -> Path:
    return gate / _PENDING_QUEUE


def list_queued_follow_up_items(session_dir: Path) -> list[tuple[str, bool]]:
    """Queued follow-ups as ``(prompt, final_turn)``."""
    try:
        gate = _primary_gate(session_dir)
    except RuntimeError:
        return []
    qp = _queue_path(gate)
    if not qp.is_file():
        return []
    out: list[tuple[str, bool]] = []
    try:
        for line in qp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("prompt") is not None:
                    text = str(obj["prompt"]).strip()
                    if text:
                        out.append((text, bool(obj.get("final"))))
                    continue
            except json.JSONDecodeError:
                pass
            out.append((line, False))
    except OSError:
        return []
    return out


def list_queued_follow_ups(session_dir: Path) -> list[str]:
    """Prompt texts queued on the host (display / length)."""
    return [p for p, _final in list_queued_follow_up_items(session_dir)]


def _write_queue(gate: Path, items: list[tuple[str, bool]]) -> None:
    qp = _queue_path(gate)
    if not items:
        try:
            qp.unlink(missing_ok=True)
        except OSError:
            pass
        return
    gate.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps({"prompt": p, "final": bool(fin)}, ensure_ascii=False) + "\n" for p, fin in items
    )
    qp.write_text(body, encoding="utf-8")


def enqueue_follow_up(session_dir: Path, prompt: str, *, final: bool = False) -> int:
    """Append a follow-up to the host queue; returns new queue length."""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("follow-up prompt is empty")
    gate = _primary_gate(session_dir)
    gate.mkdir(parents=True, exist_ok=True)
    q = list_queued_follow_up_items(session_dir)
    q.append((text, bool(final)))
    _write_queue(gate, q)
    return len(q)


def _stage_follow_up_on_gate(
    gate: Path, text: str, *, session_id: str, final: bool = False
) -> None:
    gate.mkdir(parents=True, exist_ok=True)
    (gate / "next-prompt.txt").write_text(text, encoding="utf-8")
    (gate / "command").write_text("follow_up\n", encoding="utf-8")
    final_path = gate / "final_turn"
    if final:
        final_path.write_text("1\n", encoding="utf-8")
    else:
        try:
            final_path.unlink(missing_ok=True)
        except OSError:
            pass
    prev = _read_status_file(gate)
    turn = json_as_int(prev.get("turn"), 0)
    _write_status(
        gate,
        state="running",
        session_id=json_as_str(prev.get("session_id")) or session_id,
        turn=turn,
    )


def _follow_up_already_staged(gate: Path) -> bool:
    if _gate_command(gate) != "follow_up":
        return False
    np = gate / "next-prompt.txt"
    try:
        return np.is_file() and bool(np.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def write_follow_up_for_session(session_dir: Path, prompt: str, *, final: bool = False) -> str:
    """Stage a follow-up for the entrypoint, or queue it if the gate is busy.

    When *final* is true, the entrypoint runs this turn then exits without
    awaiting another follow-up (``final_turn`` on the gate).
    """
    text = (prompt or "").strip()
    if not text:
        raise ValueError("follow-up prompt is empty")
    dirs = _ensure_gate_dirs(session_dir)
    gate = dirs[0]
    sid = Path(session_dir).name

    if _gate_command(gate) == "done":
        raise RuntimeError("session already marked done")

    awaits = session_awaits_follow_up(session_dir)
    staged = _follow_up_already_staged(gate)
    if awaits and not staged:
        for g in dirs:
            _stage_follow_up_on_gate(g, text, session_id=sid, final=final)
        return "sent"

    enqueue_follow_up(session_dir, text, final=final)
    return "queued"


def drain_queued_follow_up(session_dir: Path) -> str | None:
    """If gate is awaiting and queue non-empty, stage the next prompt. Returns it."""
    if not session_awaits_follow_up(session_dir):
        return None
    try:
        gate = _primary_gate(session_dir)
    except RuntimeError:
        return None
    if _follow_up_already_staged(gate):
        return None
    items = list_queued_follow_up_items(session_dir)
    if not items:
        return None
    (text, final), rest = items[0], items[1:]
    _write_queue(gate, rest)
    sid = Path(session_dir).name
    for g in _ensure_gate_dirs(session_dir):
        _stage_follow_up_on_gate(g, text, session_id=sid, final=final)
    return text


def write_done_for_session(session_dir: Path) -> None:
    """Ask the entrypoint to stop (``command=done``; status stays live until it exits)."""
    dirs = _ensure_gate_dirs(session_dir)
    for gate in dirs:
        gate.mkdir(parents=True, exist_ok=True)
        (gate / "command").write_text("done\n", encoding="utf-8")
        try:
            (gate / "final_turn").unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _write_queue(gate, [])
        except OSError:
            logger.debug("clear pending queue on done failed", exc_info=True)


def session_pending_label(session_dir: Path, *, turn_in_progress: bool = False) -> str:
    """Short status for UI: interactive wait, agent running, or empty if settled."""
    st = read_turn_gate_status(session_dir)
    state = json_as_str(st.get("state"))
    if state == "done":
        return ""
    if host_requested_done(session_dir):
        # Only "finishing" while traces may still be written; stale + done → settled.
        try:
            from ..parser import _infer_incomplete_turn_outcome

            if _infer_incomplete_turn_outcome(session_dir) == "running":
                return "finishing (done requested)"
        except Exception:
            return "finishing (done requested)"
        return ""
    turn = json_as_int(st.get("turn"), 0)
    queued = 0
    try:
        queued = len(list_queued_follow_ups(session_dir))
    except Exception:
        queued = 0
    qbit = f", {queued} queued" if queued else ""
    if state == "awaiting_follow_up":
        base = f"awaiting follow-up (turn {turn})" if turn else "awaiting follow-up"
        return base + qbit
    if state == "running":
        base = f"agent running (turn {turn})" if turn else "agent running"
        return base + qbit
    if turn_in_progress:
        return "turn in progress" + qbit
    if state and state not in ("unknown", ""):
        return state + qbit
    if queued:
        return f"{queued} follow-up(s) queued"
    return ""
