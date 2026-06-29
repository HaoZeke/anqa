"""Analysis pipeline configuration."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..models import JsonObject
from ..paths import app_config_path

logger = logging.getLogger(__name__)


@dataclass
class AnalysisPipelineConfig:
    """Which plugins to load and global analysis behaviour.

    * ``plugins`` — list of ``"module:AnalyzerClass"`` specs only.
      Class implements :class:`~groket.analysis.base.Analyzer`; see
      :func:`registry.load_config_plugins`.
    * ``auto_analyze_on_open`` — run all analyzers when opening a session
    """

    plugins: list[str] = field(default_factory=list)
    auto_analyze_on_open: bool = True

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
        return cls(
            plugins=plugins,
            auto_analyze_on_open=bool(data.get("auto_analyze_on_open", True)),
        )


def _config_search_paths(
    work_dir: Path | None,
    config_path: Path | None,
) -> list[Path]:
    """Ordered candidate config files (first match wins)."""
    if config_path is not None:
        return [Path(config_path).expanduser()]

    candidates = [app_config_path()]
    if work_dir is not None:
        candidates.append(Path(work_dir).expanduser() / "config.json")
    return candidates


def load_pipeline_config(
    work_dir: Path | None = None,
    *,
    config_path: Path | None = None,
) -> AnalysisPipelineConfig:
    """Load analysis config.

    Search order: explicit *config_path* → ``~/.groket/config.json``
    → ``work_dir/config.json``.
    """
    cfg = AnalysisPipelineConfig()
    for fp in _config_search_paths(work_dir, config_path):
        if fp.is_file():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("analysis"), dict):
                    cfg = AnalysisPipelineConfig.from_dict(data["analysis"])
                    break
            except (json.JSONDecodeError, KeyError):
                logger.debug("Failed to parse analysis config from %s", fp, exc_info=True)
    return cfg


def save_pipeline_config(
    work_dir: Path | None = None, cfg: AnalysisPipelineConfig | None = None
) -> None:
    """Merge ``analysis`` section into the app-global config file.

    Falls back to ``work_dir/config.json`` when *work_dir* is given and no
    app-global config exists yet.
    """

    fp = app_config_path()
    if not fp.exists() and work_dir is not None:
        work_cfg = Path(work_dir).expanduser() / "config.json"
        if work_cfg.is_file():
            fp = work_cfg
    if cfg is None:
        cfg = AnalysisPipelineConfig()
    data: JsonObject = {}
    if fp.is_file():
        try:
            loaded = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, KeyError):
            logger.debug("Failed to read existing config from %s", fp, exc_info=True)
            data = {}
    data["analysis"] = cfg.to_dict()
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
