"""Pluggable analysis — enabled plugins run on every analysis call.

Built-ins: ``basic``, ``engine`` (always enabled). Everything else is
external: load via ``analysis.plugins`` in config.json (see ``plugins/``
and ``examples/plugins/``). Only analyzers registered for *this* config
are enabled — not leftovers in the process registry or on-disk cache.

All UI/CLI/screen code should use :class:`AnalysisService` as the sole
interface — never import a specific analyzer or engine module directly.
"""

from __future__ import annotations

from .base import (
    AnalysisResult,
    Analyzer,
    AnalyzerInfo,
    Finding,
    NoopAnalyzer,
)
from .config import AnalysisPipelineConfig, load_pipeline_config, save_pipeline_config
from .registry import (
    BUILTIN_ANALYZER_IDS,
    analyze_session_path,
    analyzer_from_module_attr,
    get_analyzer,
    list_analyzers,
    load_config_plugins,
    register_analyzer,
    set_default_analyzer,
)
from .service import AnalysisService, get_analysis_service, set_analysis_service

__all__ = [
    "AnalysisPipelineConfig",
    "AnalysisResult",
    "AnalysisService",
    "Analyzer",
    "AnalyzerInfo",
    "BUILTIN_ANALYZER_IDS",
    "Finding",
    "NoopAnalyzer",
    "analyze_session_path",
    "get_analysis_service",
    "get_analyzer",
    "list_analyzers",
    "analyzer_from_module_attr",
    "load_config_plugins",
    "load_pipeline_config",
    "register_analyzer",
    "save_pipeline_config",
    "set_analysis_service",
    "set_default_analyzer",
]


def _register_builtins() -> None:
    from .basic import BasicAnalyzer
    from .plugins.engine.analyzer import EngineDetectorAnalyzer

    register_analyzer(BasicAnalyzer(), default=True)
    register_analyzer(EngineDetectorAnalyzer())


_register_builtins()
