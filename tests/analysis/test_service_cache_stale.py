"""Stale plugin version hints for force re-analyze (no auto run)."""

from __future__ import annotations

from pathlib import Path

from groket.analysis._cache import save_cached_result
from groket.analysis.base import AnalysisResult
from groket.analysis.service import AnalysisService


def test_stale_hints_version_bump(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    sess = tmp_path / "sess"
    sess.mkdir()
    (sess / "events.jsonl").write_text('{"type":"turn_ended","timestamp":1}\n', encoding="utf-8")
    save_cached_result(
        cache,
        sess,
        "feedback",
        "10",
        AnalysisResult(session_id="sess", analyzer_id="feedback", ok=True),
    )
    svc = AnalysisService(
        tmp_path,
        enabled_ids={"basic", "engine", "feedback"},
        cache_root=cache,
    )
    # Monkeypatch list_plugins versions via registry is heavy — call logic by
    # temporarily replacing list_plugins on instance.
    from groket.analysis.base import AnalyzerInfo

    def _list() -> list:
        return [
            AnalyzerInfo(id="feedback", name="F", version="11"),
            AnalyzerInfo(id="engine", name="E", version="1"),
        ]

    svc.list_plugins = _list  # type: ignore[method-assign]
    # engine has no cache but feedback does → engine "not in cache yet"
    # feedback version mismatch
    hints = svc.stale_analyzer_hints(sess)
    assert any("feedback" in h and "10" in h and "11" in h for h in hints)
    assert any("engine" in h and "not in cache" in h for h in hints)


def test_stale_hints_empty_when_no_cache(tmp_path: Path) -> None:
    sess = tmp_path / "sess"
    sess.mkdir()
    svc = AnalysisService(tmp_path, enabled_ids={"basic"}, cache_root=tmp_path / "c")
    from groket.analysis.base import AnalyzerInfo

    svc.list_plugins = lambda: [AnalyzerInfo(id="basic", name="B", version="1")]  # type: ignore[method-assign]
    assert svc.stale_analyzer_hints(sess) == []


def test_stale_when_source_newer_than_cache(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    from groket.analysis import registry as reg
    from groket.analysis._cache import save_cached_result
    from groket.analysis.base import AnalysisResult, AnalyzerInfo

    cache = tmp_path / "cache"
    sess = tmp_path / "sess"
    sess.mkdir()
    (sess / "events.jsonl").write_text('{"type":"turn_ended"}\n', encoding="utf-8")
    save_cached_result(
        cache,
        sess,
        "fake",
        "1",
        AnalysisResult(session_id="sess", analyzer_id="fake", ok=True),
    )
    # Make cache older
    cpath = cache / "analysis" / "sess" / "fake.json"
    old = time.time() - 100
    os.utime(cpath, (old, old))

    class Fake:
        @property
        def info(self):
            return AnalyzerInfo(id="fake", name="F", version="1")

        def analyze(self, session_dir, **kw):
            return AnalysisResult(ok=True, analyzer_id="fake")

    # Register with a real module file we can touch — use this test file
    fake = Fake()
    monkeypatch.setitem(reg._REGISTRY, "fake", fake)
    # Point Fake's module to this test file
    Fake.__module__ = __name__

    svc = AnalysisService(tmp_path, enabled_ids={"fake"}, cache_root=cache)
    svc.list_plugins = lambda: [AnalyzerInfo(id="fake", name="F", version="1")]  # type: ignore[method-assign]
    # Touch this test module file
    os.utime(Path(__file__), None)
    hints = svc.stale_analyzer_hints(sess)
    assert any("source newer" in h for h in hints), hints
