"""Session-derived views on traces (usage stats, workspace diffs).

Complements :mod:`groket.parser` (raw timeline/tool extraction) with higher-level
aggregates used by the session Summary pane — still UI-agnostic.
"""

from __future__ import annotations

from .export_bundle import (
    ExportBundleResult,
    export_session_bundle,
    run_volume_for_session,
)
from .import_session import (
    HostSessionRow,
    ImportSessionResult,
    host_grok_sessions_root,
    import_session,
    is_session_directory,
    list_host_grok_sessions,
)
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
    "ExportBundleResult",
    "HostSessionRow",
    "ImportSessionResult",
    "McpServerUsage",
    "SessionUsageStats",
    "SkillUsageRow",
    "ToolUsageRow",
    "collect_session_usage",
    "export_session_bundle",
    "format_diff_meta_line",
    "format_usage_markdown",
    "format_usage_plain",
    "format_usage_stats_text",
    "host_grok_sessions_root",
    "import_session",
    "is_session_directory",
    "list_host_grok_sessions",
    "load_workspace_diff",
    "run_volume_for_session",
]
