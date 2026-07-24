"""Session export embeds nested official grok-trace.tar.gz (CLI only)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from groket.session.export_bundle import (
    GROK_TRACE_ARCHIVE_NAME,
    assert_grok_trace_archive_shape,
    build_grok_trace_archive,
    export_session_bundle,
    grok_trace_member_paths,
    run_volume_for_session,
)

# Official core members from a real ``grok trace`` export (/var/tmp/actual.tar.gz).
_ACTUAL_CORE = frozenset(
    {
        "export_metadata.json",
        "trace_config.json",
        "summary.json",
        "events.jsonl",
        "chat_history.jsonl",
        "prompt_context.json",
        "system_prompt.txt",
    }
)

SID = "019f-test-session"


def _seed_session(root: Path) -> Path:
    """Layout: runs/traces/groket-abc/%2Fworkspace/<sid>/…"""
    run = root / "runs" / "traces" / "groket-abc-model"
    sess = run / "%2Fworkspace" / SID
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sess / "summary.json").write_text('{"ok":true}\n', encoding="utf-8")
    (sess / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    (sess / "prompt_context.json").write_text("{}\n", encoding="utf-8")
    (sess / "system_prompt.txt").write_text("sys\n", encoding="utf-8")
    (run / "run.json").write_text('{"run_id":"r1"}\n', encoding="utf-8")
    (run / "groket-prompt.txt").write_text("hello\n", encoding="utf-8")
    (run / "groket-launch.json").write_text("{}\n", encoding="utf-8")
    (run / "%2Fworkspace" / "prompt_history.jsonl").write_text("p\n", encoding="utf-8")
    turn = run / ".groket-turn"
    turn.mkdir()
    (turn / "scripted-turns.json").write_text("[]\n", encoding="utf-8")
    return sess


def _fake_cli_archive_bytes(session_id: str = SID) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in _ACTUAL_CORE:
            data = b"{}\n" if name.endswith(".json") else b""
            if name == "export_metadata.json":
                data = json.dumps(
                    {
                        "session_id": session_id,
                        "grok_version": "0.2.106",
                        "os": "linux",
                        "arch": "x86_64",
                        "exported_at": "2026-07-20T00:00:00+00:00",
                    }
                ).encode()
            if name == "trace_config.json":
                data = json.dumps({"trace_upload_enabled": False}).encode()
            info = tarfile.TarInfo(name=f"{session_id}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_run_volume_for_session(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path)
    vol = run_volume_for_session(sess)
    assert vol is not None
    assert vol.name == "groket-abc-model"


def test_build_grok_trace_uses_cli_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested archive is the CLI file as-is (exact bytes)."""
    sess = _seed_session(tmp_path)
    expected = _fake_cli_archive_bytes()

    def _fake_cli(_session_dir: Path, out_tar: Path) -> None:
        out_tar.write_bytes(expected)

    monkeypatch.setattr(
        "groket.session.export_bundle._grok_trace_via_cli",
        _fake_cli,
    )
    out = tmp_path / "from-cli.tar.gz"
    build_grok_trace_archive(sess, out)
    assert out.read_bytes() == expected
    assert set(assert_grok_trace_archive_shape(out, SID)) >= grok_trace_member_paths(SID)


def test_build_grok_trace_no_fallback_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _seed_session(tmp_path)

    def _fail(_session_dir: Path, out_tar: Path) -> None:
        raise RuntimeError(
            "grok CLI not found on PATH; session export requires `grok trace --local`"
        )

    monkeypatch.setattr(
        "groket.session.export_bundle._grok_trace_via_cli",
        _fail,
    )
    with pytest.raises(RuntimeError, match="grok CLI not found"):
        build_grok_trace_archive(sess, tmp_path / "x.tar.gz")


def test_export_session_bundle_embeds_nested_grok_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _seed_session(tmp_path)
    expected = _fake_cli_archive_bytes()

    def _fake_cli(_session_dir: Path, out_tar: Path) -> None:
        out_tar.write_bytes(expected)

    monkeypatch.setattr(
        "groket.session.export_bundle._grok_trace_via_cli",
        _fake_cli,
    )
    cache = tmp_path / "cache"
    analysis = cache / "analysis" / SID
    analysis.mkdir(parents=True)
    (analysis / "demo.json").write_text(
        json.dumps(
            {
                "result": {
                    "analyzer_id": "demo",
                    "ok": True,
                    "summary": "demo summary",
                    "findings": [
                        {
                            "id": "f1",
                            "plugin_id": "demo",
                            "severity": "medium",
                            "title": "Demo issue",
                            "detail": "Something happened",
                            "category": "Test",
                            "event_indices": [2, 4],
                        }
                    ],
                    "artifacts": {},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (analysis / "feedback.json").write_text(
        json.dumps(
            {
                "result": {
                    "analyzer_id": "feedback",
                    "ok": True,
                    "summary": "ignored when report present",
                    "findings": [],
                    "artifacts": {"report": "# Feedback report\n\nFull markdown from plugin.\n"},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    from groket.notes import NoteEntry, NotesDoc, save_notes

    notes_doc = NotesDoc(schema_id="default", session_id=SID)
    notes_doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "export me", "detail": "turn note"},
            event_indices=[2],
            note_id="n-export",
        )
    )
    save_notes(sess, notes_doc)

    dest = tmp_path / "out" / "bundle.tar.gz"
    result = export_session_bundle(
        sess,
        dest=dest,
        analysis_cache_root=cache,
        work_dir=tmp_path,
    )
    assert result.path == dest.resolve()
    assert result.used_grok_cli is True
    assert result.session_id == SID
    assert dest.is_file()

    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
        manifest = json.loads(tf.extractfile("manifest.json").read().decode())  # type: ignore[union-attr]
        nested_f = tf.extractfile(GROK_TRACE_ARCHIVE_NAME)
        assert nested_f is not None
        nested_bytes = nested_f.read()
        demo_md = tf.extractfile("analysis/demo.md")
        assert demo_md is not None
        demo_text = demo_md.read().decode()
        fb_md = tf.extractfile("analysis/feedback.md")
        assert fb_md is not None
        fb_text = fb_md.read().decode()
        notes_f = tf.extractfile("notes/operator_notes.toml")
        assert notes_f is not None
        notes_text = notes_f.read().decode()

    assert nested_bytes == expected
    assert "manifest.json" in names
    assert "README.txt" in names
    assert GROK_TRACE_ARCHIVE_NAME in names
    assert [n for n in names if n.endswith(".tar.gz")] == [GROK_TRACE_ARCHIVE_NAME]
    assert not any(n == SID or n.startswith(f"{SID}/") for n in names)
    assert not any(n == "trace" or n.startswith("trace/") for n in names)

    nested_path = tmp_path / "extracted-nested.tar.gz"
    nested_path.write_bytes(nested_bytes)
    nested_names = set(assert_grok_trace_archive_shape(nested_path, SID))
    for core in _ACTUAL_CORE:
        assert f"{SID}/{core}" in nested_names

    assert "run/run.json" in names
    assert "run/groket-prompt.txt" in names
    assert "run/prompt_history.jsonl" in names
    assert "run/.groket-turn/scripted-turns.json" in names
    assert "analysis/demo.json" in names
    assert "analysis/demo.md" in names
    assert "analysis/feedback.json" in names
    assert "analysis/feedback.md" in names
    assert "notes/operator_notes.toml" in names
    assert "notes/schema.toml" not in names
    assert "export me" in notes_text
    assert "n-export" in notes_text
    assert "Demo issue" in demo_text
    assert "Something happened" in demo_text
    assert "Full markdown from plugin" in fb_text
    assert "# Feedback report" in fb_text
    assert manifest["session_id"] == SID
    assert manifest["grok_trace"] == GROK_TRACE_ARCHIVE_NAME
    assert manifest["schema"] == 5
    assert manifest["grok_trace_via_cli"] is True
    assert GROK_TRACE_ARCHIVE_NAME in manifest["members"]


def test_export_cli_failure_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _seed_session(tmp_path)

    def _fail(_session_dir: Path, out_tar: Path) -> None:
        raise RuntimeError("grok trace --local failed (rc=1): boom")

    monkeypatch.setattr(
        "groket.session.export_bundle._grok_trace_via_cli",
        _fail,
    )
    with pytest.raises(RuntimeError, match="grok trace --local failed"):
        export_session_bundle(sess, dest=tmp_path / "out.tar.gz")


def test_export_missing_session_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export_session_bundle(tmp_path / "nope", dest=tmp_path / "x.tar.gz")


def test_markdown_from_analysis_result_prefers_report_artifact() -> None:
    from groket.session.export_bundle import _markdown_from_analysis_result

    md = _markdown_from_analysis_result(
        {
            "analyzer_id": "x",
            "summary": "ignored",
            "artifacts": {"report": "# From plugin\n\nbody\n"},
        },
        plugin_stem="x",
    )
    assert md.startswith("# From plugin")
    assert "ignored" not in md


def test_markdown_from_analysis_result_synthesizes_findings() -> None:
    from groket.session.export_bundle import _markdown_from_analysis_result

    md = _markdown_from_analysis_result(
        {
            "analyzer_id": "engine",
            "ok": True,
            "summary": "2 findings",
            "findings": [
                {
                    "title": "Bad edit",
                    "severity": "high",
                    "detail": "Wrong path",
                    "category": "Correctness",
                    "event_indices": [3],
                }
            ],
            "artifacts": {},
        },
        plugin_stem="engine",
    )
    assert "Analysis report — engine" in md
    assert "2 findings" in md
    assert "Bad edit" in md
    assert "Wrong path" in md
    assert "#3" in md
