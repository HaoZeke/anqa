"""SessionCatalogCache: single-flight, force refresh, fingerprint."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from groket.session.catalog import (
    SessionCatalogCache,
    session_meta_from_catalog_row,
)


def _write_sess(root: Path, name: str, title: str) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_catalog_cache_second_get_is_cached(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(12):
        _write_sess(traces, f"s{i:03d}", f"Title {i}")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=60.0)
    t0 = time.perf_counter()
    a = cache.get(force=True)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    b = cache.get()
    warm = time.perf_counter() - t0
    assert len(a) == 12
    assert len(b) == 12
    assert warm < cold
    assert warm < 0.05


def test_catalog_cache_force_rebuilds(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    assert len(cache.get(force=True)) == 1
    _write_sess(traces, "two", "Two")
    # Within TTL without force may still see fingerprint change (entry count).
    rows = cache.get(force=True)
    assert len(rows) == 2


def test_catalog_cache_single_flight(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(8):
        _write_sess(traces, f"x{i}", f"X{i}")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=60.0)
    results: list[int] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(len(cache.get(force=True)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert results == [8, 8, 8, 8]


def test_apply_fs_catalog_events_patches_dirty_row(tmp_path: Path) -> None:
    """Watch callback patches the dirty session instead of a full catalog scan."""
    from groket.integrations.daemon import apply_fs_catalog_events

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    _write_sess(traces, "two", "Two")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    (one / "events.jsonl").write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0})
        + "\n"
        + json.dumps({"ts": 2, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    sessions, notes = apply_fs_catalog_events(cache, [str(one / "events.jsonl")], [traces])
    assert one.resolve() in [p.resolve() for p in sessions]
    assert notes == []
    by_id = {str(r["sessionId"]): r for r in cache.get()}
    assert by_id["one"]["status"] == "complete"


def test_catalog_cache_refresh_rows_updates_one_status(tmp_path: Path) -> None:
    """FS watch must patch the dirty session instead of rescanning the tree."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    _write_sess(traces, "two", "Two")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    first = cache.get(force=True)
    by_id = {str(r["sessionId"]): r for r in first}
    assert "one" in by_id
    (one / "events.jsonl").write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0})
        + "\n"
        + json.dumps({"ts": 2, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    updated = cache.refresh_rows([one])
    by_id = {str(r["sessionId"]): r for r in updated}
    assert by_id["one"]["status"] == "complete"
    assert by_id["two"]["sessionId"] == "two"
    assert len(updated) == 2
    cached = cache.get()
    assert {str(r["sessionId"]): r["status"] for r in cached}["one"] == "complete"


def test_session_meta_from_catalog_row_status() -> None:
    meta = session_meta_from_catalog_row(
        {
            "sessionId": "abc",
            "path": "/tmp/abc",
            "title": "Hello",
            "model": "grok:high",
            "status": "awaiting",
            "origin": "host",
            "taskId": "task-9",
            "durationSeconds": 42.5,
            "numEvents": 17,
            "contextUsageCompact": "35% 1.2k/128k",
            "contextWindowUsagePct": 35,
            "contextTokensUsed": 1200,
            "contextWindowTokens": 128_000,
            "toolCallCount": 3,
            "errorCount": 1,
        }
    )
    assert meta is not None
    assert meta.session_id == "abc"
    assert meta.list_status_label() == "awaiting"
    assert meta.model_display == "grok:high"
    assert meta.task_id == "task-9"
    assert meta.duration_seconds == 42.5
    assert meta.num_events == 17
    assert meta.tool_call_count == 3
    assert meta.error_count == 1
    assert meta.context_window_usage_pct == 35
    assert meta.context_tokens_used == 1200
    assert meta.context_window_tokens == 128_000
    assert "35" in meta.context_usage_compact
