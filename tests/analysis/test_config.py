"""Tests for analysis pipeline configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from groket.analysis.config import (
    AnalysisPipelineConfig,
    load_pipeline_config,
    save_pipeline_config,
)


class TestAnalysisPipelineConfig:
    def test_defaults(self):
        cfg = AnalysisPipelineConfig()
        assert cfg.plugins == []
        assert cfg.auto_analyze_when == "session_complete"
        assert cfg.analysis_workers == 1
        assert cfg.live_refresh_workers == 1

    def test_from_dict(self):
        cfg = AnalysisPipelineConfig.from_dict(
            {
                "plugins": ["mod.a", "mod.b"],
                "auto_analyze_when": "never",
            }
        )
        assert len(cfg.plugins) == 2
        assert cfg.auto_analyze_when == "never"

    def test_from_dict_none(self):
        cfg = AnalysisPipelineConfig.from_dict(None)
        assert cfg.plugins == []

    def test_from_dict_empty(self):
        cfg = AnalysisPipelineConfig.from_dict({})
        assert cfg.plugins == []

    def test_from_dict_filters_bad_plugins(self):
        cfg = AnalysisPipelineConfig.from_dict(
            {
                "plugins": ["valid:func", 123, None, "", "  "],
            }
        )
        assert cfg.plugins == ["valid:func"]

    def test_from_dict_ignores_unknown_keys(self):
        cfg = AnalysisPipelineConfig.from_dict(
            {
                "plugins": ["a:b"],
                "force_on_refresh": True,
            }
        )
        assert cfg.plugins == ["a:b"]
        assert not hasattr(cfg, "force_on_refresh")

    def test_to_dict_roundtrip(self):
        cfg = AnalysisPipelineConfig(
            plugins=["a:b"],
            auto_analyze_when="never",
            analysis_workers=2,
        )
        d = cfg.to_dict()
        restored = AnalysisPipelineConfig.from_dict(d)
        assert restored.plugins == cfg.plugins
        assert restored.auto_analyze_when == "never"
        assert restored.analysis_workers == 2


class TestLoadPipelineConfig:
    def test_no_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("groket.paths.app_config_path", lambda: tmp_path / "missing.toml")
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        cfg = load_pipeline_config()
        assert cfg.plugins == []

    def test_load_from_config_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fp = tmp_path / "config.toml"
        fp.write_text(
            '[analysis]\nplugins = ["groket.analysis.plugins.engine.analyzer:EngineDetectorAnalyzer"]\n'
        )
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        cfg = load_pipeline_config()
        assert len(cfg.plugins) == 1

    def test_explicit_config_path(self, tmp_path: Path) -> None:
        config_file = tmp_path / "custom.toml"
        config_file.write_text('[analysis]\nplugins = ["x:y"]\nauto_analyze_when = "never"\n')
        cfg = load_pipeline_config(config_path=config_file)
        assert cfg.plugins == ["x:y"]
        assert cfg.auto_analyze_when == "never"

    def test_malformed_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fp = tmp_path / "config.toml"
        fp.write_text("not = [toml")
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        cfg = load_pipeline_config()
        assert cfg.plugins == []


class TestSavePipelineConfig:
    def test_save_creates_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fp = tmp_path / "config.toml"
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        save_pipeline_config(cfg=AnalysisPipelineConfig(plugins=["a:b"]))
        assert fp.is_file()
        data = tomlkit.parse(fp.read_text())
        assert list(data["analysis"]["plugins"]) == ["a:b"]

    def test_save_keeps_theme(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fp = tmp_path / "config.toml"
        fp.write_text('theme = "nord"\n[analysis]\nplugins = []\n')
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        save_pipeline_config(cfg=AnalysisPipelineConfig(plugins=["new:plugin"]))
        data = tomlkit.parse(fp.read_text())
        assert data["theme"] == "nord"
        assert list(data["analysis"]["plugins"]) == ["new:plugin"]

    def test_save_keeps_unknown_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fp = tmp_path / "config.toml"
        fp.write_text('other_key = "value"\n[analysis]\nplugins = []\n')
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        save_pipeline_config(cfg=AnalysisPipelineConfig(plugins=["new:plugin"]))
        data = tomlkit.parse(fp.read_text())
        assert data["other_key"] == "value"
        assert list(data["analysis"]["plugins"]) == ["new:plugin"]

    def test_save_with_no_existing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fp = tmp_path / "config.toml"
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        save_pipeline_config(cfg=AnalysisPipelineConfig(plugins=["a:b"], auto_analyze_when="never"))
        data = tomlkit.parse(fp.read_text())
        assert list(data["analysis"]["plugins"]) == ["a:b"]

    def test_save_corrupt_existing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fp = tmp_path / "config.toml"
        fp.write_text("not = [toml")
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        save_pipeline_config(cfg=AnalysisPipelineConfig(plugins=["x:y"]))
        data = tomlkit.parse(fp.read_text())
        assert list(data["analysis"]["plugins"]) == ["x:y"]

    def test_save_default_config_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fp = tmp_path / "config.toml"
        monkeypatch.setattr("groket.paths.app_config_path", lambda: fp)
        from groket.config import invalidate_config_cache

        invalidate_config_cache()
        save_pipeline_config(cfg=None)
        data = tomlkit.parse(fp.read_text())
        assert list(data["analysis"]["plugins"]) == []
