"""Notes RPC stays responsive while catalog discovery is building."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
from importlib import import_module
from pathlib import Path

import pytest
from async_wait import wait_until


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="anqa-notes-rpc-"))
    return root / name


def _write_grok_session(root: Path, name: str) -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": "Pinned store"}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return session_dir


@pytest.mark.asyncio
async def test_notes_list_does_not_join_catalog_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notes/list must answer while list_session_catalog is still blocked."""
    catalog_mod = import_module("anqa.session.catalog")
    daemon = import_module("anqa.control.daemon")
    client_mod = import_module("anqa.control.client")

    store = tmp_path / "pinned-store"
    session_dir = _write_grok_session(store, "sess-pinned")
    release = threading.Event()
    started = threading.Event()
    real = catalog_mod.list_session_catalog

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("catalog scan still blocked")
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)
    sock = _short_sock("notes-warm.sock")
    server = daemon.build_domain_control_server(socket_path=sock, traces_path=store)
    await server.start()
    try:
        await daemon._catalog_warm_once(server._catalog_cache)  # type: ignore[attr-defined]
        assert started.wait(timeout=2)
        client = client_mod.ControlClient(sock, client_name="notes-warm", timeout=2.0)
        t0 = time.perf_counter()
        await client.initialize()
        listed = await client.notes_list(session_dir.name)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.5
        assert "revision" in listed
        assert listed["notes"] == []
    finally:
        release.set()
        await server.close()


@pytest.mark.asyncio
async def test_notes_list_does_not_walk_host_or_collect_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pinned store must not walk host adapter stores to list notes."""
    sources_mod = import_module("anqa.session.sources")
    catalog_mod = import_module("anqa.session.catalog")
    grok_mod = import_module("anqa.harness.grok")
    daemon = import_module("anqa.control.daemon")
    client_mod = import_module("anqa.control.client")

    store = tmp_path / "only-store"
    session_dir = _write_grok_session(store, "sess-notes")
    host = tmp_path / "host-sessions"
    host.mkdir()

    def hang(*_a: object, **_k: object) -> object:
        time.sleep(8)
        raise AssertionError("host walk must not run on notes/list")

    monkeypatch.setattr(sources_mod, "collect_session_dirs", hang)
    monkeypatch.setattr(catalog_mod, "list_session_catalog", hang)
    monkeypatch.setattr(grok_mod.GrokAdapter, "ref_for_id", hang)
    monkeypatch.setattr(sources_mod, "_adapter_store_roots", lambda: [host])

    sock = _short_sock("notes-no-host.sock")
    server = daemon.build_domain_control_server(socket_path=sock, traces_path=store)
    await server.start()
    try:
        client = client_mod.ControlClient(sock, client_name="notes-no-host", timeout=2.0)
        await client.initialize()
        t0 = time.perf_counter()
        listed = await client.notes_list(session_dir.name)
        assert time.perf_counter() - t0 < 1.5
        assert listed["notes"] == []
        saved = await client.notes_upsert(
            session_dir.name,
            {
                "id": "n-fast",
                "turnIndex": 0,
                "source": "tui",
                "fields": {"summary": "while catalog builds"},
                "eventIndices": [],
            },
            expected_revision=listed["revision"],
        )
        assert any(n["id"] == "n-fast" for n in saved["notes"])
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_notes_rpc_does_not_take_heavy_semaphore(tmp_path: Path) -> None:
    """Held catalog/overview slots must not stall notes/list."""
    control = import_module("anqa.control.server")
    client_mod = import_module("anqa.control.client")

    session_dir = _write_grok_session(tmp_path, "sess-light")
    server = control.ControlServer(
        socket_path=_short_sock("notes-light.sock"),
        resolve_session=lambda ref: (
            session_dir if ref in {session_dir.name, str(session_dir)} else None
        ),
    )
    await server.start()
    held = asyncio.Event()
    try:
        for _ in range(control.HEAVY_IO_CONCURRENCY):
            await server._heavy_sem.acquire()
        held.set()
        client = client_mod.ControlClient(
            server.socket_path, client_name="notes-light", timeout=2.0
        )
        await client.initialize()
        t0 = time.perf_counter()
        listed = await client.notes_list(session_dir.name)
        assert time.perf_counter() - t0 < 1.5
        assert "revision" in listed
    finally:
        if held.is_set():
            for _ in range(control.HEAVY_IO_CONCURRENCY):
                server._heavy_sem.release()
        await server.close()


def test_pinned_store_disables_host_include(tmp_path: Path) -> None:
    """``anqad -P`` / traces_path override does not scan host adapter stores."""
    daemon = import_module("anqa.control.daemon")
    catalog_mod = import_module("anqa.session.catalog")

    store = tmp_path / "pin"
    store.mkdir()
    assert daemon.daemon_include_host(store, None) is False
    assert daemon.daemon_include_host(None, None) is None
    assert daemon.daemon_include_host(store, True) is True

    server = daemon.build_domain_control_server(
        socket_path=_short_sock("pin-host.sock"),
        traces_path=store,
    )
    cache = server._catalog_cache  # type: ignore[attr-defined]
    assert cache._include_host is False
    roots = catalog_mod.catalog_scan_roots(traces_path=store, include_host=cache._include_host)
    assert all(root.path.resolve() != store.parent for root in roots)
    assert any(root.path.resolve() == store.resolve() for root in roots)


@pytest.mark.asyncio
async def test_catalog_warm_does_not_join_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup warm must kick a rebuild without occupying a worker for 120s."""
    catalog_mod = import_module("anqa.session.catalog")
    daemon = import_module("anqa.control.daemon")

    store = tmp_path / "warm-store"
    _write_grok_session(store, "sess-warm")
    release = threading.Event()
    started = threading.Event()
    real = catalog_mod.list_session_catalog

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("warm scan still blocked")
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)
    cache = catalog_mod.SessionCatalogCache(traces_path=store, include_host=False)
    t0 = time.perf_counter()
    await daemon._catalog_warm_once(cache)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.8
    assert started.wait(timeout=2)
    release.set()
    rows = cache.get()
    assert any(str(row.get("sessionId")) == "sess-warm" for row in rows)


@pytest.mark.asyncio
async def test_diagnostics_exposes_active_rpc_and_bounded_failure(
    tmp_path: Path,
) -> None:
    """Owner diagnostics names the in-flight RPC and the last bounded failure."""
    control = import_module("anqa.control.server")
    client_mod = import_module("anqa.control.client")
    diagnostics = import_module("anqa.diagnostics.self_test")

    session_dir = _write_grok_session(tmp_path, "sess-diag")
    server = control.ControlServer(
        socket_path=_short_sock("notes-diag.sock"),
        resolve_session=lambda ref: (
            session_dir if ref in {session_dir.name, str(session_dir)} else None
        ),
    )
    gate = threading.Event()
    orig = server._access.notes_list

    def slow_list(session: str, *args: object, **kwargs: object) -> object:
        if not gate.wait(timeout=5):
            raise TimeoutError("notes list still gated")
        return orig(session, *args, **kwargs)

    server._access.notes_list = slow_list  # type: ignore[method-assign]
    await server.start()
    try:
        client = client_mod.ControlClient(server.socket_path, client_name="notes-diag", timeout=3.0)
        await client.initialize()
        task = asyncio.create_task(client.notes_list(session_dir.name))
        await wait_until(
            lambda: any(
                item["method"] == "notes/list" for item in server.rpc_diagnostics()["active"]
            ),
            timeout=2.0,
            description="notes/list is the active RPC",
        )
        snap = server.rpc_diagnostics()
        assert snap["active"][0]["method"] == "notes/list"
        assert snap["lastFailure"] is None
        gate.set()
        listed = await task
        assert "revision" in listed

        server._record_rpc_failure(
            "notes/upsert",
            -32603,
            "notes rpc timed out",
            {"boundSeconds": control.NOTES_RPC_TIMEOUT},
        )
        fail = server.rpc_diagnostics()["lastFailure"]
        assert fail is not None
        assert fail["method"] == "notes/upsert"
        assert fail["code"] == -32603
        assert "timed out" in str(fail["message"])

        via_rpc = await client.diagnostics()
        assert via_rpc["lastFailure"]["method"] == "notes/upsert"

        # Doctor uses a blocking socket; run it off this loop so the owner can answer.
        report = await asyncio.to_thread(
            diagnostics.run_self_test,
            catalog_root=tmp_path,
            control_socket=server.socket_path,
        )
        ids = {c.id for c in report.checks}
        assert "control_rpc" in ids
        row = next(c for c in report.checks if c.id == "control_rpc")
        assert "notes/upsert" in row.detail
        assert "timed out" in row.detail
    finally:
        gate.set()
        await server.close()
