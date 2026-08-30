"""Grok disk adapter (harness contract over parser)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from anqa.harness.grok import (
    GROK_HARNESS_ID,
    GrokAdapter,
    discover,
    load_meta,
    looks_like,
    parse_timeline,
    watch_hints,
)
from anqa.harness.registry import require_adapter
from anqa.models import SessionMeta, TraceEvent
from anqa.session.export_bundle import export_session_bundle

_MINIMAL = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "minimal_session"


def _write_summary_session(root: Path, name: str = "sess-1") -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(json.dumps({"generated_title": "t"}), encoding="utf-8")
    return sd


def test_harness_id_is_grok() -> None:
    assert GROK_HARNESS_ID == "grok"


def test_discover_finds_summary_json_session(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path)
    found = discover([tmp_path])
    assert sd.resolve() in {p.locator.resolve() for p in found}


def test_discover_host_root_uses_shallow_collector(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = host / "%2Fproj" / "host-sid"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "anqa.harness.grok.default_sessions_root",
        lambda: host,
    )
    found = discover([host])
    assert sess.resolve() in {p.locator.resolve() for p in found}


def test_looks_like_true_and_false(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert looks_like(sd) is True
    assert looks_like(str(sd)) is True
    assert looks_like(empty) is False
    assert looks_like(tmp_path / "missing") is False


def test_load_meta_returns_session_meta(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "meta-sid")
    meta = load_meta(sd)
    assert isinstance(meta, SessionMeta)
    assert meta.session_id == "meta-sid"
    assert meta.harness == "grok"


def test_parse_timeline_minimal_session() -> None:
    assert _MINIMAL.is_dir()
    events = parse_timeline(_MINIMAL)
    assert events
    assert all(isinstance(ev, TraceEvent) for ev in events)


def test_write_archive_packs_session_files(tmp_path: Path) -> None:
    """Grok adapter writes the session directory (workspace/terminal omitted)."""
    import tarfile

    sd = _write_summary_session(tmp_path, "pack-sid")
    (sd / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sd / "workspace" / "src").mkdir(parents=True)
    (sd / "workspace" / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (sd / "terminal" / "1").mkdir(parents=True)
    (sd / "terminal" / "1" / "out").write_text("y\n", encoding="utf-8")
    dest = tmp_path / "sess.tar.gz"
    members = GrokAdapter().write_archive(sd, dest)
    assert "pack-sid/summary.json" in members
    assert "pack-sid/events.jsonl" in members
    assert not any("workspace" in n for n in members)
    assert not any("terminal" in n for n in members)
    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
    assert names == set(members)
    opened = GrokAdapter().open_archive(dest, tmp_path / "opened")
    assert opened.session_id == "pack-sid"
    assert (opened.locator / "summary.json").is_file()
    assert not (opened.locator / "workspace").exists()


def test_export_bundle_from_session_dir(tmp_path: Path) -> None:
    sd = _write_summary_session(tmp_path, "pack-sid")
    (sd / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    dest = tmp_path / "bundle.tar.gz"
    result = export_session_bundle(sd, dest=dest)
    assert dest.is_file()
    assert result.session_id == "pack-sid"


def test_list_status_complete_and_running(tmp_path: Path) -> None:
    done = _write_summary_session(tmp_path, "done-sess")
    (done / "updates.jsonl").write_text(
        json.dumps({"params": {"update": {"sessionUpdate": "turn_completed"}}}) + "\n",
        encoding="utf-8",
    )
    live = _write_summary_session(tmp_path, "live-sess")
    (live / "updates.jsonl").write_text(
        json.dumps({"params": {"update": {"sessionUpdate": "user_message_chunk"}}}) + "\n",
        encoding="utf-8",
    )
    (live / "events.jsonl").write_text('{"type":"turn_started"}\n', encoding="utf-8")
    assert require_adapter(done).load_meta(done).list_status_label() == "complete"
    assert require_adapter(live).load_meta(live).list_status_label() == "running"


def test_watch_hints_include_updates_jsonl() -> None:
    assert "updates.jsonl" in watch_hints()


def test_bind_locator_and_ref_for_id(tmp_path: Path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = _write_summary_session(host / "%2Fproj", "host-sid")
    monkeypatch.setattr("anqa.harness.grok.default_sessions_root", lambda: host)
    adapter = GrokAdapter()
    bound = adapter.bind_locator(sess)
    assert bound is not None
    assert bound.session_id == "host-sid"
    assert bound.locator.resolve() == sess.resolve()
    assert adapter.bind_locator(tmp_path) is None
    found = adapter.ref_for_id("host-sid")
    assert found is not None
    assert found.locator.resolve() == sess.resolve()
    assert adapter.ref_for_id("missing") is None


def test_importing_grok_adapter_does_not_import_ui() -> None:
    saved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "anqa.harness" or name.startswith("anqa.harness.")
    }
    for name in list(saved):
        del sys.modules[name]
    ui_before = {n for n in sys.modules if n == "anqa.ui" or n.startswith("anqa.ui.")}
    try:
        import anqa.harness.grok as grok_mod

        ui_after = {n for n in sys.modules if n == "anqa.ui" or n.startswith("anqa.ui.")}
        assert ui_after == ui_before
        assert grok_mod.GROK_HARNESS_ID == "grok"
    finally:
        for name in list(sys.modules):
            if name == "anqa.harness" or name.startswith("anqa.harness."):
                del sys.modules[name]
        sys.modules.update(saved)
