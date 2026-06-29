"""Session-derived views on traces (usage stats, workspace diffs).

Complements :mod:`groket.parser` (raw timeline/tool extraction) with higher-level
aggregates used by the session Summary pane — still UI-agnostic.
"""

from __future__ import annotations

from .usage_stats import (
    McpServerUsage,
    SessionUsageStats,
    SkillUsageRow,
    ToolUsageRow,
    collect_session_usage,
    format_usage_markdown,
    format_usage_plain,
    format_usage_stats_text,
)
from .workspace_diff import format_diff_meta_line, load_workspace_diff

__all__ = [
    "McpServerUsage",
    "SessionUsageStats",
    "SkillUsageRow",
    "ToolUsageRow",
    "collect_session_usage",
    "format_diff_meta_line",
    "format_usage_markdown",
    "format_usage_plain",
    "format_usage_stats_text",
    "load_workspace_diff",
]
