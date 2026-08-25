"""Shared Textual Select helpers for notes and export modals."""

from __future__ import annotations

from textual.widgets import SelectionList


def select_null() -> object:
    """Empty selection sentinel for Textual ``Select`` (version-safe).

    Textual 8+ uses :attr:`Select.NULL` (a ``NoSelection`` instance). Older
    code and some docs still refer to ``Select.BLANK``, which is the bool
    ``False`` and is **not** a legal option value — constructing
    ``Select(..., value=Select.BLANK)`` raises ``InvalidSelectValueError``.
    """
    from textual.widgets import Select

    null = getattr(Select, "NULL", None)
    if null is not None:
        return null
    return getattr(Select, "BLANK", None)


def select_is_blank(value: object) -> bool:
    """True when a Select has no selection (NULL / legacy BLANK / None / bool)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if type(value).__name__ in ("NoSelection", "_NoSelection"):
        return True
    from textual.widgets import Select

    null = getattr(Select, "NULL", None)
    if null is not None and value is null:
        return True
    blank = getattr(Select, "BLANK", None)
    if blank is not None and value is blank:
        return True
    return False


def select_value_str(widget_value: str | None | object, *, default: str = "") -> str:
    """Coerce Select.value (may be sentinel) to str."""
    if select_is_blank(widget_value):
        return default
    s = str(widget_value).strip()
    if s in ("", "None", "False", "True"):
        return default
    return s


def selection_list_selected_ids(selection_list: SelectionList[str]) -> list[str]:
    """Ordered selected ids from a SelectionList."""
    try:
        selected = list(selection_list.selected)
    except Exception:
        return []
    out: list[str] = []
    for item in selected:
        sid = str(item).strip()
        if sid and sid not in out:
            out.append(sid)
    return out
