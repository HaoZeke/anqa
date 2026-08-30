"""Import a harness archive or anqa export into the import store."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
from anqa.harness.grok import GrokAdapter
from anqa.notes import NOTES_FILENAME
from anqa.session.catalog import list_session_catalog, session_catalog_row
from anqa.session.export_bundle import export_session_bundle
from anqa.session.imports import (
    IMPORT_SIDECAR,
    import_session,
    looks_like_import_source,
)
from anqa.session.query import CatalogQueryRow, row_matches_query


def _seed_session(root: Path, name: str = "import-sid") -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"generated_title": "Imported session"}),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    return sd


def test_open_archive_round_trip(tmp_path: Path) -> None:
    sd = _seed_session(tmp_path / "src", "pack-sid")
    archive = tmp_path / "pack-sid.tar.gz"
    GrokAdapter().write_archive(sd, archive)
    dest = tmp_path / "opened"
    ref = GrokAdapter().open_archive(archive, dest)
    assert ref.session_id == "pack-sid"
    assert ref.harness == "grok"
    assert (ref.locator / "summary.json").is_file()
    assert not (ref.locator / "workspace").exists()


def test_import_native_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    sd = _seed_session(tmp_path / "src")
    archive = tmp_path / "sess.tar.gz"
    GrokAdapter().write_archive(sd, archive)
    result = import_session(archive)
    assert result.ref.session_id == "import-sid"
    assert result.replaced is False
    assert (result.ref.locator / IMPORT_SIDECAR).is_file()
    assert looks_like_import_source(archive)
    row = session_catalog_row(result.ref.locator)
    assert row is not None
    assert row["imported"] is True
    assert row["origin"] == "import"
    assert row_matches_query(CatalogQueryRow.from_wire(row), "is:import")
    assert not row_matches_query(CatalogQueryRow.from_wire(row), "is:host")
    from anqa.session.control_views import build_session_overview

    overview = build_session_overview(result.ref.locator)
    assert overview["meta"]["origin"] == "import"
    assert overview["meta"]["imported"] is True


def test_import_replaces_same_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    first = _seed_session(tmp_path / "a", "same-id")
    (first / "events.jsonl").write_text("one\n", encoding="utf-8")
    second = _seed_session(tmp_path / "b", "same-id")
    (second / "events.jsonl").write_text("two\n", encoding="utf-8")
    a1 = tmp_path / "one.tar.gz"
    a2 = tmp_path / "two.tar.gz"
    GrokAdapter().write_archive(first, a1)
    GrokAdapter().write_archive(second, a2)
    first_hit = import_session(a1)
    again = import_session(a2)
    assert again.replaced is True
    assert (again.ref.locator / "events.jsonl").read_text(encoding="utf-8") == "two\n"
    assert first_hit.ref.locator == again.ref.locator


def test_import_anqa_bundle_restores_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    from anqa.notes import NoteEntry, NotesDoc, save_notes

    sd = _seed_session(tmp_path / "src", "bundle-sid")
    notes_doc = NotesDoc(schema_id="default", session_id="bundle-sid")
    notes_doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "keep"},
            note_id="n1",
        )
    )
    save_notes(sd, notes_doc)
    bundle = export_session_bundle(sd, dest=tmp_path / "bundle.tar.gz")
    with tarfile.open(bundle.path, "r:gz") as tf:
        manifest = json.loads(tf.extractfile("manifest.json").read())
    assert manifest["harness"] == "grok"
    assert manifest["kind"] == "anqa-session-export"
    result = import_session(bundle.path)
    assert result.ref.session_id == "bundle-sid"
    notes = result.ref.locator / NOTES_FILENAME
    assert notes.is_file()
    assert "keep" in notes.read_text(encoding="utf-8")


def test_import_bundle_children(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    parent = _seed_session(tmp_path / "src", "parent-sid")
    child = parent.parent / "child-exp"
    child.mkdir()
    (child / "summary.json").write_text(
        json.dumps({"info": {"id": "child-exp"}, "session_kind": "subagent"}),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (parent / "subagents" / "child-exp").mkdir(parents=True)
    (parent / "subagents" / "child-exp" / "meta.json").write_text(
        json.dumps({"child_session_id": "child-exp", "subagent_type": "coder"}),
        encoding="utf-8",
    )
    (parent / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_spawned",
                        "childSessionId": "child-exp",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dest = tmp_path / "with-child.tar.gz"
    export_session_bundle(parent, dest=dest)
    result = import_session(dest)
    assert result.ref.session_id == "parent-sid"
    sibling = result.ref.locator.parent / "child-exp"
    assert sibling.is_dir()
    assert (sibling / "summary.json").is_file()


def test_import_rejects_unknown_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    junk = tmp_path / "notes.txt"
    junk.write_text("hello\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a session archive"):
        import_session(junk)


def test_import_rejects_path_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        info = tarfile.TarInfo(name="../escape/secret")
        payload = b"nope"
        info.size = len(payload)
        tf.addfile(info, fileobj=__import__("io").BytesIO(payload))
    with pytest.raises(RuntimeError):
        import_session(evil)


def test_catalog_lists_imported_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths
    import anqa.session.sources as sources

    home = tmp_path / "home"
    monkeypatch.setattr(paths, "APP_HOME", home)
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.setattr(sources, "_adapter_store_roots", lambda: [host])
    sd = _seed_session(tmp_path / "src", "listed")
    archive = tmp_path / "listed.tar.gz"
    GrokAdapter().write_archive(sd, archive)
    imported = import_session(archive)
    rows = list_session_catalog()
    ids = {r["sessionId"] for r in rows}
    assert "listed" in ids
    hit = next(r for r in rows if r["sessionId"] == "listed")
    assert hit["imported"] is True
    assert imported.ref.locator.name == "listed"


def test_catalog_keeps_host_and_import_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anqa.paths as paths
    import anqa.session.sources as sources
    from anqa.session.catalog import SessionCatalogCache

    home = tmp_path / "home"
    monkeypatch.setattr(paths, "APP_HOME", home)
    host = tmp_path / "host"
    live = _seed_session(host, "listed")
    monkeypatch.setattr(sources, "_adapter_store_roots", lambda: [host])
    archive = tmp_path / "listed.tar.gz"
    GrokAdapter().write_archive(live, archive)
    cache = SessionCatalogCache(traces_path=host, include_host=True, ttl=3600.0)
    first = cache.get(force=True)
    assert [r["origin"] for r in first if r["sessionId"] == "listed"] == ["host"]
    imported = import_session(archive)
    rows = list_session_catalog()
    same = [r for r in rows if r["sessionId"] == "listed"]
    assert len(same) == 2
    assert {r["origin"] for r in same} == {"host", "import"}
    updated, _changed = cache.refresh_rows([imported.ref.locator])
    same = [r for r in updated if r["sessionId"] == "listed"]
    assert len(same) == 2
    assert {r["origin"] for r in same} == {"host", "import"}


def test_import_directory_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import anqa.paths as paths

    monkeypatch.setattr(paths, "APP_HOME", tmp_path / "home")
    sd = _seed_session(tmp_path / "src", "dir-sid")
    result = import_session(sd)
    assert result.ref.session_id == "dir-sid"
    assert result.ref.locator != sd
    assert (result.ref.locator / "summary.json").is_file()
