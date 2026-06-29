"""Main Textual application for groket.

UI entry point only: domain work goes through ``services``, ``analysis``,
``run_manager``, ``personas`` — not embedded business logic in screens.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Select,
    Static,
)

from ..analysis import AnalysisResult, AnalysisService, get_analysis_service, set_analysis_service
from ..analysis.base import Finding
from ..constants import META_CACHE_FILENAME, META_LOAD_WORKERS
from ..models import JsonObject, SessionMeta
from ..parser import extract_prompt, find_sessions, load_session_meta
from ..paths import app_config_path
from ..runs.run_manager import BackgroundRun, RunManager
from . import text as U
from .bindings import (
    APP_GLOBAL_PRIORITY,
    APP_SESSIONS,
    FORM_SAVE,
    SESSION_HOME_ACTIONS,
    SESSION_SEARCH_MODAL,
    focus_primary_list,
)
from .data_table import cursor_row_key, restore_cursor, set_marker_column, style_data_table
from .fuzzy import fzf_match
from .i18n import setup_i18n, t
from .screens.browser import BrowserScreen
from .screens.rules import RulesScreen
from .screens.run_configs import RunConfigsScreen
from .screens.runner import RunnerPrefill, RunnerScreen
from .threads import call_ui
from .widgets.controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS

logger = logging.getLogger(__name__)
_SESSION_FILTER_ALL = "all"


def _coerce_select_value(value, *, default=None):
    """Normalize Textual Select values (sentinel / None) to a plain choice."""
    if value is None:
        return default
    try:
        from textual.widgets import Select as _Select

        if value == getattr(_Select, "BLANK", object()):
            return default
    except Exception:
        pass
    name = type(value).__name__
    if name in ("NoSelection", "MissingValue", "Null", "_NoSelection"):
        return default
    if not isinstance(value, (str, int, float, bool)):
        return default
    return value


class InteractiveSessionsModal(ModalScreen[tuple[str, bool] | None]):
    """Prompt for a follow-up on awaiting sessions (sessions home).

    Dismisses with ``(prompt, final_turn)`` or ``None`` on cancel. When
    *final_turn* is true, the gate runs this turn then stops awaiting
    (same as the browser pending bar). Mark-done (``e``) remains separate.
    """

    BINDINGS = list(FORM_SAVE)

    def __init__(self, *, n_awaiting: int) -> None:
        super().__init__()
        self._n = max(1, int(n_awaiting))

    def compose(self) -> ComposeResult:
        with Container(id="interactive-sessions-modal"):
            yield Label(U.interactive_modal_title(self._n), id="interactive-modal-title")
            yield Input(placeholder=U.follow_up_placeholder(), id="interactive-follow-input")
            yield Checkbox(
                t("follow-up-last-turn"),
                id="interactive-follow-last-turn",
                value=False,
            )
            with Horizontal(id="interactive-modal-actions"):
                yield Button(U.send(), variant="primary", id="interactive-send")
                yield Button(U.cancel(), id="interactive-cancel")

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#interactive-follow-input", Input).focus()

    def action_save(self) -> None:
        self._submit_follow()

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    @on(Button.Pressed, "#interactive-send")
    def _on_send(self) -> None:
        self._submit_follow()

    @on(Button.Pressed, "#interactive-cancel")
    def _on_cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#interactive-follow-input")
    def _on_submit_input(self) -> None:
        self._submit_follow()

    def _submit_follow(self) -> None:
        try:
            text = self.query_one("#interactive-follow-input", Input).value.strip()
        except Exception:
            text = ""
        if not text:
            with suppress(Exception):
                self.notify(U.follow_up_empty(), severity="warning", timeout=2)
            return
        final = False
        with suppress(Exception):
            final = bool(self.query_one("#interactive-follow-last-turn", Checkbox).value)
        self.dismiss((text, final))


class AnalysisSettingsModal(ModalScreen[bool]):
    """Configure analysis behaviour (all registered plugins run on analyze)."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, work_dir: Path) -> None:
        super().__init__()
        self._work_dir = Path(work_dir)

    def compose(self) -> ComposeResult:
        from ..analysis import list_analyzers, load_pipeline_config

        app = self.app
        config_path = getattr(app, "_config_path", None)
        cfg = load_pipeline_config(self._work_dir, config_path=config_path)
        from ..analysis import get_analysis_service

        try:
            svc = get_analysis_service()
            plugin_list = ", ".join(p.id for p in svc.list_plugins() if p.id != "noop") or "(none)"
        except Exception:
            plugin_list = ", ".join(p.id for p in list_analyzers() if p.id != "noop") or "(none)"
        with Container(id="analysis-settings-modal"):
            yield Label(U.analysis_pipeline_title(), id="analysis-settings-title")
            yield Static(
                f"{t('ui-enabled-analyzers')} {plugin_list}. Optional plugins: analysis.plugins as module:ClassName (active config: {config_path or '~/.groket or work_dir'}).",
                id="analysis-settings-help",
            )
            yield Checkbox(
                U.auto_analyze_on_open(), value=cfg.auto_analyze_on_open, id="as-auto-analyze"
            )
            from .prefs import show_tips_enabled

            yield Checkbox(U.show_tips_checkbox(), value=show_tips_enabled(), id="as-show-tips")
            with Horizontal(id="analysis-settings-actions"):
                yield Button(U.save(), variant="primary", id="as-save")
                yield Button(U.cancel(), id="as-cancel")

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, False)

    def action_save(self) -> None:
        self._persist()

    @on(Button.Pressed, "#as-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#as-save")
    def _save(self) -> None:
        self._persist()

    def _persist(self) -> None:
        from ..analysis import (
            AnalysisPipelineConfig,
            AnalysisService,
            load_pipeline_config,
            save_pipeline_config,
            set_analysis_service,
        )
        from .prefs import set_show_tips

        auto = self.query_one("#as-auto-analyze", Checkbox).value
        try:
            tips = bool(self.query_one("#as-show-tips", Checkbox).value)
            set_show_tips(tips)
        except Exception:
            pass
        app = self.app
        config_path = getattr(app, "_config_path", None)
        prev = load_pipeline_config(self._work_dir, config_path=config_path)
        cfg = AnalysisPipelineConfig(plugins=list(prev.plugins), auto_analyze_on_open=bool(auto))
        save_pipeline_config(self._work_dir, cfg)
        from ..paths import analysis_cache_dir

        svc = AnalysisService(
            self._work_dir, config=cfg, config_path=config_path, cache_root=analysis_cache_dir()
        )
        set_analysis_service(svc)
        try:
            refresh = getattr(app, "_refresh_all_tip_surfaces", None)
            if callable(refresh):
                refresh()
        except Exception:
            pass
        self.dismiss(True)


class SessionSearchModal(ModalScreen):
    """Fuzzy search modal for filtering the session list."""

    BINDINGS = list(SESSION_SEARCH_MODAL)

    def __init__(self, sessions: list[tuple[SessionMeta, str]]) -> None:
        super().__init__()
        self._sessions = sessions
        self._results: list[int] = []

    async def action_dismiss(self, result: object = None) -> None:  # noqa: ARG002
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def compose(self) -> ComposeResult:
        with Container(id="session-search-modal"):
            yield Label(U.fuzzy_search_sessions(), id="session-search-label")
            yield Input(placeholder=U.type_to_filter(), id="session-search-input")
            yield OptionList(*self._build_all_options(), id="session-search-list")

    def _build_all_options(self) -> list[Text]:
        self._results = list(range(len(self._sessions)))
        return [Text(self._session_label(i)) for i in self._results]

    def _session_label(self, idx: int) -> str:
        """Build a rich search candidate string covering many metadata fields."""
        meta, label = self._sessions[idx]
        model = meta.model_display
        title = meta.label[:40]
        parts = [meta.session_id[:12], model, title, label]
        if meta.task_id:
            parts.append(meta.task_id)
        if meta.git_repo:
            repo_short = meta.git_repo.rstrip("/").rsplit("/", 1)[-1]
            parts.append(repo_short)
        if meta.summary_text:
            parts.append(meta.summary_text[:60])
        if meta.turn_outcome:
            parts.append(f"turn:{meta.turn_outcome}")
        return "  ".join(parts)

    def on_mount(self) -> None:
        self.query_one("#session-search-input", Input).focus()

    def _move_highlight(self, delta: int) -> None:
        ol = self.query_one("#session-search-list", OptionList)
        if ol.option_count == 0:
            return
        current = ol.highlighted if ol.highlighted is not None else -1
        ol.highlighted = max(0, min(ol.option_count - 1, current + delta))
        ol.scroll_to_highlight()

    def action_cursor_up(self) -> None:
        self._move_highlight(-1)

    def action_cursor_down(self) -> None:
        self._move_highlight(1)

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        ol = self.query_one("#session-search-list", OptionList)
        if not query:
            options = self._build_all_options()
            ol.set_options(options)
        else:
            scored: list[tuple[float, Text, int]] = []
            for i in range(len(self._sessions)):
                candidate = self._session_label(i)
                score, display = fzf_match(query, candidate)
                if score > 0:
                    scored.append((score, display, i))
            scored.sort(key=lambda x: -x[0])
            self._results = [idx for _, _, idx in scored]
            ol.set_options([display for _, display, _ in scored])
        if ol.option_count > 0:
            ol.highlighted = 0

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#session-search-list", OptionList)
        if ol.option_count > 0 and ol.highlighted is not None:
            self._dismiss_at(ol.highlighted)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._dismiss_at(event.option_index)

    def _dismiss_at(self, idx: int) -> None:
        if 0 <= idx < len(self._results):
            session_idx = self._results[idx]
            meta, label = self._sessions[session_idx]
            self.dismiss(str(meta.session_dir))


class TraceEvalApp(App):
    """groket — Trace evaluation TUI for hunting bad model behaviors."""

    TITLE = "groket"
    SUB_TITLE = t("ui-trace-evaluation-error-hunting")
    CSS_PATH = "app.tcss"
    BINDINGS = [*APP_GLOBAL_PRIORITY, *APP_SESSIONS]

    def get_system_commands(self, screen: Screen):
        """Populate Ctrl+P palette with context-aware actions."""
        yield from super().get_system_commands(screen)
        from .commands import yield_app_commands

        for title, help_text, callback in yield_app_commands(self, screen):
            yield SystemCommand(title, help_text, callback)

    class _BgStatus(Message):
        """Worker → UI: container status with a session_dir (thread-safe via post_message)."""

        def __init__(self, status: object) -> None:
            super().__init__()
            self.status = status

    class _BgFinished(Message):
        """Worker → UI: background run finished."""

        def __init__(self, run: BackgroundRun) -> None:
            super().__init__()
            self.run = run

    def __init__(
        self,
        traces_path: Path | None = None,
        work_dir: Path | None = None,
        *,
        config_path: Path | None = None,
        **kwargs,
    ) -> None:
        setup_i18n()
        super().__init__(**kwargs)
        from ..paths import default_work_dir, resolve_work_and_traces, traces_root_for_reload

        self.traces_path: Path | None
        if work_dir is not None:
            self.work_dir = Path(work_dir).expanduser().resolve()
            self.traces_path = (
                Path(traces_path).expanduser().resolve()
                if traces_path is not None
                else self.work_dir / "runs" / "traces"
            )
        elif traces_path is not None:
            self.work_dir, self.traces_path = resolve_work_and_traces(traces_path)
        else:
            self.work_dir = default_work_dir()
            self.traces_path = None
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.run_manager = RunManager(self.work_dir)
        self.run_manager.add_finished_listener(self._on_background_run_finished)
        self.run_manager.add_status_listener(self._on_background_run_status)
        self._run_status_timer: Timer | None = None
        self._live_sessions_timer: Timer | None = None
        self._live_sessions_busy = False
        self._live_sessions_last_scan: float = 0.0
        self._share_notified: set[str] = set()
        self._populate_busy = False
        self._exiting = False
        self._config_path = Path(config_path).expanduser() if config_path else None
        self._analysis_jobs_active: int = 0
        self._self_test_summary: str = ""
        self._meta_only: list[tuple[SessionMeta, str]] = []
        self._plugin_results: dict[str, dict[str, AnalysisResult]] = {}
        self._selected: set[str] = set()
        self._filter_model: str = ""
        self._delete_pending_paths: list[Path] | None = None
        self._delete_cursor_key: str | None = None
        self._delete_row_keys_snapshot: list[str] | None = None
        self._config: JsonObject = self._load_config()
        self._theme_persist = False
        early = str(self._config.get("theme") or "").strip()
        if early:
            try:
                self.theme = early
            except Exception:
                logger.debug(t("ui-failed-to-apply-saved-theme-r"), early)
        self._traces_root_for_reload = traces_root_for_reload

    def compose(self) -> ComposeResult:
        from .widgets.activity_bar import ActivityBar

        yield Header()
        yield ActivityBar()
        with Vertical():
            yield Static("", id="session-paths")
            yield Static("", id="session-summary")
            with Horizontal(id="session-filter-bar", classes=FILTER_BAR_CLASS):
                yield Static(U.filter_label(), classes=FILTER_LABEL_CLASS)
                yield Select(
                    [(U.all_models(), _SESSION_FILTER_ALL)],
                    value=_SESSION_FILTER_ALL,
                    id="session-model-select",
                    allow_blank=False,
                    classes=t("ui-field-select-session-filter-select"),
                )
            yield DataTable(id="session-table")
        yield Footer()

    def _session_traces_root(self) -> Path:
        """Traces directory fixed for this process (CLI / constructor only)."""
        if self.traces_path:
            return Path(self.traces_path).expanduser()
        return Path(self.work_dir).expanduser() / "runs" / "traces"

    def _update_session_paths_banner(self) -> None:
        """Read-only traces root (sessions live here; not the process cwd)."""
        try:
            banner = self.query_one("#session-paths", Static)
        except Exception:
            return
        traces = self._session_traces_root()
        # Fluent strips trailing spaces in labels — join with explicit separators.
        banner.update(f"[dim]Traces[/dim]  {traces}")

    def _load_config(self) -> JsonObject:
        """Load ``~/.groket/config.json`` (empty mapping when missing or invalid)."""
        fp = app_config_path()
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_config(self) -> None:
        """Merge in-memory keys into ``~/.groket/config.json`` and write.

        Re-reads the file so concurrent writers (prefs, analysis settings)
        are not clobbered by a partial in-memory snapshot.
        """
        fp = app_config_path()
        fp.parent.mkdir(parents=True, exist_ok=True)
        on_disk: JsonObject = {}
        if fp.is_file():
            try:
                loaded = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    on_disk = loaded
            except (OSError, json.JSONDecodeError):
                logger.debug(t("ui-failed-to-read-prefs-from-s"), fp, exc_info=True)
                on_disk = {}
        on_disk.update(self._config)
        self._config = on_disk
        text = json.dumps(on_disk, indent=2)
        if not text.endswith("\n"):
            text += "\n"
        try:
            fp.write_text(text, encoding="utf-8")
        except OSError:
            logger.warning(t("ui-failed-to-write-prefs-to-s"), fp, exc_info=True)

    def _theme_names(self) -> list[str]:
        reg = getattr(self, "available_themes", None) or {}
        try:
            return sorted(reg.keys())
        except Exception:
            return []

    def apply_saved_theme(self, *, save: bool = False) -> str | None:
        """Restore theme from config.json (or keep current). Re-applied after refresh.

        Textual can reset ``self.theme`` during App/mount; setting only once in
        ``on_mount`` is unreliable.
        """
        name = str(self._config.get("theme") or "").strip() or self.theme
        names = set(self._theme_names())
        if name not in names:
            if not names:
                return None
            name = self.theme if self.theme in names else next(iter(sorted(names)))
        try:
            self.theme = name
        except Exception:
            return None
        self._config["theme"] = name
        if save:
            self._save_config()
        return name

    def _enable_theme_persist(self) -> None:
        """Re-apply saved theme, then persist any later theme changes to disk.

        Covers Ctrl+P → Change theme and any other path that sets ``App.theme``.
        """
        self.apply_saved_theme(save=False)
        if not self._theme_persist:
            self._theme_persist = True
            self.theme_changed_signal.subscribe(self, self._on_theme_changed)

    def _on_theme_changed(self, theme: Theme) -> None:
        """Persist the active theme name when Textual applies a new theme."""
        if not self._theme_persist:
            return
        name = (theme.name or self.theme or "").strip()
        if not name or self._config.get("theme") == name:
            return
        self._config["theme"] = name
        self._save_config()

    def on_mount(self) -> None:
        try:
            from ..runs.personas import PersonaStore

            PersonaStore(self.work_dir).ensure_defaults()
        except Exception:
            logger.debug(t("ui-personastore-initialization-failed"), exc_info=True)
        try:
            from ..paths import analysis_cache_dir

            svc = AnalysisService(
                self.work_dir,
                traces=Path(self.traces_path) if self.traces_path else None,
                config_path=self._config_path,
                cache_root=analysis_cache_dir(),
            )
            set_analysis_service(svc)
            if svc.load_failures:
                self.notify(
                    f"{t('ui-failed-to-load')} {len(svc.load_failures)} {t('ui-plugin-s')}"
                    + ", ".join(svc.load_failures),
                    severity="error",
                    timeout=15,
                )
        except Exception:
            logger.warning(t("ui-analysis-service-initialization-failed"), exc_info=True)
        self.apply_saved_theme(save=False)
        self.call_after_refresh(self._enable_theme_persist)
        table = self.query_one("#session-table", DataTable)
        style_data_table(table)
        table.add_columns(
            " ",
            t("ui-session-id"),
            t("ui-model"),
            t("ui-task"),
            t("ui-title"),
            t("ui-turn"),
            t("ui-duration"),
            t("ui-events"),
            t("ui-findings-1"),
            t("ui-high-1"),
            t("ui-med"),
            t("ui-label"),
        )
        try:
            bits = [f"{t('ui-work-1')} {self.work_dir}", f"{t('ui-runs')} {self.work_dir / 'runs'}"]
            try:
                n_plugins = len([p for p in self._analysis_svc().list_plugins() if p.id != "noop"])
                bits.append(t("ui-plugins-count", n=n_plugins))
            except Exception:
                pass
            self.sub_title = "  ·  ".join(bits)
        except Exception:
            pass
        try:
            (self.work_dir / "runs" / "traces").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._update_session_paths_banner()
        load_p = self._session_traces_root()
        if load_p.exists():
            self._load_sessions(load_p)
        else:
            self.notify(
                f"{t('ui-traces-path-not-found-yet-runner-writes-to')} {self.work_dir / 'runs' / 'traces'}",
                severity="information",
                timeout=6,
            )
        table.focus()
        self._schedule_live_sessions_poll()

    _CACHE_FILE = META_CACHE_FILENAME

    def _load_meta_cache(self, root: Path) -> dict[str, dict]:
        """Load cached session metadata keyed by resolved session_dir path."""
        cache_file = self.work_dir / self._CACHE_FILE
        if not cache_file.exists():
            return {}
        try:
            data = json.loads(cache_file.read_text())
            if data.get("root") != str(root):
                return {}
            return data.get("sessions", {})
        except (json.JSONDecodeError, KeyError):
            return {}

    def _save_meta_cache(self, root: Path, entries: list[tuple[SessionMeta, str]]) -> None:
        """Write session metadata cache to disk."""
        sessions_cache: dict[str, dict[str, object]] = {}
        cache: dict[str, object] = {"root": str(root), "sessions": sessions_cache}
        for meta, label in entries:
            key = str(meta.session_dir.resolve())
            sessions_cache[key] = {
                "session_id": meta.session_id,
                "model_id": meta.model_id,
                "title": meta.title,
                "created_at": meta.created_at,
                "num_events": meta.num_events,
                "duration_seconds": meta.duration_seconds,
                "task_id": meta.task_id,
                "run_id": meta.run_id,
                "git_repo": meta.git_repo,
                "git_branch": meta.git_branch,
                "label": label,
            }
        try:
            (self.work_dir / self._CACHE_FILE).write_text(json.dumps(cache, indent=2))
        except Exception:
            pass

    def _load_sessions_sync(self, root: Path) -> int:
        """Load session metas into ``_meta_only`` (any thread; no UI calls).

        :returns: Number of sessions loaded (0 if none found — leaves prior list untouched).
        """
        session_dirs = find_sessions(root)
        if not session_dirs:
            if root.is_dir():
                for sub in sorted(root.iterdir()):
                    if sub.is_dir():
                        session_dirs.extend(find_sessions(sub))
        if not session_dirs:
            return 0
        self._meta_only = []
        self._plugin_results = {}
        seen_dirs: set[str] = set()
        unique_dirs: list[Path] = []
        for sd in session_dirs:
            resolved = str(sd.resolve())
            if resolved not in seen_dirs:
                seen_dirs.add(resolved)
                unique_dirs.append(sd)

        def _load_one(sd: Path) -> tuple[SessionMeta, str] | None:
            try:
                meta = load_session_meta(sd)
            except Exception:
                logger.debug(t("ui-failed-to-load-session-meta-for-s"), sd, exc_info=True)
                return None
            label = self._derive_label(sd, root)
            return (meta, label)

        with ThreadPoolExecutor(max_workers=META_LOAD_WORKERS) as pool:
            results = pool.map(_load_one, unique_dirs)
        for result in results:
            if result is not None:
                self._meta_only.append(result)
        self._save_meta_cache(root, self._meta_only)
        return len(self._meta_only)

    @work(thread=True)
    def _load_sessions(self, root: Path | None = None) -> None:
        if root is None:
            return
        call_ui(self, self.notify, f"{t('ui-scanning')} {root}...", severity="information")
        n = self._load_sessions_sync(root)
        if n == 0:
            call_ui(
                self,
                self.notify,
                f"{t('ui-no-sessions-found-in')} {root}",
                severity="error",
            )
            return
        call_ui(self, self._rebuild_session_filters)
        call_ui(self, self._populate_session_table)
        call_ui(
            self,
            self.notify,
            f"{t('ui-loaded')} {n} {t('ui-sessions-press-a-to-run-detectors')}",
            severity="information",
        )

    def _analysis_svc(self) -> AnalysisService:
        return get_analysis_service(self.work_dir)

    def _analyze_one(self, meta: SessionMeta, label: str) -> None:
        """Analyze a single session with all plugins. Must be called from a worker thread."""
        sd_key = str(meta.session_dir)
        if sd_key in self._plugin_results:
            return
        try:
            self._plugin_results[sd_key] = self._analysis_svc().analyze_all(meta.session_dir)
        except Exception as exc:
            logger.warning(t("ui-analysis-failed-for-s-s"), sd_key, exc)
            self._plugin_results[sd_key] = {}

    def action_self_test(self) -> None:
        """Open dependency self-test (Docker, Grok auth, work dir, …) on the UI thread."""
        from .widgets.self_test_modal import SelfTestModal

        self.push_screen(SelfTestModal(work_dir=self.work_dir))

    @work(thread=True)
    def _analyze_targets(self, targets: list[tuple[SessionMeta, str]] | None = None) -> None:
        """Analyze (meta, label) pairs on a worker thread; UI updates via call_ui."""
        if (
            not targets
            or isinstance(targets, (str, Path))
            or (not isinstance(targets, (list, tuple)))
        ):
            return
        pending: list[tuple[SessionMeta, str]] = []
        for item in targets:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            meta, label = item
            if str(meta.session_dir) not in self._plugin_results:
                pending.append((meta, str(label)))
        if not pending:
            call_ui(self, self.notify, t("ui-already-analyzed"), severity="information")
            return
        n_plugins = 0
        try:
            n_plugins = len([p for p in self._analysis_svc().list_plugins() if p.id != "noop"])
        except Exception:
            pass
        call_ui(
            self,
            self.notify,
            f"{t('ui-analyzing')} {len(pending)} {t('ui-sessions-1')} {n_plugins} {t('ui-plugins')}",
            severity="information",
        )
        self._analysis_jobs_active = max(0, int(self._analysis_jobs_active)) + len(pending)
        try:
            for idx, (meta, label) in enumerate(pending):
                try:
                    self._analyze_one(meta, label)
                finally:
                    self._analysis_jobs_active = max(0, self._analysis_jobs_active - 1)
                if (idx + 1) % 5 == 0 or idx == len(pending) - 1:
                    call_ui(self, self._populate_session_table)
        except Exception:
            self._analysis_jobs_active = 0
            raise
        call_ui(
            self,
            self.notify,
            f"{t('ui-analysis-complete')} {len(pending)} {t('ui-sessions-2')}",
            severity="information",
        )

    def _derive_label(self, session_dir: Path, root: Path) -> str:
        """Derive a display label from directory path."""
        try:
            rel = session_dir.relative_to(root)
            parts = list(rel.parts)
            meaningful = [p for p in parts if p != "sessions" and (not p.startswith("%"))]
            if meaningful:
                return "/".join(meaningful[:2])
        except ValueError:
            pass
        return session_dir.name[:20]

    def _session_model_options(self) -> list[tuple[str, str]]:
        models = sorted(
            {
                meta.model_display
                for meta, _ in self._meta_only
                if meta.model_id and meta.model_id != "unknown"
            }
        )
        return [(U.all_models(), _SESSION_FILTER_ALL), *[(m, m) for m in models]]

    @staticmethod
    def _select_value_to_filter(value: object) -> str:
        """Map Select value to internal filter (``all`` / blank → no filter)."""
        if value is Select.BLANK or value is None:
            return ""
        s = str(value)
        return "" if s == _SESSION_FILTER_ALL else s

    @staticmethod
    def _filter_to_select_value(filt: str) -> str:
        return filt if filt else _SESSION_FILTER_ALL

    def _rebuild_session_filters(self) -> None:
        """Refresh Model Select options from loaded session metadata."""
        model_opts = self._session_model_options()
        model_vals = {v for _, v in model_opts}
        model_sel_val = self._filter_to_select_value(self._filter_model)
        if model_sel_val not in model_vals:
            self._filter_model = ""
            model_sel_val = _SESSION_FILTER_ALL
        try:
            model_sel = self.query_one("#session-model-select", Select)
            model_sel.set_options(model_opts)
            model_sel.value = model_sel_val
        except Exception:
            logger.debug(t("ui-session-model-select-update-failed"), exc_info=True)

    def _set_session_filter_selects(self) -> None:
        """Push ``_filter_model`` into the Model Select (keyboard cycle)."""
        try:
            self.query_one("#session-model-select", Select).value = self._filter_to_select_value(
                self._filter_model
            )
        except Exception:
            pass

    @on(Select.Changed, "#session-model-select")
    def _on_session_model_filter(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        self._filter_model = self._select_value_to_filter(event.value)
        self._populate_session_table()

    @staticmethod
    def _cursor_key_after_deletes(
        row_keys_in_order: list[str], cursor_key: str | None, gone: set[str]
    ) -> str | None:
        """Pick a sensible post-delete cursor: next row, else previous, else first remaining."""
        if not row_keys_in_order:
            return None
        remaining = [k for k in row_keys_in_order if k not in gone]
        if not remaining:
            return None
        if cursor_key and cursor_key not in gone and (cursor_key in remaining):
            return cursor_key
        try:
            idx = row_keys_in_order.index(cursor_key) if cursor_key else 0
        except ValueError:
            idx = 0
        for k in row_keys_in_order[idx + 1 :]:
            if k not in gone:
                return k
        for k in reversed(row_keys_in_order[:idx]):
            if k not in gone:
                return k
        return remaining[0]

    def _session_row_keys_in_order(self, table: DataTable | None = None) -> list[str]:
        table = table or self.query_one("#session-table", DataTable)
        try:
            return [str(rk.value) for rk in table.rows.keys()]
        except Exception:
            return []

    @staticmethod
    def _session_sort_ts(meta: SessionMeta) -> float:
        """Best-effort epoch seconds for newest-first session ordering."""
        for raw in (meta.updated_at, meta.created_at):
            if not raw:
                continue
            try:
                s = str(raw).replace("Z", "+00:00")
                from datetime import datetime

                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.timestamp()
            except Exception:
                pass
        try:
            from ..parser import session_trace_mtime

            mt = session_trace_mtime(Path(meta.session_dir))
            if mt > 0:
                return mt
        except Exception:
            pass
        try:
            return Path(meta.session_dir).stat().st_mtime
        except OSError:
            return 0.0

    def _populate_session_table(self, *, restore_key: str | None = None) -> None:
        """Rebuild sessions table on the UI thread."""
        if self._populate_busy:
            return
        self._populate_busy = True
        try:
            self._populate_session_table_inner(restore_key=restore_key)
        finally:
            self._populate_busy = False

    def _populate_session_table_inner(self, *, restore_key: str | None = None) -> None:
        table = self.query_one("#session-table", DataTable)
        if restore_key is None:
            restore_key = self._session_row_key_at_cursor(table)
        table.clear()
        rows: list[tuple[SessionMeta, str, dict[str, AnalysisResult] | None]] = []
        for meta, label in self._meta_only:
            if self._filter_model and meta.model_display != self._filter_model:
                continue
            sd_key = str(meta.session_dir)
            results = self._plugin_results.get(sd_key)
            rows.append((meta, label, results))

        def sort_key(item):
            meta, _label, _results = item
            ts = self._session_sort_ts(meta)
            return (-ts, meta.model_display, meta.task_id or "", meta.session_id or "")

        rows.sort(key=sort_key)
        total_findings = 0
        total_high = 0
        analyzed_count = 0
        for meta, label, results in rows:
            sd_key = str(meta.session_dir)
            sel = Text("*", style="bold green") if sd_key in self._selected else Text(" ")
            finding_count: int | str
            if results is not None:
                analyzed_count += 1
                high = sum(r.high_count for r in results.values())
                med = sum(r.medium_count for r in results.values())
                finding_count = sum(r.finding_count for r in results.values())
                total_findings += int(finding_count)
                total_high += high
                high_text = Text(str(high), style="bold red") if high else Text("0")
                med_text = Text(str(med), style="yellow") if med else Text("0")
            else:
                finding_count = "--"
                high_text = Text("--", style="dim")
                med_text = Text("--", style="dim")
            task_text = Text(meta.task_id[:20], style="cyan") if meta.task_id else Text("")
            from .styles import status_rich_style

            status = meta.list_status_label()
            if status == "awaiting":
                turn_text = Text(t("status-waiting-prompt"), style=status_rich_style("awaiting"))
            elif status == "running":
                turn_text = Text(t("status-running"), style=status_rich_style("running"))
            elif status == "cancelled":
                turn_text = Text(t("status-cancelled"), style=status_rich_style("failed"))
            elif status == "complete":
                turn_text = Text(t("status-complete"), style=status_rich_style("completed"))
            else:
                turn_text = Text(
                    status if status != "—" else t("status-unknown"),
                    style=status_rich_style("idle"),
                )
            try:
                table.add_row(
                    sel,
                    meta.session_id[:20],
                    meta.model_display[:40],
                    task_text,
                    meta.label[:40],
                    turn_text,
                    meta.duration_str,
                    str(meta.num_events),
                    str(finding_count),
                    high_text,
                    med_text,
                    label,
                    key=sd_key,
                )
            except Exception:
                logger.debug(t("ui-failed-to-add-row-for-s"), sd_key, exc_info=True)
        if restore_key:
            self._restore_cursor(table, restore_key)
        pending = len(self._meta_only) - analyzed_count
        self._update_summary_lazy(
            len(self._meta_only), analyzed_count, total_findings, total_high, pending
        )
        focus_primary_list(table)
        with suppress(Exception):
            self.refresh_bindings()

    def _update_summary_lazy(
        self, total: int, analyzed: int, total_findings: int, total_high: int, pending: int
    ) -> None:
        sel_count = len(self._selected)
        sel_part = f"{t('ui-msg')} {sel_count} {t('ui-selected')}" if sel_count else ""
        scope = t("ui-report-uses-selected") if sel_count else ""
        if pending > 0 and analyzed > 0:
            progress = f"{t('ui-msg-1')} {pending} {t('ui-pending-analysis')}"
        elif pending > 0:
            progress = t("ui-a-analyze")
        else:
            progress = ""
        summary = f"[bold]{total} {t('ui-sessions')} {total_findings} {t('ui-findings')} {total_high} {t('ui-high')} {progress} {sel_part} {scope}"
        self.query_one("#session-summary", Static).update(summary)

    def _restore_cursor(self, table: DataTable, row_key_value: str) -> None:
        """Move cursor back to the row with the given key after a table repopulate."""
        restore_cursor(table, row_key_value)

    def _session_row_key_at_cursor(self, table: DataTable | None = None) -> str | None:
        """Stable row key for the highlighted session row (session_dir path)."""
        table = table or self.query_one("#session-table", DataTable)
        return cursor_row_key(table)

    def _set_session_sel_cell(self, table: DataTable, row_key: str, selected: bool) -> None:
        """Update only the selection marker column (avoids table.clear / cursor jump)."""
        from rich.text import Text

        mark = Text("*", style="bold green") if selected else Text(" ")
        try:
            cols = list(table.columns.keys())
            if not cols:
                return
            table.update_cell(row_key, cols[0], mark)
        except Exception:
            set_marker_column(table, row_key, selected, on="*", off=" ")

    def _refresh_session_selection_markers(self, table: DataTable | None = None) -> None:
        """Refresh all Sel cells from ``self._selected`` without rebuilding rows."""
        table = table or self.query_one("#session-table", DataTable)
        for rk in table.rows.keys():
            key = str(rk.value)
            self._set_session_sel_cell(table, key, key in self._selected)

    def action_cycle_model_filter(self) -> None:
        """Cycle model Select: all -> model1 -> … -> all (``m`` / command palette)."""
        models = [v for _, v in self._session_model_options() if v != _SESSION_FILTER_ALL]
        if not models:
            return
        if self._filter_model and self._filter_model in models:
            idx = models.index(self._filter_model)
            self._filter_model = models[idx + 1] if idx + 1 < len(models) else ""
        else:
            self._filter_model = models[0]
        self._set_session_filter_selects()
        self.notify(f"{t('ui-model-filter')} {self._filter_model or 'all'}")
        self._populate_session_table()

    def action_toggle_select(self) -> None:
        """Toggle selection on the current row (in-place; cursor stays put)."""
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if not cursor_key:
            return
        if cursor_key in self._selected:
            self._selected.discard(cursor_key)
            now_on = False
        else:
            self._selected.add(cursor_key)
            now_on = True
        self._set_session_sel_cell(table, cursor_key, now_on)
        self._refresh_selection_summary_only()
        with suppress(Exception):
            self.refresh_bindings()

    def action_select_all(self) -> None:
        """Select all or deselect all (in-place markers; no cursor jump)."""
        table = self.query_one("#session-table", DataTable)
        preserve = self._session_row_key_at_cursor(table)
        if self._selected:
            self._selected.clear()
        else:
            for meta, _ in self._meta_only:
                self._selected.add(str(meta.session_dir))
        self._refresh_session_selection_markers(table)
        if preserve:
            self._restore_cursor(table, preserve)
        self._refresh_selection_summary_only()
        with suppress(Exception):
            self.refresh_bindings()

    def _refresh_selection_summary_only(self) -> None:
        """Recompute summary counts from in-memory analysis (no table rebuild)."""
        total = len(self._meta_only)
        analyzed_count = 0
        total_findings = 0
        total_high = 0
        for meta, _ in self._meta_only:
            sd_key = str(meta.session_dir)
            results = self._plugin_results.get(sd_key)
            if results is None:
                continue
            analyzed_count += 1
            total_findings += sum(r.finding_count for r in results.values())
            total_high += sum(r.high_count for r in results.values())
        pending = max(0, total - analyzed_count)
        try:
            self._update_summary_lazy(total, analyzed_count, total_findings, total_high, pending)
        except Exception:
            pass

    def action_search_sessions(self) -> None:
        """Open fuzzy search modal over the session list."""
        if not self._meta_only:
            self.notify(U.load_sessions_first(), severity="warning")
            return
        self.push_screen(SessionSearchModal(self._meta_only), callback=self._on_search_result)

    def _on_search_result(self, result: str | None) -> None:
        """Handle a selected session from the search modal."""
        if result is None:
            return
        table = self.query_one("#session-table", DataTable)
        self._restore_cursor(table, result)
        self._open_session(result)

    def action_rerun_session(self) -> None:
        """Open the runner pre-filled with the current session's details."""
        table = self.query_one("#session-table", DataTable)
        if table.cursor_row is None:
            self.notify(U.select_session_first(), severity="warning")
            return
        try:
            row_key = list(table.rows.keys())[table.cursor_row]
            cursor_key = row_key.value
        except (IndexError, KeyError):
            return
        meta = None
        for m, _label in self._meta_only:
            if str(m.session_dir) == cursor_key:
                meta = m
                break
        if meta is None:
            self.notify(U.session_not_found(), severity="error")
            return
        self._do_rerun(meta)

    def _extract_session_launch_params(self, meta: SessionMeta) -> dict:
        """Extract launch parameters from a session's run.json and task catalog.

        Returns dict with keys: prompt, setup_instructions, docker_image,
        repo_url, repo_branch, models.
        """
        from ..constants import DEFAULT_DOCKER_IMAGE, DEFAULT_MODEL_ID
        from ..paths import RUN_PREFIX as RUN_DIR_PREFIX

        prompt = extract_prompt(meta.session_dir)
        setup = ""
        docker_image = DEFAULT_DOCKER_IMAGE
        repo_url = meta.git_repo
        repo_branch = meta.git_branch
        run_json = meta.session_dir / "run.json"
        if not run_json.exists():
            parent = meta.session_dir.parent
            if parent.name.startswith(RUN_DIR_PREFIX):
                run_json = parent / "run.json"
        if run_json.exists():
            try:
                run_data = json.loads(run_json.read_text())
                repo_url = repo_url or run_data.get("repo_url", "")
                repo_branch = repo_branch or run_data.get("repo_branch", "")
                setup = run_data.get("setup_instructions", "")
                docker_image = run_data.get("docker_image", docker_image)
            except (json.JSONDecodeError, KeyError):
                pass
        if not repo_url:
            trace_dir = meta.session_dir
            for parent in [meta.session_dir] + list(meta.session_dir.parents):
                if parent.name.startswith(RUN_DIR_PREFIX):
                    trace_dir = parent
                    break
            task_id, _ = self._extract_task_and_model(trace_dir.name)
            try:
                tasks: list = []
                task_map = {t.task_id: t for t in tasks}
                if task_id in task_map:
                    task = task_map[task_id]
                    repo_url = task.repo_url
                    repo_branch = task.repo_branch
                    setup = setup or task.setup_instructions
                    docker_image = task.docker_image
            except Exception:
                logger.debug(t("ui-task-catalog-lookup-failed-for-s"), task_id, exc_info=True)
        models = [meta.model_id] if meta.model_id and meta.model_id != DEFAULT_MODEL_ID else []
        return {
            "prompt": prompt,
            "setup_instructions": setup,
            "docker_image": docker_image,
            "repo_url": repo_url,
            "repo_branch": repo_branch,
            "models": models,
        }

    @work(thread=True)
    def _do_rerun(self, meta: SessionMeta | None = None) -> None:
        """Extract session details and open runner with prefill."""
        if not isinstance(meta, SessionMeta):
            return
        params = self._extract_session_launch_params(meta)
        prefill = RunnerPrefill(**params)
        call_ui(self, self._push_runner_with_prefill, prefill)

    def action_save_session_config(self) -> None:
        """Save the highlighted (or first selected) session as a reusable run config."""
        meta = None
        if self._selected:
            key = next(iter(self._selected))
            for m, _ in self._meta_only:
                if str(m.session_dir) == key:
                    meta = m
                    break
        if meta is None:
            table = self.query_one("#session-table", DataTable)
            if table.row_count == 0:
                self.notify(U.no_session_to_save(), severity="warning")
                return
            try:
                row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                path = str(row_key.value) if row_key else ""
            except Exception:
                path = ""
            for m, _ in self._meta_only:
                if str(m.session_dir) == path:
                    meta = m
                    break
        if meta is None:
            self.notify(U.session_not_found(), severity="error")
            return
        self._do_save_session_config(meta)

    @work(thread=True)
    def _do_save_session_config(self, meta: SessionMeta | None = None) -> None:
        if not isinstance(meta, SessionMeta):
            return
        from ..runs.run_configs import RunConfigStore

        params = self._extract_session_launch_params(meta)
        try:
            store = RunConfigStore(self.work_dir)
            cfg = store.from_session_fields(
                prompt=params["prompt"] or t("ui-no-prompt-extracted"),
                setup_instructions=params["setup_instructions"],
                docker_image=params["docker_image"],
                repo_url=params["repo_url"],
                repo_branch=params["repo_branch"],
                models=params["models"],
                session_id=meta.session_id,
                session_dir=str(meta.session_dir),
                name=meta.task_id or meta.label or meta.session_id[:12],
            )
            call_ui(
                self,
                self.notify,
                f"{t('ui-saved-run-config')} {cfg.config_id} ({cfg.display_name()} {t('ui-open-with-c-configs-sessions-unchanged')}",
                severity="information",
                timeout=10,
            )
        except Exception as exc:
            call_ui(self, self.notify, f"{t('ui-save-config-failed')} {exc}", severity="error")

    def _toast(
        self,
        message: str,
        *,
        severity: str = "information",
        timeout: float = 2.0,
        replace: bool = True,
    ) -> None:
        """Short status toast (optionally clearing prior notifications)."""
        from typing import Literal, cast

        sev = cast(Literal["information", "warning", "error"], severity)

        def _show() -> None:
            if replace:
                with suppress(Exception):
                    self.clear_notifications()
            self.notify(message, severity=sev, timeout=timeout)

        call_ui(self, _show)

    def _session_action_targets(self) -> list[Path]:
        """Selected session dirs, or the cursor row if nothing is selected."""
        if self._selected:
            return [Path(p) for p in self._selected]
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if cursor_key:
            return [Path(cursor_key)]
        return []

    def _refresh_session_meta_rows(self, paths: list[Path]) -> None:
        """Reload meta for *paths* and repaint the session table."""
        if not paths:
            return
        want = {str(p) for p in paths}
        updated: list[tuple[SessionMeta, str]] = []
        for meta, label in self._meta_only:
            key = str(meta.session_dir)
            if key in want:
                try:
                    reloaded = load_session_meta(meta.session_dir)
                    if reloaded is not None:
                        meta = reloaded
                except Exception:
                    logger.debug(t("ui-reload-meta-failed-for-s"), key, exc_info=True)
            updated.append((meta, label))
        self._meta_only = updated
        with suppress(Exception):
            self._populate_session_table()

    def _awaiting_session_targets(self) -> list[Path]:
        """Subset of action targets that are awaiting a follow-up."""
        from ..session.turn_gate import session_awaits_follow_up

        targets = self._session_action_targets()
        if not targets:
            return []
        by_path = {str(m.session_dir): m for m, _ in self._meta_only}
        out: list[Path] = []
        for path in targets:
            meta = by_path.get(str(path))
            if meta is not None and meta.turn_in_progress:
                out.append(path)
                continue
            try:
                if session_awaits_follow_up(path):
                    out.append(path)
            except Exception:
                logger.debug(t("ui-awaiting-check-failed-for-s"), path, exc_info=True)
        return out

    def _apply_follow_up_to_paths(
        self, paths: list[Path], prompt: str, *, final: bool = False
    ) -> int:
        from ..session.turn_gate import write_follow_up_for_session

        errors = 0
        for path in paths:
            try:
                how = write_follow_up_for_session(path, prompt, final=final)
            except Exception:
                errors += 1
                logger.debug(t("ui-follow-up-failed-for-s"), path, exc_info=True)
                continue
            try:
                meta = next(
                    (m for m, _ in self._meta_only if str(m.session_dir) == str(path)), None
                )
                rid = (meta.run_id if meta else "") or ""
                rm = self.run_manager
                if how == "sent" and rid and hasattr(rm, "submit_follow_up"):
                    rm.submit_follow_up(prompt, run_id=rid, final=final)
            except Exception:
                pass
        return errors

    def _apply_done_to_paths(self, paths: list[Path]) -> int:
        from ..session.turn_gate import write_done_for_session

        errors = 0
        for path in paths:
            try:
                write_done_for_session(path)
            except Exception:
                errors += 1
                logger.debug(t("ui-mark-done-failed-for-s"), path, exc_info=True)
            try:
                meta = next(
                    (m for m, _ in self._meta_only if str(m.session_dir) == str(path)), None
                )
                rid = (meta.run_id if meta else "") or ""
                rm = self.run_manager
                if rid and hasattr(rm, "complete_interactive"):
                    rm.complete_interactive(rid)
            except Exception:
                pass
        return errors

    def _awaiting_targets_or_toast(self) -> list[Path]:
        targets = self._awaiting_session_targets()
        if targets:
            return targets
        if not self._session_action_targets():
            self._toast(U.select_session_first(), severity="warning", timeout=2.0)
        else:
            self._toast(U.no_awaiting_sessions(), severity="warning", timeout=2.5)
        return []

    def _sessions_home_active(self) -> bool:
        """True when the sessions list screen is on top (not a pushed screen/modal)."""
        try:
            return self.screen is self.screen_stack[0]
        except Exception:
            return True

    def _runner_active(self) -> bool:
        """True when the evaluation runner form is the top screen."""
        from .screens.runner import RunnerScreen

        return isinstance(self.screen, RunnerScreen)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Gate session-home bindings so they do not leak into pushed-screen footers.

        ``n`` / ``e`` also require an awaiting multi-turn target on the home list.
        Priority launch (Ctrl+Enter / Ctrl+J) only while the runner is open.
        """
        if action == "launch_from_runner":
            return self._runner_active()
        if action in SESSION_HOME_ACTIONS and not self._sessions_home_active():
            return False
        if action in ("follow_up_sessions", "mark_sessions_done"):
            return bool(self._awaiting_session_targets())
        return True

    def action_launch_from_runner(self) -> None:
        """Priority hotkey: launch eval when Runner is the active screen."""
        from .screens.runner import RunnerScreen

        screen = self.screen
        if isinstance(screen, RunnerScreen):
            screen.action_run_evaluation()

    def action_mark_sessions_done(self) -> None:
        """``e`` — end awaiting sessions (mark done)."""
        targets = self._awaiting_targets_or_toast()
        if not targets:
            return
        errors = self._apply_done_to_paths(targets)
        self._refresh_session_meta_rows(targets)
        self.refresh_bindings()
        if errors:
            self._toast(
                f"{t('ui-failed-for')} {errors}/{len(targets)}", severity="warning", timeout=3.0
            )

    def action_follow_up_sessions(self) -> None:
        """``n`` — next prompt for awaiting selection."""
        targets = self._awaiting_targets_or_toast()
        if not targets:
            return

        def _apply(result: tuple[str, bool] | None) -> None:
            if not result:
                return
            prompt, final = result
            errors = self._apply_follow_up_to_paths(targets, prompt, final=final)
            self._refresh_session_meta_rows(targets)
            self.refresh_bindings()
            if errors:
                self._toast(
                    f"{t('ui-failed-for')} {errors}/{len(targets)}", severity="warning", timeout=3.0
                )
            elif final:
                self._toast(t("follow-up-sent-final"), severity="information", timeout=2.5)

        self.push_screen(InteractiveSessionsModal(n_awaiting=len(targets)), _apply)

    def action_delete_sessions(self) -> None:
        """Delete selected sessions (or current row if none selected). Removes traces only."""
        targets: list[Path] = []
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if self._selected:
            targets = [Path(p) for p in self._selected]
        elif cursor_key:
            targets = [Path(cursor_key)]
        if not targets:
            self.notify(
                t("ui-select-sessions-with-s-or-highlight-a-row-then-p"), severity="warning"
            )
            return
        from .delete_confirm import second_press_armed

        n = len(targets)
        commit, pending = second_press_armed(
            [str(p) for p in (self._delete_pending_paths or [])],
            [str(p) for p in targets],
        )
        if not commit:
            self._delete_pending_paths = [Path(p) for p in pending]
            self._delete_cursor_key = cursor_key
            self._delete_row_keys_snapshot = self._session_row_keys_in_order(table)
            self.notify(
                f"{t('ui-press-again-to-delete')} {n} {t('ui-session-s-from-disk-traces-feedback-cache-run-co')}",
                severity="warning",
                timeout=10,
            )
            return
        gone_preview = {str(p) for p in targets}
        snap = self._delete_row_keys_snapshot or self._session_row_keys_in_order(table)
        cur = self._delete_cursor_key or cursor_key
        restore_key = self._cursor_key_after_deletes(snap, cur, gone_preview)
        self._delete_pending_paths = None
        self._delete_cursor_key = None
        self._delete_row_keys_snapshot = None
        self._do_delete_sessions(targets, restore_key=restore_key)

    @work(thread=True)
    def _do_delete_sessions(
        self, targets: list[Path] | None = None, *, restore_key: str | None = None
    ) -> None:
        if not targets:
            return
        from ..runs.run_configs import delete_session_dirs, session_dirs_for_delete

        paths = session_dirs_for_delete(targets)
        stats = delete_session_dirs(paths, traces_root=self.traces_path, prune_empty_parents=True)
        gone = {str(p) for p in paths}

        def _refresh() -> None:
            self._selected -= gone
            self._meta_only = [
                (m, lab) for m, lab in self._meta_only if str(m.session_dir) not in gone
            ]
            for key in list(self._plugin_results.keys()):
                if key in gone:
                    del self._plugin_results[key]
            try:
                self._populate_session_table(restore_key=restore_key)
            except Exception:
                pass
            errors_raw = stats.get("errors")
            errors_list = list(errors_raw) if isinstance(errors_raw, list) else []
            err_n = len(errors_list)
            err_hint = ""
            if err_n:
                sample = str(errors_list[0]) if errors_list else ""
                err_hint = f" — {sample[:120]}"
            self.notify(
                f"{t('ui-deleted')} {stats['deleted']}/{stats['requested']} {t('ui-session-s')}"
                + (f"{t('ui-errors')} {err_n} {err_hint}" if err_n else ""),
                severity="warning" if err_n else "information",
                timeout=12,
            )

        call_ui(self, _refresh)

    def action_open_run_configs(self) -> None:
        """Browse reusable run configs (launch again with new models)."""
        self.push_screen(RunConfigsScreen(self.work_dir, run_manager=self.run_manager))

    def _findings_for_session(self, sd_key: str) -> list[Finding]:
        """All findings across all plugins for a session."""
        results = self._plugin_results.get(sd_key, {})
        out: list[Finding] = []
        for r in results.values():
            out.extend(r.findings)
        return out

    def action_refresh_everything(self) -> None:
        """Full refresh: rescan + run all analysis plugins."""
        from ..paths import traces_root_for_reload

        traces = traces_root_for_reload(self.work_dir, self.traces_path)
        runner_traces = self.work_dir / "runs" / "traces"
        root = runner_traces if runner_traces.exists() else traces
        if not root.exists():
            self.notify(f"{t('ui-no-traces-dir-to-refresh')} {root}", severity="error")
            return
        self._meta_only = []
        self._plugin_results = {}
        self._selected = set()
        try:
            cf = self.work_dir / self._CACHE_FILE
            if cf.exists():
                cf.unlink()
        except OSError:
            pass
        self.notify(
            f"{t('ui-full-refresh-from')} {root} {t('ui-background')}",
            severity="warning",
            timeout=12,
        )
        self._run_refresh_everything(root)

    @work(thread=True)
    def _run_refresh_everything(self, traces_root: Path | None = None) -> None:
        if traces_root is None:
            return
        summary: dict = {"sessions_loaded": 0, "analysis_ok": 0, "analysis_err": 0, "error": ""}
        try:
            # Sync load — do not nest @work _load_sessions (would not run inline).
            summary["sessions_loaded"] = self._load_sessions_sync(traces_root)
            targets = list(self._meta_only)
            for meta, label in targets:
                self._analyze_one(meta, label)
                sd_key = str(meta.session_dir)
                results = self._plugin_results.get(sd_key, {})
                if all(r.ok for r in results.values()):
                    summary["analysis_ok"] += 1
                else:
                    summary["analysis_err"] += 1
            call_ui(self, self._populate_session_table)
        except Exception as exc:
            summary["error"] = str(exc)

        def _done() -> None:
            try:
                self.traces_path = traces_root
                self._update_session_paths_banner()
            except Exception:
                pass
            try:
                self._populate_session_table()
            except Exception:
                pass
            if summary.get("error"):
                self.notify(
                    f"{t('ui-refresh-all-failed')} {summary['error']}", severity="error", timeout=15
                )
                return
            err_n = int(summary.get("analysis_err") or 0)
            self.notify(
                f"{t('ui-refresh-done-sessions')} {summary.get('sessions_loaded', 0)} {t('ui-analyzed')} {summary.get('analysis_ok', 0)} {t('ui-errs')} {err_n}",
                severity="warning" if err_n else "information",
                timeout=16,
            )

        call_ui(self, _done)

    def action_analyze(self) -> None:
        """Run configured session analyzer on selected sessions (or all if none selected)."""
        if not self._meta_only:
            self.notify(U.load_sessions_first(), severity="warning")
            return
        if self._selected:
            targets = [
                (meta, label)
                for meta, label in self._meta_only
                if str(meta.session_dir) in self._selected
            ]
        else:
            targets = list(self._meta_only)
        self._analyze_targets(targets)

    @staticmethod
    def _extract_task_and_model(trace_dir_name: str) -> tuple[str, str]:
        """Extract (task_id, model_suffix) from a trace directory name.

        Convention: groket-{run_id}-{model_suffix}. The model_suffix is only used
        as a fallback for grouping when the full model_id is unavailable.
        """
        from ..paths import strip_run_prefix

        name = strip_run_prefix(trace_dir_name)
        for suffix in ("-build", "-s80", "-s140"):
            if name.endswith(suffix):
                return (name[: -len(suffix)], suffix[1:])
        if "-" in name:
            parts = name.rsplit("-", 1)
            return (parts[0], parts[1])
        return (trace_dir_name, "unknown")

    @on(DataTable.RowHighlighted, "#session-table")
    def _on_session_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Refresh footer ``n`` / ``e`` when the cursor moves."""
        _ = event
        with suppress(Exception):
            self.refresh_bindings()

    @on(DataTable.RowSelected, "#session-table")
    def _on_session_selected(self, event: DataTable.RowSelected) -> None:
        row_key = str(event.row_key.value)
        self._open_session(row_key)

    def open_session_path(self, session_dir: Path | str, *, live: bool | None = None) -> None:
        """Open a session path in the trace browser (main list, Jobs modal, etc.)."""
        self._open_session(str(session_dir), live=live)

    def _open_session(self, row_key: str, live: bool | None = None) -> None:
        """Open a session in the browser immediately.

        Analysis runs inside BrowserScreen._load_data on its own worker
        so the screen appears without delay.
        """
        plugin_results = self._plugin_results.get(row_key)
        self._push_browser(Path(row_key), plugin_results)

    def _push_runner_with_prefill(self, prefill: RunnerPrefill) -> None:
        """Construct and push RunnerScreen on the main thread."""
        self.push_screen(RunnerScreen(self.work_dir, run_manager=self.run_manager, prefill=prefill))

    def _push_browser(
        self, session_path: Path, plugin_results: dict[str, AnalysisResult] | None
    ) -> None:
        """Construct and push BrowserScreen on the main thread."""
        self.push_screen(BrowserScreen(session_path, plugin_results))

    def action_open_runner(self) -> None:
        self.push_screen(RunnerScreen(self.work_dir, run_manager=self.run_manager))

    def action_open_personas(self) -> None:
        """Persona builder: create/edit/delete personas under runs/personas/."""
        from .screens.personas import PersonasScreen

        self.push_screen(PersonasScreen(self.work_dir))

    def action_open_jobs(self) -> None:
        """Open background jobs + container logs modal (runner stays quiet by default)."""
        from .screens.jobs import JobsModal

        self.push_screen(JobsModal(self.run_manager, work_dir=self.work_dir))

    def _run_manager_batch_ids(self) -> list[str]:
        return self.run_manager.active_batch_ids

    def _subtitle_run_status(self) -> None:
        """Alias used by run-config screens to refresh the header run badge."""
        self.update_run_status()

    def update_run_status(self) -> None:
        """Reflect background eval status in the app title when possible."""
        try:
            n = self.run_manager.active_count
            batches = self._run_manager_batch_ids()
            if batches:
                self.title = (
                    f"{t('ui-groket-batch')} {batches[0][:12]}… · {n} {t('ui-run-s-j-jobs')}"
                )
            elif n:
                cur = self.run_manager.latest()
                rid = cur.run_id if cur else "?"
                self.title = f"{t('ui-groket')} {n} {t('ui-run-s-latest')} {rid} {t('ui-j-jobs')}"
            else:
                self.title = "groket"
        except Exception:
            logger.debug(t("ui-failed-to-update-title-bar"), exc_info=True)

    def _schedule_run_status_update(self) -> None:
        """Debounce title updates (batch runs finish containers rapidly)."""
        if self._run_status_timer is not None:
            self._run_status_timer.stop()
        self._run_status_timer = self.set_timer(0.6, self.update_run_status)

    def _runner_traces_root(self) -> Path:
        """Host path where eval containers write sessions (always shareable mid-run)."""
        return self.work_dir / "runs" / "traces"

    def _schedule_live_sessions_poll(self) -> None:
        """Re-arm periodic live-session scan (runs while TUI is open)."""
        try:
            if self._live_sessions_timer is not None:
                try:
                    self._live_sessions_timer.stop()
                except Exception:
                    pass
            from ..constants import LIVE_POLL_ACTIVE_INTERVAL, LIVE_POLL_IDLE_INTERVAL

            interval = (
                LIVE_POLL_ACTIVE_INTERVAL
                if self.run_manager.active_count
                else LIVE_POLL_IDLE_INTERVAL
            )
            self._live_sessions_timer = self.set_timer(interval, self._live_sessions_tick)
        except Exception:
            pass

    def _live_sessions_tick(self) -> None:
        """Timer callback: merge any new/updated live sessions into the table."""
        try:
            self._scan_live_sessions_into_table()
        except Exception:
            pass
        finally:
            self._schedule_live_sessions_poll()

    def _on_background_run_status(self, status) -> None:
        """Worker-thread status callback: session_dir may appear mid-run."""
        if self._exiting or self.run_manager.ui_detached:
            return
        try:
            if getattr(status, "session_dir", None) is None:
                return
            if not self.is_running:
                return
            # post_message is thread-safe; call_from_thread raises if already on the app thread
            # (e.g. quit/cancel races).
            self.post_message(self._BgStatus(status))
        except Exception:
            pass

    def on_trace_eval_app__bg_status(self, event: _BgStatus) -> None:
        if self._exiting or self.run_manager.ui_detached:
            return
        with suppress(Exception):
            self._on_live_session_discovered(event.status)

    def _on_live_session_discovered(self, status) -> None:
        """UI-thread: ensure a mid-run session is in the sessions list."""
        self._schedule_run_status_update()
        sd = getattr(status, "session_dir", None)
        if sd is None:
            return
        try:
            sd_path = Path(sd)
        except Exception:
            return
        if not sd_path.is_dir():
            return
        runner_traces = self._runner_traces_root()
        try:
            if not self.traces_path or not Path(self.traces_path).exists():
                self.traces_path = runner_traces
                self._update_session_paths_banner()
        except Exception:
            pass
        try:
            self._merge_session_dirs([sd_path], traces_root=runner_traces)
        except Exception:
            pass
        try:
            self._request_live_share(sd_path, status=status)
        except Exception:
            pass

    def _request_live_share(self, session_dir: Path, *, status=None, force: bool = False) -> None:
        """Re-read groket-share.json (written in-container via ``grok share`` only)."""
        from ..runs.live_share import get_share_url, refresh_share_from_disk

        sd = Path(session_dir)
        _ = force
        url = refresh_share_from_disk(sd) or get_share_url(sd)
        if not url:
            return
        if status is not None:
            try:
                status.share_url = url
            except Exception:
                pass
        self._maybe_notify_share_url(sd, url)

    def _maybe_notify_share_url(self, session_dir: Path, share_url: str) -> None:
        """Share updates are normal workflow (Jobs/Browser/s key); no toast spam."""
        _ = (session_dir, share_url)
        return

    def _scan_live_sessions_into_table(self) -> None:
        """Scan runner traces + active container peeks; merge new/updated sessions."""
        if self._live_sessions_busy:
            return
        import time

        now = time.time()
        if now - self._live_sessions_last_scan < 2.0 and (not self.run_manager.active_count):
            return
        self._live_sessions_last_scan = now
        self._live_sessions_busy = True
        try:
            runner_traces = self._runner_traces_root()
            if not runner_traces.exists():
                return
            if self.run_manager.active_count:
                try:
                    if self.traces_path is None or not Path(self.traces_path).exists():
                        self.traces_path = runner_traces
                        self._update_session_paths_banner()
                except Exception:
                    pass
            try:
                viewing = Path(self.traces_path) if self.traces_path else runner_traces
            except Exception:
                viewing = runner_traces
            try:
                if viewing.resolve() != runner_traces.resolve() and self._meta_only:
                    if not self.run_manager.active_count:
                        return
            except Exception:
                pass
            found: list[Path] = []
            try:
                found.extend(find_sessions(runner_traces))
            except Exception:
                pass
            try:
                orch = self.run_manager.orchestrator
                for bg in self.run_manager.list_active():
                    for cfg in bg.configs:
                        try:
                            sd = orch.peek_session_dir(cfg.container_name)
                        except Exception:
                            sd = None
                        if sd is not None:
                            found.append(sd)
                        try:
                            st = bg.statuses.get(cfg.container_name)
                            if st is not None and st.session_dir is None and (sd is not None):
                                st.session_dir = sd
                        except Exception:
                            pass
            except Exception:
                pass
            if found:
                self._merge_session_dirs(found, traces_root=runner_traces)
                try:
                    seen: set[str] = set()
                    for sd in found:
                        try:
                            k = str(Path(sd).resolve())
                        except Exception:
                            k = str(sd)
                        if k in seen:
                            continue
                        seen.add(k)
                        self._request_live_share(Path(sd))
                except Exception:
                    pass
        finally:
            self._live_sessions_busy = False

    def _merge_session_dirs(
        self, session_dirs: list[Path], *, traces_root: Path | None = None
    ) -> None:
        """Add or refresh session metas without a full table rebuild storm."""
        if not session_dirs:
            return
        root = traces_root or self._runner_traces_root()
        existing: dict[str, int] = {}
        for idx, (meta, _label) in enumerate(self._meta_only):
            try:
                existing[str(Path(meta.session_dir).resolve())] = idx
            except Exception:
                existing[str(meta.session_dir)] = idx
        added = 0
        updated = 0
        for sd in session_dirs:
            try:
                sd_res = sd if sd.is_absolute() else root / sd
                if not sd_res.is_dir():
                    continue
                key = str(sd_res.resolve())
            except Exception:
                continue
            try:
                meta = load_session_meta(sd_res)
            except Exception:
                continue
            label = self._derive_label(sd_res, root)
            if key in existing:
                idx = existing[key]
                old_meta, _old_label = self._meta_only[idx]
                if (
                    old_meta.turn_outcome != meta.turn_outcome
                    or old_meta.num_events != meta.num_events
                    or old_meta.updated_at != meta.updated_at
                    or (old_meta.title != meta.title)
                ):
                    self._meta_only[idx] = (meta, label)
                    updated += 1
            else:
                self._meta_only.append((meta, label))
                existing[key] = len(self._meta_only) - 1
                added += 1
        if added or updated:
            try:
                self._populate_session_table()
            except Exception:
                pass

    def _on_background_run_finished(self, run: BackgroundRun) -> None:
        """Notify from worker thread when a backgrounded eval completes."""
        if self._exiting or self.run_manager.ui_detached:
            return
        try:
            if not self.is_running:
                return
            self.post_message(self._BgFinished(run))
        except Exception:
            pass

    def on_trace_eval_app__bg_finished(self, event: _BgFinished) -> None:
        if self._exiting or self.run_manager.ui_detached:
            return
        with suppress(Exception):
            self._notify_run_finished(event.run)

    def _prepare_clean_exit(self) -> None:
        """Detach UI from background jobs so ``q`` returns promptly.

        Docker containers and daemon worker threads keep running under dockerd
        (interactive sessions stay resumable on reopen). We only stop timers and
        UI callbacks that would block Textual shutdown via ``call_from_thread``.
        """
        self._exiting = True
        for attr in ("_run_status_timer", "_live_sessions_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            for screen in list(self.screen_stack):
                stop = getattr(screen, "_stop_live_refresh", None)
                if callable(stop):
                    stop()
        except Exception:
            logger.debug(t("ui-stop-live-refresh-on-quit-failed"), exc_info=True)
        try:
            self.run_manager.detach_ui()
        except Exception:
            logger.debug(t("ui-detach-ui-on-quit-failed"), exc_info=True)
        try:
            workers_cancel = getattr(self, "workers", None)
            if workers_cancel is not None and hasattr(workers_cancel, "cancel_all"):
                workers_cancel.cancel_all()
        except Exception:
            logger.debug(t("ui-workers-cancel-on-quit-failed"), exc_info=True)

    async def action_quit(self) -> None:
        """Quit the TUI without waiting for in-flight eval containers."""
        self._prepare_clean_exit()
        self.exit()

    def _notify_run_finished(self, run: BackgroundRun) -> None:
        from ..utils import fmt_duration

        self._schedule_run_status_update()
        try:
            self._scan_live_sessions_into_table()
        except Exception:
            pass
        if run.quiet or run.batch_id:
            return
        if self.run_manager.batch_active:
            return
        if self._run_manager_batch_ids():
            return
        elapsed = fmt_duration(run.elapsed_s)
        if run.error:
            self.notify(
                f"{t('ui-run')} {run.run_id} {t('ui-failed-after')} {elapsed}: {run.error[:120]}",
                severity="error",
                timeout=12,
            )
            return
        completed = sum(1 for r in run.results if r.status == "completed")
        failed = sum(1 for r in run.results if r.status == "failed")
        total = len(run.results) or len(run.configs)
        if failed:
            self.notify(
                f"{t('ui-run')} {run.run_id} {t('ui-finished-in')} {elapsed}: {completed}/{total} {t('ui-ok')} {failed} {t('ui-failed')}",
                severity="error",
                timeout=12,
            )
        from ..paths import traces_root_for_reload

        traces = traces_root_for_reload(self.work_dir, self.traces_path)
        runner_traces = self.work_dir / "runs" / "traces"
        for candidate in (runner_traces, traces):
            if candidate.exists():
                try:
                    self._load_sessions(candidate)
                    self.traces_path = candidate
                    self._update_session_paths_banner()
                    break
                except Exception:
                    pass

    def action_open_rules(self) -> None:
        self.push_screen(RulesScreen())

    def action_analysis_settings(self) -> None:
        """Open modal to configure session/feedback analyzer plugins."""

        def _done(saved: bool | None) -> None:
            if saved:
                try:
                    svc = self._analysis_svc()
                    n = len([p for p in svc.list_plugins() if p.id != "noop"])
                    self.notify(
                        f"{t('ui-analysis')} {n} {t('ui-plugin-s-1')}",
                        severity="information",
                        timeout=8,
                    )
                except Exception:
                    self.notify(U.analysis_settings_saved(), severity="information")

        self.push_screen(AnalysisSettingsModal(self.work_dir), _done)

    def action_toggle_tips(self) -> None:
        """Show/hide framed admonitions **app-wide** (``show_tips`` in config.json).

        Invoked from the command palette (Ctrl+P → Toggle tips / callouts) or
        Analysis settings — not a Footer key binding.
        """
        from .prefs import set_show_tips, show_tips_enabled, toggle_show_tips

        on = toggle_show_tips()
        self._config["show_tips"] = on
        set_show_tips(on)
        assert show_tips_enabled() is on
        self.notify(
            t("ui-tips-callouts-on") if on else t("ui-tips-callouts-off-hidden"),
            severity="information",
        )
        self._refresh_all_tip_surfaces()

    def _refresh_all_tip_surfaces(self) -> None:
        """Re-render every TipSurface by widget class (no hardcoded IDs / screen hooks)."""
        from .panel_render import TipSurface, refresh_tip_surfaces_in

        screens: list = []
        try:
            stack = getattr(self, "screen_stack", None)
            if stack is not None:
                screens.extend(list(stack))
        except Exception:
            pass
        try:
            if self.screen is not None and self.screen not in screens:
                screens.append(self.screen)
        except Exception:
            pass
        for scr in screens:
            if scr is None:
                continue
            fn = getattr(scr, "refresh_tip_surfaces", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    logger.debug(t("ui-refresh-tip-surfaces-failed-on-s"), scr, exc_info=True)
        try:
            n = 0
            for tip in self.query(TipSurface):
                tip.refresh_tip()
                n += 1
            if n == 0:
                refresh_tip_surfaces_in(self)
        except Exception:
            try:
                refresh_tip_surfaces_in(self)
            except Exception:
                pass

    def action_refresh_context(self) -> None:
        """Refresh whatever screen/context is active (F5 / Ctrl+R globally)."""
        from .screens.browser import BrowserScreen
        from .screens.personas import PersonasScreen
        from .screens.rules import RulesScreen
        from .screens.run_configs import RunConfigsScreen
        from .screens.runner import RunnerScreen

        screen = self.screen
        if isinstance(screen, BrowserScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, RunConfigsScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, PersonasScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, RunnerScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, RulesScreen):
            screen.action_refresh_context()
            return
        self._refresh_sessions_list()

    def _refresh_sessions_list(self) -> None:
        """Reload the sessions table from the fixed traces root."""
        root = self._session_traces_root()
        if not root.exists():
            self.notify(f"{t('ui-nothing-to-refresh')} {root}", severity="warning")
            return
        self._update_session_paths_banner()
        self.notify(
            f"{t('ui-refreshing-sessions-from')} {root}…", severity="information", timeout=4
        )
        self._load_sessions(root)
        try:
            self._populate_session_table()
        except Exception:
            pass
        self.notify(
            f"{t('ui-refreshed')} {len(self._meta_only)} {t('ui-session-s')}",
            severity="information",
            timeout=5,
        )

    def action_open_session(self) -> None:
        """Open the highlighted session (same as Enter on sessions table)."""
        try:
            table = self.query_one("#session-table", DataTable)
            if not table.row_count:
                return
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            if row_key and row_key.value:
                self._open_session(str(row_key.value))
        except Exception:
            pass

    def action_show_help(self) -> None:
        from .bindings import notify_help

        notify_help(self.screen)
