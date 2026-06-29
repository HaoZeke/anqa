"""Shared form helpers for structured Runner / modal inputs (Textual Select, etc.)."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from textual.widgets import SelectionList

from ..docker.base_profiles import DEFAULT_DOCKER_IMAGE
from ..runs.batch import active_model_ids, load_models
from ..runs.personas import PersonaStore
from .i18n import t

DOCKER_IMAGE_OPTIONS: list[tuple[str, str]] = [
    (t("ui-fully-loaded-full-tools-share-loop"), "fully-loaded"),
    (t("ui-minimal-baseline-share-loop-setup-sh-for-rest"), "minimal"),
    (t("ui-ubuntu-24-04-fully-loaded"), "ubuntu:24.04@fully-loaded"),
    (t("ui-debian-bookworm-fully-loaded"), "debian:bookworm@fully-loaded"),
    (t("ui-ubuntu-24-04-raw-os-still-share-loop-via-entrypo"), "ubuntu:24.04"),
]
BATCH_PARALLEL_OPTIONS: list[tuple[str, str]] = [
    (f"{n}{t('ui-config-s-in-flight')}", str(n)) for n in (1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24, 32)
]
PERSONA_NONE = "__none__"


def docker_select_options() -> list[tuple[str, str]]:
    return list(DOCKER_IMAGE_OPTIONS)


def normalize_docker_choice(value: str | None) -> str:
    v = (value or "").strip() or DEFAULT_DOCKER_IMAGE
    known = {opt[1] for opt in DOCKER_IMAGE_OPTIONS}
    if v in known:
        return v
    low = v.lower()
    if low in ("full", "groket:fully-loaded", "default"):
        return "fully-loaded"
    if low in ("min", "bare"):
        return "minimal"
    return DEFAULT_DOCKER_IMAGE


def docker_select_value_or_default(stored: str | None) -> str:
    return normalize_docker_choice(stored)


def load_active_model_ids() -> list[str]:
    """Canonical ids from ``~/.grok/models_cache.json`` (``grok models``), not models.yaml alone."""
    with suppress(Exception):
        ids = list(active_model_ids() or [])
        if ids:
            return ids
    try:
        return list(load_models() or [])
    except Exception:
        return []


def default_model_selection() -> list[str]:
    """Single default model for a blank runner (host config / catalog), not all models."""
    try:
        from ..runs.batch import default_model_id

        mid = (default_model_id() or "").strip()
        if mid:
            return [mid]
    except Exception:
        pass
    cat = load_active_model_ids()
    return [cat[0]] if cat else []


def model_selection_items(
    selected: Sequence[str] | None = None,
    *,
    catalog: Sequence[str] | None = None,
    default_select_all: bool = False,
) -> list[tuple[str, str, bool]]:
    """Build SelectionList triples: (label, id, initially_selected).

    When *selected* is empty/None and *default_select_all* is False (default),
    only the host default model is ticked — not the full catalog. Pass an
    explicit list (e.g. from a saved run config) to pre-select those ids.
    Set *default_select_all* True only for rare “run everything” entry points.
    """
    cat = list(catalog if catalog is not None else load_active_model_ids())
    sel = {m.strip() for m in selected or [] if (m or "").strip()}
    if not sel and cat:
        if default_select_all:
            sel = set(cat)
        else:
            sel = set(default_model_selection())
            if sel and (not sel & set(cat)):
                sel = {cat[0]}
    extra = [m for m in sorted(sel) if m not in cat]
    items: list[tuple[str, str, bool]] = []
    for mid in cat:
        label = mid
        items.append((label, mid, mid in sel))
    for mid in extra:
        items.append((f"{mid} [not in catalog]", mid, True))
    return items


def persona_select_options(work_dir: Path | None) -> list[tuple[str, str]]:
    opts: list[tuple[str, str]] = [(t("ui-none-run-defaults-only"), PERSONA_NONE)]
    if work_dir is None:
        return opts
    with suppress(Exception):
        store = PersonaStore(work_dir)
        store.ensure_defaults()
        for p in store.list():
            bits: list[str] = []
            if p.github_write:
                bits.append("gh-write")
            if (p.github_token or "").strip():
                bits.append("token")
            elif (p.github_token_env or "").strip():
                bits.append("token-env")
            suffix = f" · {'+'.join(bits)}" if bits else ""
            label = f"{p.name or p.persona_id}{suffix}"
            opts.append((label, p.persona_id))
    return opts


def normalize_persona_id(value: str | None | object) -> str:
    """UI select value → stored/launch persona_id (empty string means none)."""
    if value is None:
        return ""
    if type(value).__name__ in ("NoSelection", "_NoSelection"):
        return ""
    s = str(value).strip()
    if not s or s in (PERSONA_NONE, "None", "False", "True"):
        return ""
    return s


def persona_select_value(stored_persona_id: str | None) -> str:
    """Stored persona_id → Select.value (always a valid option id)."""
    s = (stored_persona_id or "").strip()
    return s if s else PERSONA_NONE


def select_value_str(widget_value: str | None | object, *, default: str = "") -> str:
    """Coerce Select.value (may be sentinel) to str."""
    if widget_value is None:
        return default
    if type(widget_value).__name__ in ("NoSelection", "_NoSelection"):
        return default
    if isinstance(widget_value, bool):
        return default
    s = str(widget_value).strip()
    if s in ("", "None", "False", "True"):
        return default
    return s


def selection_list_selected_ids(selection_list: SelectionList[str]) -> list[str]:
    """Ordered selected model/persona ids from a SelectionList."""
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


def batch_parallel_options() -> list[tuple[str, str]]:
    return list(BATCH_PARALLEL_OPTIONS)


def normalize_batch_parallel(value: str | int | None, default: int = 2) -> int:
    try:
        n = int(value if value is not None else default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(32, n))
