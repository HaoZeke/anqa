"""Application paths: config under ``~/.anqa``.

**Config home** (``APP_HOME`` / ``~/.anqa``) holds ``config.toml``,
notes, reports, optional ``keys.toml``. The session catalog is the
adapter store (default ``~/.grok/sessions``). A CLI path selects a
store or a session.
"""

from __future__ import annotations

from pathlib import Path

# App-global state and user extensions (not per-workspace run data).
APP_HOME = Path.home() / ".anqa"


def app_home() -> Path:
    """Return the app-global home directory, creating it if needed."""
    APP_HOME.mkdir(parents=True, exist_ok=True)
    return APP_HOME


def cache_dir() -> Path:
    """``~/.anqa/cache`` — host catalog snapshot and other local cache."""
    d = APP_HOME / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_config_path() -> Path:
    """``~/.anqa/config.toml`` — app-global prefs file."""
    return APP_HOME / "config.toml"


def user_keys_path() -> Path:
    """``~/.anqa/keys.toml`` — optional key overlay (diffs over the catalog)."""
    return APP_HOME / "keys.toml"


def user_themes_dir() -> Path:
    """``~/.anqa/themes`` — optional named colorways (``*.toml``)."""
    return APP_HOME / "themes"


def reports_dir() -> Path:
    """``~/.anqa/reports`` — finding Markdown and session export tarballs."""
    d = APP_HOME / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def notes_fallback_dir(session_id: str) -> Path:
    """``~/.anqa/notes/<session_id>`` — operator notes when session dir is not writable."""
    d = APP_HOME / "notes" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_export_profiles_dir() -> Path:
    """``~/.anqa/export_profiles`` — user session-export profile YAML."""
    d = APP_HOME / "export_profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


# On-disk prefix for leftover work-tree / container names.
RUN_PREFIX = "anqa-"
# Session-side config filename beside a leftover work tree.
CONFIG_FILENAME = "anqa-config.toml"


def is_run_dir_name(name: str) -> bool:
    """True when *name* uses the leftover work-tree prefix (``anqa-*``)."""
    return bool(name) and name.startswith(RUN_PREFIX)


def strip_run_prefix(name: str) -> str:
    if name.startswith(RUN_PREFIX):
        return name[len(RUN_PREFIX) :]
    return name


def default_host_sessions_root() -> Path:
    """Default Grok session store: ``~/.grok/sessions``."""
    return Path.home() / ".grok" / "sessions"


def resolve_catalog_root(path: Path | str | None = None) -> Path:
    """Adapter store for the terminal app and serve.

    Default ``~/.grok/sessions``. A ``-P`` store tree, or the parent of a
    ``-P`` session directory.
    """
    if path is None:
        return default_host_sessions_root()

    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = Path(path).expanduser()

    if p.name == "sessions" and p.parent.name == ".grok":
        return p

    session_markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl", "summary.json")
    if p.is_dir() and any((p / marker).is_file() for marker in session_markers):
        parent = p.parent
        if parent.name == "sessions" and parent.parent.name == ".grok":
            return parent
        return parent

    if p.is_dir():
        return p
    return default_host_sessions_root()


def traces_root_for_reload(traces_path: Path | None) -> Path:
    """Path to rescan when the catalog store changes."""
    if traces_path is not None:
        tp = Path(traces_path)
        if tp.is_dir():
            markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl")
            if any((tp / m).exists() for m in markers):
                return tp.parent
            return tp
    return default_host_sessions_root()
