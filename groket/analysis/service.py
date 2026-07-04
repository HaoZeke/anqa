"""Facade for UI/CLI: thin dispatcher over config-enabled plugins."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Unpack

from ..flags import Flag, load_flags
from ..paths import analysis_cache_dir, default_work_dir
from ._cache import (
    cache_file_path,
    load_cached_result,
    read_cached_plugin_version,
    save_cached_result,
)
from .base import AnalysisResult, AnalyzeContext, Analyzer, AnalyzerInfo, NoopAnalyzer
from .config import AnalysisPipelineConfig, load_pipeline_config
from .registry import (
    BUILTIN_ANALYZER_IDS,
    get_analyzer,
    list_analyzers,
    load_config_plugins,
)

logger = logging.getLogger(__name__)


def _session_is_complete(session_dir: Path) -> bool:
    """True when the session has a ``turn_ended`` event (safe to cache)."""
    events = session_dir / "events.jsonl"
    if not events.is_file():
        return False

    try:
        with events.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    if json.loads(line).get("type") == "turn_ended":
                        return True
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return False


class AnalysisService:
    """Single entry point for app/screens/CLI — no direct plugin imports.

    Only **enabled** analyzers run on :meth:`analyze_all`: built-ins
    (``basic``, ``engine``) plus ids registered while loading
    ``config.analysis.plugins``.  Other analyzers may still sit in the
    process-wide registry (and on-disk cache from earlier configs) but are
    not executed or cache-served unless enabled for this service instance.
    """

    def __init__(
        self,
        work_dir: Path,
        config: AnalysisPipelineConfig | None = None,
        *,
        traces: Path | None = None,
        config_path: Path | None = None,
        cache_root: Path | None = None,
        enabled_ids: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.work_dir = Path(work_dir).expanduser()
        self.config_path = Path(config_path).expanduser() if config_path else None
        self.config = config or load_pipeline_config(
            self.work_dir,
            config_path=self.config_path,
        )
        self.traces = Path(traces).expanduser() if traces else None
        self.cache_root = Path(cache_root).expanduser() if cache_root else None
        self.load_failures: list[str] = []
        # Optional override for tests; otherwise builtins + this config's plugins.
        self._enabled_ids: set[str] = (
            set(enabled_ids) if enabled_ids is not None else set(BUILTIN_ANALYZER_IDS)
        )
        if enabled_ids is None:
            self._load_configured_plugins()
        self._apply_worker_pools()

    def _apply_worker_pools(self) -> None:
        """Sync global analysis / live-refresh pools from config."""
        from ..job_pools import configure_job_pools

        configure_job_pools(
            analysis_workers=self.config.analysis_workers,
            live_refresh_workers=self.config.live_refresh_workers,
        )

    def _load_configured_plugins(self) -> None:
        """Import config plugin specs and enable analyzer ids they register."""
        if not self.config.plugins:
            return

        config_dir = self.config_path.parent if self.config_path else None
        _loaded, self.load_failures, registered = load_config_plugins(
            self.config.plugins,
            config_dir=config_dir,
        )
        self._enabled_ids |= set(registered)

    @property
    def enabled_ids(self) -> frozenset[str]:
        """Analyzer ids that participate in :meth:`analyze_all` / :meth:`list_plugins`."""
        return frozenset(self._enabled_ids)

    def reload_config(self) -> AnalysisPipelineConfig:
        self.config = load_pipeline_config(self.work_dir, config_path=self.config_path)
        self.load_failures = []
        self._enabled_ids = set(BUILTIN_ANALYZER_IDS)
        self._apply_worker_pools()
        self._load_configured_plugins()
        return self.config

    def list_plugins(self) -> list[AnalyzerInfo]:
        """Enabled analyzers only (plus ``noop`` for display if nothing else)."""
        infos = [i for i in list_analyzers() if i.id in self._enabled_ids]
        if not infos:
            return [NoopAnalyzer().info]
        return infos

    def load_cached_all(self, session_dir: Path | str) -> dict[str, AnalysisResult]:
        """Return enabled analyzer results present in the on-disk cache only.

        Does not run analyzers. Empty mapping when there is no cache root or
        no valid entries for this session.
        """
        path = Path(session_dir)
        if self.cache_root is None:
            return {}
        results: dict[str, AnalysisResult] = {}
        for info in self.list_plugins():
            if info.id == "noop":
                continue
            cached = load_cached_result(
                self.cache_root,
                path,
                info.id,
                info.version,
            )
            if cached is not None:
                results[info.id] = cached
        return results

    def stale_analyzer_hints(self, session_dir: Path | str) -> list[str]:
        """Human-readable reasons force re-analyze is useful (empty if none).

        Detects enabled plugins whose on-disk cache was written with a different
        ``AnalyzerInfo.version``, whose **source file is newer than the cache**
        (version forgotten), or a newly enabled plugin when this session already
        has other analysis cache. Does **not** run analyzers.
        """
        path = Path(session_dir)
        if self.cache_root is None:
            return []
        enabled = [i for i in self.list_plugins() if i.id != "noop"]
        if not enabled:
            return []
        versions = {
            info.id: read_cached_plugin_version(self.cache_root, path, info.id) for info in enabled
        }
        any_cache = any(v is not None for v in versions.values())
        hints: list[str] = []
        for info in enabled:
            cached_ver = versions.get(info.id)
            if cached_ver is None:
                if any_cache:
                    hints.append(f"{info.id} v{info.version} (not in cache yet)")
                continue
            if cached_ver != info.version:
                hints.append(f"{info.id} v{cached_ver} → v{info.version}")
                continue
            # Version string matches but plugin *file* is newer than cache → stale.
            src_newer = self._analyzer_source_newer_than_cache(path, info.id)
            if src_newer:
                hints.append(f"{info.id} source newer than cache (re-analyze)")
        return hints

    def _analyzer_source_newer_than_cache(self, session_dir: Path, analyzer_id: str) -> bool:
        """True when the analyzer module file mtime is newer than its cache file."""
        if self.cache_root is None:
            return False
        cpath = cache_file_path(self.cache_root, session_dir, analyzer_id)
        if not cpath.is_file():
            return False
        try:
            cache_mtime = cpath.stat().st_mtime
        except OSError:
            return False
        try:
            analyzer: Analyzer = get_analyzer(analyzer_id)
        except KeyError:
            return False
        # Analyzer class module + LLM base/helpers when subclassed from there.
        modules: list[type] = [type(analyzer)]
        for cls in type(analyzer).__mro__:
            mod = getattr(cls, "__module__", "") or ""
            if mod.startswith("groket.analysis"):
                modules.append(cls)
        newest_src = 0.0
        seen: set[str] = set()
        for cls in modules:
            mod_name = getattr(cls, "__module__", None)
            if not mod_name or mod_name in seen:
                continue
            seen.add(mod_name)
            try:
                import importlib

                mod = importlib.import_module(mod_name)
            except Exception:
                continue
            mod_file = getattr(mod, "__file__", None)
            if not mod_file:
                continue
            try:
                newest_src = max(newest_src, Path(mod_file).stat().st_mtime)
            except OSError:
                continue
        if newest_src <= 0:
            return False
        # Small skew tolerance for FS clocks / write order.
        return newest_src > cache_mtime + 1.0

    def analyze_all(
        self,
        session_dir: Path | str,
        *,
        force: bool = False,
        **kwargs: Unpack[AnalyzeContext],
    ) -> dict[str, AnalysisResult]:
        """Run every **enabled** analyzer on *session_dir*.

        Two-pass execution: analyzers with ``AnalyzerInfo.defer`` run after
        the others and receive ``prior_findings`` (plus flags) so external
        pipelines can incorporate detector output.

        Completed sessions have their results cached per-plugin.  The cache
        is keyed by ``(session_id, analyzer_id)`` and invalidated when the
        trace mtime or plugin version changes.  Pass ``force=True`` to skip
        reading the cache (still writes fresh results when cacheable).

        Returns results keyed by analyzer ID.  Per-plugin exceptions are
        caught and logged — one broken plugin does not block others.
        """
        path = Path(session_dir)
        cacheable = self.cache_root is not None and _session_is_complete(path)
        ctx: AnalyzeContext = {**kwargs}
        results: dict[str, AnalysisResult] = {}
        deferred: list[AnalyzerInfo] = []

        # Pass 1: enabled non-deferred plugins only
        for info in self.list_plugins():
            if info.id == "noop":
                continue
            if info.id not in self._enabled_ids:
                continue
            if info.defer:
                deferred.append(info)
                continue
            results[info.id] = self._run_one(info, path, ctx, cacheable=cacheable, force=force)

        # Pass 2: deferred plugins get prior findings + flags
        if deferred:
            prior_findings = [f for r in results.values() if r.ok for f in r.findings]
            flags = ctx.get("flags") or self._load_flags(path)
            deferred_ctx: AnalyzeContext = {
                **ctx,
                "prior_findings": prior_findings,
                "flags": flags,
            }
            for info in deferred:
                results[info.id] = self._run_one(
                    info,
                    path,
                    deferred_ctx,
                    cacheable=cacheable,
                    force=force,
                )

        return results

    def _run_one(
        self,
        info: AnalyzerInfo,
        path: Path,
        kw: AnalyzeContext,
        *,
        cacheable: bool = False,
        force: bool = False,
    ) -> AnalysisResult:
        """Run a single analyzer, checking/populating cache when applicable."""
        if info.id not in self._enabled_ids:
            return AnalysisResult(
                session_id=path.name,
                session_dir=str(path),
                analyzer_id=info.id,
                ok=False,
                error=f"plugin {info.id} is not enabled in analysis config",
            )

        if cacheable and not force and self.cache_root is not None:
            cached = load_cached_result(
                self.cache_root,
                path,
                info.id,
                info.version,
            )
            if cached is not None:
                logger.debug("Cache hit for %s/%s", path.name, info.id)
                return cached

        try:
            result = get_analyzer(info.id).analyze(path, context=kw or None)
        except Exception:
            logger.warning("Plugin %s failed on %s", info.id, path.name, exc_info=True)
            return AnalysisResult(
                session_id=path.name,
                session_dir=str(path),
                analyzer_id=info.id,
                ok=False,
                error=f"plugin {info.id} raised an exception",
            )

        if cacheable and result.ok and self.cache_root is not None:
            save_cached_result(
                self.cache_root,
                path,
                info.id,
                info.version,
                result,
            )

        return result

    @staticmethod
    def _load_flags(session_dir: Path) -> list[Flag]:
        """Load user flags for the session, if any."""
        try:
            return load_flags(session_dir)
        except Exception:
            return []

    def analyze_session(
        self,
        session_dir: Path | str,
        *,
        analyzer_id: str | None = None,
        **kwargs: Unpack[AnalyzeContext],
    ) -> AnalysisResult:
        """Run a single named analyzer (must be registered; not limited to enabled)."""
        aid = analyzer_id or "basic"
        path = Path(session_dir)
        ctx: AnalyzeContext = {**kwargs}
        return get_analyzer(aid).analyze(path, context=ctx or None)


_service: AnalysisService | None = None


def get_analysis_service(
    work_dir: Path | None = None,
    *,
    traces: Path | None = None,
    config_path: Path | None = None,
) -> AnalysisService:
    """Return the process-wide analysis service.

    **Does not** recreate the service when *work_dir* is passed — that used to
    drop ``config_path`` / enabled plugins (e.g. example config) and silently
    fall back to ``~/.groket`` only. Prefer :func:`set_analysis_service` at app
    startup; pass *config_path* only when creating the first instance.
    """
    global _service
    if _service is None:
        _service = AnalysisService(
            work_dir or default_work_dir(),
            traces=traces,
            config_path=config_path,
            cache_root=analysis_cache_dir(),
        )
        return _service
    if traces is not None:
        _service.traces = Path(traces).expanduser()
    return _service


def set_analysis_service(service: AnalysisService) -> None:
    global _service
    _service = service
