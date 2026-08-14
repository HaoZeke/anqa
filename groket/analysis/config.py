"""Analysis pipeline configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..models import JsonObject

# When to run analyzers automatically (never blocks timeline paint).
# * session_complete — not live; has a settled turn outcome (default)
# * never — only via command palette / explicit force analyze
_AUTO_ANALYZE_WHEN = frozenset({"session_complete", "never"})


@dataclass
class AnalysisPipelineConfig:
    """Which plugins to load and global analysis behaviour.

    * ``plugins`` — list of ``"module:AnalyzerClass"`` specs only.
      Class implements :class:`~groket.analysis.base.Analyzer`; see
      :func:`registry.load_config_plugins`.
    * ``auto_analyze_when`` — ``session_complete`` (default) or ``never``.
    * ``analysis_workers`` / ``live_refresh_workers`` — fixed pool sizes (default 1).
    """

    plugins: list[str] = field(default_factory=list)
    auto_analyze_when: str = "session_complete"
    analysis_workers: int = 1
    live_refresh_workers: int = 1

    def to_dict(self) -> JsonObject:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: JsonObject | None) -> AnalysisPipelineConfig:
        if not data or not isinstance(data, dict):
            return cls()
        raw_plugins = data.get("plugins")
        plugins: list[str] = []
        if isinstance(raw_plugins, list):
            plugins = [str(p).strip() for p in raw_plugins if isinstance(p, str) and p.strip()]
        when = str(data.get("auto_analyze_when") or "session_complete").strip().lower()
        if when not in _AUTO_ANALYZE_WHEN:
            when = "session_complete"
        try:
            aw = int(data.get("analysis_workers", 1))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            aw = 1
        try:
            rw = int(data.get("live_refresh_workers", 1))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            rw = 1
        return cls(
            plugins=plugins,
            auto_analyze_when=when,
            analysis_workers=max(1, aw),
            live_refresh_workers=max(1, rw),
        )


def load_pipeline_config(
    work_dir: Path | None = None,
    *,
    config_path: Path | None = None,
) -> AnalysisPipelineConfig:
    """Load the ``analysis`` section from the app config file.

    *work_dir* is ignored (config lives under :data:`~groket.paths.APP_HOME`).
    *config_path* selects an explicit file (tests / ``--config``).
    """
    from ..config import load_app_config

    _ = work_dir
    prefs = load_app_config(config_path)
    return AnalysisPipelineConfig(
        plugins=list(prefs.analysis.plugins),
        auto_analyze_when=prefs.analysis.auto_analyze_when,
        analysis_workers=prefs.analysis.analysis_workers,
        live_refresh_workers=prefs.analysis.live_refresh_workers,
    )


def save_pipeline_config(
    work_dir: Path | None = None,
    cfg: AnalysisPipelineConfig | None = None,
    *,
    config_path: Path | None = None,
) -> None:
    """Write the ``analysis`` section into the app config file."""
    from ..config import AnalysisPrefs, update_app_config

    _ = work_dir
    pipe = cfg or AnalysisPipelineConfig()
    update_app_config(
        config_path,
        analysis=AnalysisPrefs(
            plugins=list(pipe.plugins),
            auto_analyze_when=pipe.auto_analyze_when,
            analysis_workers=pipe.analysis_workers,
            live_refresh_workers=pipe.live_refresh_workers,
        ),
    )
