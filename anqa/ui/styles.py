"""Semantic style constants for Rich markup and Text objects.

Roles only — not a hex palette. Chrome uses the active Textual theme
(``$success``, ``$warning``, ``$error``, ``$text-muted``). Timeline and
status markup uses the matching ANSI names so the terminal's own colors
win on ``auto``.
"""

from __future__ import annotations

from contextlib import suppress

from textual.app import App

from ..session.query import QuerySpanKind
from ..tool_display import format_tool_display, tool_family

# One job, one ANSI face. Theme tokens paint the same roles in TCSS / HUD.
SUCCESS = "green"
CAUTION = "yellow"
DANGER = "red"
QUIET = "dim"
EMPHASIS = "default"

# Catalog search box — modifier vs value (same roles on the HUD).
QUERY_SPAN_STYLE: dict[QuerySpanKind, str] = {
    "field": f"bold {CAUTION}",
    "operator": f"bold {CAUTION}",
    "value": SUCCESS,
    "unknown": DANGER,
}

SEVERITY_STYLE: dict[str, str] = {
    "high": f"{DANGER} bold",
    "medium": f"{CAUTION} bold",
    "low": CAUTION,
}

SEVERITY_LABEL: dict[str, str] = {
    "high": f"[{DANGER} bold]High[/]",
    "medium": f"[{CAUTION} bold]Medium[/]",
    "low": f"[{CAUTION}]Low[/]",
}


# sessionUpdate / events.jsonl type → Rich style (identity keys).
EVENT_TYPE_STYLE: dict[str, str] = {
    "user_message_chunk": f"bold {EMPHASIS}",
    "agent_message_chunk": EMPHASIS,
    "agent_thought_chunk": f"{QUIET} italic",
    "plan": EMPHASIS,
    "tool_call": f"bold {SUCCESS}",
    "tool_call_update": f"{QUIET} {SUCCESS}",
    "task_backgrounded": f"bold {CAUTION}",
    "task_completed": CAUTION,
    "scheduled_task_created": CAUTION,
    "scheduled_task_updated": CAUTION,
    "scheduled_task_fired": CAUTION,
    "scheduled_task_deleted": CAUTION,
    "turn_completed": CAUTION,
    "subagent_spawned": EMPHASIS,
    "subagent_finished": EMPHASIS,
    "current_mode_update": f"{QUIET} {CAUTION}",
    "retry_state": f"{QUIET} {CAUTION}",
    "goal_updated": CAUTION,
    "session_recap": CAUTION,
    "auto_compact_started": CAUTION,
    "auto_compact_completed": CAUTION,
    "compaction_checkpoint": CAUTION,
    "hook_execution": CAUTION,
    "hook_annotation": CAUTION,
    "turn_started": CAUTION,
    "turn_ended": CAUTION,
    "session_error": f"bold {DANGER}",
    "error": f"bold {DANGER}",
    "turn_error": f"bold {DANGER}",
    "fatal_error": f"bold {DANGER}",
    "system": QUIET,
    "user": f"bold {EMPHASIS}",
    "assistant": EMPHASIS,
    "thought": f"{QUIET} italic",
    "tool_result": f"{QUIET} {SUCCESS}",
    "subagent": EMPHASIS,
    "session": CAUTION,
}

# Type column uses stored identifiers (spaces from underscores in type_label).
EVENT_TYPE_LABEL: dict[str, str] = {
    k: f"[{v}]{k.replace('_', ' ')}[/]" for k, v in EVENT_TYPE_STYLE.items()
}

# Failed tool call — sits after the family-colored name (not a recolor).
TOOL_ERROR_MARK = "⚠"


# Action family → role (scannable, not a rainbow).
TOOL_FAMILY_STYLE: dict[str, str] = {
    "read": EMPHASIS,
    "write": SUCCESS,
    "shell": CAUTION,
    "agent": EMPHASIS,
    "mcp": QUIET,
    "other": QUIET,
}


# Run / container lifecycle — one palette for tables, activity bar, labels.
STATUS_RICH_STYLE: dict[str, str] = {
    "pending": QUIET,
    "building": f"bold {CAUTION}",
    "running": f"bold {CAUTION}",
    "ending": f"bold {QUIET}",
    "awaiting": f"bold {QUIET}",
    "extracting": f"bold {CAUTION}",
    "completed": f"bold {SUCCESS}",
    "failed": f"bold {DANGER}",
    "idle": QUIET,
}


def theme_is_light(name: str) -> bool:
    """True when a theme name is a light colorway."""
    n = (name or "").strip().lower()
    return any(tok in n for tok in ("light", "latte", "dawn", "lotus", "operandi", "day", "paper"))


def active_theme_is_light() -> bool:
    """True when the running Textual app is on a light paper theme."""
    with suppress(Exception):
        app = getattr(App, "get_running_app", lambda: None)()
        if app is not None:
            return theme_is_light(getattr(app, "theme", "") or "")
    return False


def event_type_markup(event_type: str, *, light: bool = False) -> str:
    """Styled Type-column label, or empty when *event_type* has no palette entry."""
    del light
    style = EVENT_TYPE_STYLE.get(event_type)
    if not style:
        return ""
    return f"[{style}]{event_type.replace('_', ' ')}[/]"


STATUS_LABEL: dict[str, str] = {
    "pending": f"[{STATUS_RICH_STYLE['pending']}]Pending[/]",
    "building": f"[{STATUS_RICH_STYLE['building']}]Building…[/]",
    "running": f"[{STATUS_RICH_STYLE['running']}]Running…[/]",
    "ending": f"[{STATUS_RICH_STYLE['ending']}]Ending…[/]",
    "extracting": f"[{STATUS_RICH_STYLE['extracting']}]Extracting…[/]",
    "completed": f"[{STATUS_RICH_STYLE['completed']}]Completed[/]",
    "failed": f"[{STATUS_RICH_STYLE['failed']}]Failed[/]",
}


def status_rich_style(status: str, *, light: bool = False) -> str:
    """Rich style for a container/run status name (``running``, ``failed``, …)."""
    del light
    return STATUS_RICH_STYLE.get((status or "").strip().lower(), STATUS_RICH_STYLE["idle"])


SYNTAX_THEME_LIGHT = "friendly"
SYNTAX_THEME_DARK = "monokai"

# Textual theme name substring → Pygments style (no code-block background).
_SYNTAX_BY_THEME: tuple[tuple[str, str], ...] = (
    ("solarized-light", "solarized-light"),
    ("solarized", "solarized-dark"),
    ("gruvbox-light", "gruvbox-light"),
    ("gruvbox", "gruvbox-dark"),
    ("nord", "nord"),
    ("github-light", SYNTAX_THEME_LIGHT),
    ("textual-light", SYNTAX_THEME_LIGHT),
    ("ansi-light", SYNTAX_THEME_LIGHT),
    ("catppuccin", "dracula"),
    ("tokyo-night", "nord"),
    ("everforest", "gruvbox-dark"),
)


def syntax_theme_for_app(app: App) -> str:
    """Pick a Pygments style that follows the active Textual theme name."""
    with suppress(Exception):
        name = (getattr(app, "theme", "") or "").lower()
        for needle, pygments in _SYNTAX_BY_THEME:
            if needle in name:
                return pygments
        if "light" in name:
            return SYNTAX_THEME_LIGHT
    return SYNTAX_THEME_DARK


def severity_style(value: str) -> str:
    """Rich style string for a severity value (``"high"`` / ``"medium"`` / ``"low"``)."""
    return SEVERITY_STYLE.get(value, QUIET)


def tool_style(name: str, *, light: bool = False) -> str:
    """Rich style for a tool name (family palette)."""
    del light
    return TOOL_FAMILY_STYLE.get(tool_family(name or ""), TOOL_FAMILY_STYLE["other"])


def tool_label(name: str, *, max_len: int = 32, light: bool = False) -> str:
    """Rich markup label for a tool name in tables (MCP shown as server · method)."""
    display = format_tool_display(name or "?")
    if len(display) > max_len:
        display = display[: max_len - 1] + "…"
    style = tool_style(name, light=light)
    safe = display.replace("[", "\\[").replace("]", "\\]")
    return f"[{style}]{safe}[/]"
