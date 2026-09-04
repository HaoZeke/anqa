"""Notes session resolve must not wait on a catalog store walk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from anqa.harness.registry import resolve_session_ref
from anqa.session.access import LocalSessionAccess
from anqa.session.catalog import resolve_session_locator
from anqa.session.sources import find_named_session_dir


def _write_sess(root: Path, name: str, *, title: str = "Notes") -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_find_named_session_dir_direct_and_cwd_bucket(tmp_path: Path) -> None:
    store = tmp_path / "store"
    direct = _write_sess(store, "sess-direct")
    nested = store / "%2Fhome%2Fproj" / "sess-nested"
    nested.mkdir(parents=True)
    (nested / "summary.json").write_text("{}", encoding="utf-8")
    (nested / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    assert find_named_session_dir(store, "sess-direct") == direct.resolve()
    assert find_named_session_dir(store, "sess-nested") == nested.resolve()
    assert find_named_session_dir(store, "missing") is None


def test_resolve_session_locator_skips_catalog_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anqa.session.catalog as catalog_mod
    import anqa.session.sources as sources_mod

    store = tmp_path / "store"
    sess = _write_sess(store, "only-sess")

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("notes resolve walked the catalog")

    monkeypatch.setattr(catalog_mod, "list_session_catalog", _boom)
    monkeypatch.setattr(sources_mod, "collect_session_dirs", _boom)

    assert resolve_session_locator("only-sess", traces_path=store) == sess.resolve()
    assert resolve_session_locator(str(sess), traces_path=store) == sess.resolve()
    assert resolve_session_locator("grok:only-sess", traces_path=store) == sess.resolve()
    assert resolve_session_locator("missing", traces_path=store) is None


def test_resolve_session_ref_notes_skips_adapter_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    sess = _write_sess(store, "bare-id")

    def path_resolve(reference: str) -> Path | None:
        return resolve_session_locator(reference, traces_path=store)

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("notes resolve walked adapters")

    monkeypatch.setattr("anqa.harness.registry.adapters", _boom)
    found = resolve_session_ref("bare-id", path_resolve=path_resolve, walk_adapters=False)
    assert found is not None
    assert found.locator == sess.resolve()
    missing = resolve_session_ref("nope", path_resolve=path_resolve, walk_adapters=False)
    assert missing is None


def test_notes_list_does_not_call_catalog_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "store"
    sess = _write_sess(store, "note-sess")

    def path_resolve(reference: str) -> Path | None:
        return resolve_session_locator(reference, traces_path=store)

    access = LocalSessionAccess(resolve_session=path_resolve)

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("notes_list waited on catalog get")

    monkeypatch.setattr("anqa.session.catalog.SessionCatalogCache.get", _boom)
    monkeypatch.setattr("anqa.session.catalog.list_session_catalog", _boom)
    snap = access.notes_list("note-sess")
    assert snap["revision"]
    assert snap["notes"] == []
    again = access.notes_list(str(sess))
    assert again["revision"] == snap["revision"]
