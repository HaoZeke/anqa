"""Resolve work_dir / traces_root / feedback_cache from CLI args and user intent.

Runner always writes under ``work_dir/runs/traces``. Passing a path to ``groket`` sets both
what is loaded and (when the path is a work root) where new runs go — see
``resolve_work_and_traces``.

Environment:
  GROKET_WORK_DIR — default work root when no CLI path is given.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_APP_ROOT = Path.home() / "groket"

# Dot-directory for app-global state (config, cache, personas).
# Work dirs (traces, run_configs) are separate — typically DEFAULT_APP_ROOT or user-specified.
APP_HOME = Path.home() / ".groket"


def app_home() -> Path:
    """Return the app-global home directory, creating it if needed."""
    APP_HOME.mkdir(parents=True, exist_ok=True)
    return APP_HOME


def analysis_cache_dir() -> Path:
    """``~/.groket/cache`` — analysis result cache root."""
    d = APP_HOME / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def personas_home() -> Path:
    """``~/.groket/personas`` — app-global persona store."""
    d = APP_HOME / "personas"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_config_path() -> Path:
    """``~/.groket/config.json`` — app-global config file."""
    return APP_HOME / "config.json"


def user_rules_dir() -> Path:
    """``~/.groket/rules`` — user rule YAML overrides (merged on top of bundled rules)."""
    d = APP_HOME / "rules"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_detectors_dir() -> Path:
    """``~/.groket/detectors`` — user detector modules (``@detector`` registration)."""
    d = APP_HOME / "detectors"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_analysis_plugins_dir() -> Path:
    """``~/.groket/plugins`` — analysis plugin modules (``module:ClassName`` on sys.path)."""
    d = APP_HOME / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_tasks_dir() -> Path:
    """``~/.groket/tasks`` — optional user task YAML files (pass explicitly to ``batch --tasks``)."""
    d = APP_HOME / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_user_extension_dirs() -> dict[str, Path]:
    """Create standard user extension directories; return a name → path map."""
    return {
        "rules": user_rules_dir(),
        "detectors": user_detectors_dir(),
        "plugins": user_analysis_plugins_dir(),
        "tasks": user_tasks_dir(),
    }


# On-disk prefix for run/trace/container names.
# All run dirs and container names use this prefix.
RUN_PREFIX = "groket-"
RUN_PREFIXES = (RUN_PREFIX,)
# Session-side eval config filename (also embedded in images as groket-config.toml).
CONFIG_FILENAME = "groket-config.toml"


def is_run_dir_name(name: str) -> bool:
    """True for runner/batch trace or container names (``groket-*`` prefix)."""
    return bool(name) and name.startswith(RUN_PREFIX)


def strip_run_prefix(name: str) -> str:
    for pfx in RUN_PREFIXES:
        if name.startswith(pfx):
            return name[len(pfx) :]
    return name


def run_name(*parts: str) -> str:
    """Build a canonical run/container name with the on-disk prefix."""
    body = "-".join(str(p) for p in parts if p is not None and str(p) != "")
    return f"{RUN_PREFIX}{body}"


def _env_work_dir() -> Path | None:
    raw = (os.environ.get("GROKET_WORK_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return None


def default_work_dir() -> Path:
    env = _env_work_dir()
    return env if env is not None else DEFAULT_APP_ROOT


def default_traces_root(work_dir: Path | None = None) -> Path:
    wd = work_dir or default_work_dir()
    return Path(wd).expanduser() / "runs" / "traces"


def resolve_work_and_traces(
    path: Path | str | None = None,
    *,
    work_dir_override: Path | str | None = None,
) -> tuple[Path, Path]:
    """Return ``(work_dir, traces_root)`` for TUI / runner / batch / feedback.

    ``work_dir`` owns ``runs/`` (docker-build, traces, feedback_cache), reports, etc.
    ``traces_root`` is where sessions are discovered by default.
    """
    if work_dir_override is not None:
        wd = Path(work_dir_override).expanduser().resolve()
        tr = Path(path).expanduser().resolve() if path else default_traces_root(wd)
        return wd, tr

    if path is None:
        wd = default_work_dir()
        try:
            wd = wd.resolve()
        except OSError:
            pass
        return wd, default_traces_root(wd)

    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = Path(path).expanduser()

    parts = p.parts

    # …/runs/traces  or  …/runs/traces/<session>
    if len(parts) >= 2 and parts[-1] == "traces" and parts[-2] == "runs":
        wd = p.parent.parent
        return wd, p
    if len(parts) >= 3 and parts[-2] == "traces" and parts[-3] == "runs":
        wd = p.parent.parent.parent
        return wd, p.parent

    # …/runs/feedback_cache  or under it
    if len(parts) >= 2 and parts[-1] == "feedback_cache" and parts[-2] == "runs":
        wd = p.parent.parent
        return wd, default_traces_root(wd)
    if len(parts) >= 3 and parts[-2] == "feedback_cache" and parts[-3] == "runs":
        wd = p.parent.parent.parent
        return wd, default_traces_root(wd)

    # …/runs  (batch/orchestrator style work root)
    if parts and parts[-1] == "runs":
        wd = p.parent
        return wd, p / "traces"

    # …/traces (standalone traces folder, not under runs/)
    if parts and parts[-1] == "traces":
        wd = p.parent
        return wd, p

    if p.is_dir():
        if (p / "runs" / "traces").is_dir() or (p / "runs").is_dir():
            return p, p / "runs" / "traces"
        if (p / "traces").is_dir():
            return p, p / "traces"
        session_markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl", "summary.json")
        if any((p / m).exists() for m in session_markers):
            parent = p.parent
            # runs/traces/<session> is handled above via parts; remaining cases:
            if parent.name == "traces":
                return parent.parent, parent
            return parent, parent

        # Empty or custom work root (e.g. ./b2) — use it as work_dir
        return p, p / "runs" / "traces"

    if not p.suffix:
        return p, p / "runs" / "traces"

    wd = default_work_dir()
    return wd, default_traces_root(wd)


def traces_root_for_reload(work_dir: Path, traces_path: Path | None) -> Path:
    """Path to rescan after a Docker run finishes."""
    if traces_path is not None:
        tp = Path(traces_path)
        if tp.is_dir():
            markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl")
            if any((tp / m).exists() for m in markers):
                return tp.parent
            return tp
    return default_traces_root(work_dir)
