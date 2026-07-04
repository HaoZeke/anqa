"""Semantic style constants for Rich markup and Text objects.

All Python-side color choices live here.  Screens and widgets import from
this module instead of hardcoding color names.  The TCSS layer (``app.tcss``)
uses Textual ``$`` design tokens and is maintained separately.

One-off structural markup (``[dim]``, ``[bold]``) is fine inline — only
*semantic concepts* that repeat across files belong in this module.
"""

from __future__ import annotations

from contextlib import suppress

from textual.app import App

SEVERITY_STYLE: dict[str, str] = {
    "high": "red bold",
    "medium": "dark_orange bold",
    "low": "yellow",
}

SEVERITY_LABEL: dict[str, str] = {
    "high": "[red bold]High[/]",
    "medium": "[dark_orange bold]Medium[/]",
    "low": "[yellow]Low[/]",
}


def finding_mark(severity: str) -> str:
    """Timeline / report glyph for an automated finding (⚠ + severity color)."""
    sev = (severity or "low").lower()
    style = SEVERITY_STYLE.get(sev, SEVERITY_STYLE["low"])
    return f"[{style}]⚠[/]"


# Small palette by *role* (not a rainbow per label):
#   white  = human input
#   cyan   = model / tools stream (call vs result = bold vs dim)
#   yellow = session runtime
#   red    = error (also applied as underline in the timeline widget)

# Grok sessionUpdate / events.jsonl type → Rich style (identity keys).
EVENT_TYPE_STYLE: dict[str, str] = {
    "user_message_chunk": "bold white",
    "agent_message_chunk": "cyan",
    "agent_thought_chunk": "dim cyan italic",
    "plan": "cyan",
    "tool_call": "bold cyan",
    "tool_call_update": "dim cyan",
    "task_backgrounded": "bold yellow",
    "task_completed": "yellow",
    "turn_completed": "yellow",
    "subagent_spawned": "cyan",
    "subagent_finished": "cyan",
    "current_mode_update": "dim yellow",
    "retry_state": "dim yellow",
    "turn_started": "yellow",
    "turn_ended": "yellow",
    "session_error": "bold red",
    "error": "bold red",
    "turn_error": "bold red",
    "fatal_error": "bold red",
    "system": "bold magenta",
    # legacy pre-taxonomy names (cached / old tests)
    "user": "bold white",
    "assistant": "cyan",
    "thought": "dim cyan italic",
    "tool_result": "dim cyan",
    "subagent": "cyan",
    "session": "yellow",
}

# Type column uses Grok identifiers (spaces from underscores in type_label).
EVENT_TYPE_LABEL: dict[str, str] = {
    k: f"[{v}]{k.replace('_', ' ')}[/]" for k, v in EVENT_TYPE_STYLE.items()
}

# Color by *action family*, not per-tool identity (keeps the column scannable):
#   cyan   = read / search / inspect
#   green  = write / edit / mutate workspace
#   yellow = shell / process / wait
#   white  = agent / UI / plan / other (default)

_TOOL_FAMILY_READ = frozenset(
    {
        "read_file",
        "grep",
        "list_dir",
        "web_search",
        "read_resource",
        "list_resources",
    }
)
_TOOL_FAMILY_WRITE = frozenset(
    {
        "search_replace",
        "write_file",
        "create_file",
        "todo_write",
        "update_goal",
        "image_gen",
        "image_edit",
        "image_to_video",
        "reference_to_video",
    }
)
_TOOL_FAMILY_SHELL = frozenset(
    {
        "run_terminal_command",
        "get_command_or_subagent_output",
        "kill_command_or_subagent",
        "wait_commands_or_subagents",
        "monitor",
        "scheduler_create",
        "scheduler_delete",
        "scheduler_list",
    }
)
_TOOL_FAMILY_AGENT = frozenset(
    {
        "spawn_subagent",
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode",
        "use_tool",
        "search_tool",
        "call_mcp",
        "search_mcp",
    }
)

TOOL_FAMILY_STYLE: dict[str, str] = {
    "read": "cyan",
    "write": "green",
    "shell": "yellow",
    "agent": "white",
    "mcp": "magenta",
    "other": "dim",
}

# Explicit overrides (optional); prefer families via tool_style().
TOOL_STYLE: dict[str, str] = {}


def tool_family(name: str) -> str:
    """Map a tool name to read | write | shell | agent | mcp | other."""
    n = (name or "").strip()
    # MCP / plugin tools often look like server__method
    if "__" in n or n.startswith("mcp_"):
        return "mcp"
    if n in _TOOL_FAMILY_READ:
        return "read"
    if n in _TOOL_FAMILY_WRITE:
        return "write"
    if n in _TOOL_FAMILY_SHELL:
        return "shell"
    if n in _TOOL_FAMILY_AGENT:
        return "agent"
    # Heuristics for MCP / unknown tools
    low = n.lower()
    if any(k in low for k in ("read", "get", "list", "search", "grep", "find")):
        return "read"
    if any(k in low for k in ("write", "edit", "create", "update", "delete", "save")):
        return "write"
    if any(k in low for k in ("run", "exec", "shell", "terminal", "wait", "kill")):
        return "shell"
    return "other"


def format_tool_display(name: str) -> str:
    """Human tool column text: host ids stay snake_case; MCP as ``server · method``."""
    n = (name or "").strip()
    if not n:
        return "?"
    if "__" in n:
        server, method = n.split("__", 1)
        server, method = server.strip(), method.strip()
        if server and method:
            return f"{server} · {method}"
    if n.startswith("mcp_") and len(n) > 4:
        return f"mcp · {n[4:]}"
    return n


# Run / container lifecycle — one palette for tables, activity bar, labels.
STATUS_RICH_STYLE: dict[str, str] = {
    "pending": "dim",
    "building": "bold cyan",
    "running": "bold yellow",
    "ending": "bold magenta",
    "awaiting": "bold cyan",
    "extracting": "bold cyan",
    "completed": "bold green",
    "failed": "bold red",
    "idle": "dim",
}

STATUS_LABEL: dict[str, str] = {
    "pending": f"[{STATUS_RICH_STYLE['pending']}]Pending[/]",
    "building": f"[{STATUS_RICH_STYLE['building']}]Building…[/]",
    "running": f"[{STATUS_RICH_STYLE['running']}]Running…[/]",
    "ending": f"[{STATUS_RICH_STYLE['ending']}]Ending…[/]",
    "extracting": f"[{STATUS_RICH_STYLE['extracting']}]Extracting…[/]",
    "completed": f"[{STATUS_RICH_STYLE['completed']}]Completed[/]",
    "failed": f"[{STATUS_RICH_STYLE['failed']}]Failed[/]",
}


def status_rich_style(status: str) -> str:
    """Rich style for a container/run status name (``running``, ``failed``, …)."""
    return STATUS_RICH_STYLE.get((status or "").strip().lower(), STATUS_RICH_STYLE["idle"])


SYNTAX_THEME_LIGHT = "friendly"
SYNTAX_THEME_DARK = "monokai"


def syntax_theme_for_app(app: App) -> str:
    """Pick a Syntax highlight theme matching the active Textual theme.

    ``app`` is the Textual ``App`` instance (or any object with a ``.theme``
    attribute).  Falls back to the dark theme when detection fails.
    """
    with suppress(Exception):
        name = getattr(app, "theme", "") or ""
        if "light" in name.lower():
            return SYNTAX_THEME_LIGHT
    return SYNTAX_THEME_DARK


def severity_style(value: str) -> str:
    """Rich style string for a severity value (``"high"`` / ``"medium"`` / ``"low"``)."""
    return SEVERITY_STYLE.get(value, "white")


def tool_style(name: str) -> str:
    """Rich style for a tool name (family palette, optional per-name override)."""
    if name and name in TOOL_STYLE:
        return TOOL_STYLE[name]
    return TOOL_FAMILY_STYLE.get(tool_family(name or ""), TOOL_FAMILY_STYLE["other"])


def tool_label(name: str, *, max_len: int = 32) -> str:
    """Rich markup label for a tool name in tables (MCP shown as server · method)."""
    display = format_tool_display(name or "?")
    if len(display) > max_len:
        display = display[: max_len - 1] + "…"
    style = tool_style(name)
    safe = display.replace("[", "\\[").replace("]", "\\]")
    return f"[{style}]{safe}[/]"
