"""Model token helpers (``model`` / ``model:effort``)."""

from __future__ import annotations

REASONING_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


def split_model_effort(token: str) -> tuple[str, str]:
    """Split ``model`` or ``model:effort`` into ``(model_id, effort)``."""
    if not (raw := (token or "").strip()):
        return "", ""
    if ":" in raw:
        model, _, effort = raw.rpartition(":")
        if (model_s := model.strip()) and (effort_l := effort.strip().lower()) in REASONING_EFFORTS:
            return model_s, effort_l
    return raw, ""


def join_model_effort(model: str, effort: str = "") -> str:
    """Format a model token, appending ``:effort`` when *effort* is set."""
    mid, eff = (model or "").strip(), (effort or "").strip().lower()
    return f"{mid}:{eff}" if mid and eff in REASONING_EFFORTS else mid
