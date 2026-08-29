"""SessionRef parse / format."""

from __future__ import annotations

from pathlib import Path

from anqa.harness.ref import SessionRef, parse_session_ref_string
from anqa.harness.registry import resolve_session_ref


def test_parse_harness_id() -> None:
    assert parse_session_ref_string("grok:019f-uuid") == ("grok", "019f-uuid")


def test_parse_rejects_paths() -> None:
    assert parse_session_ref_string("/tmp/grok:weird") is None
    assert parse_session_ref_string("~/store.db") is None
    assert parse_session_ref_string("unknown:ses") is None
    assert parse_session_ref_string("") is None


def test_directory_ref_string_is_path(tmp_path: Path) -> None:
    loc = tmp_path / "sid"
    loc.mkdir()
    ref = SessionRef(
        harness="grok",
        session_id="sid",
        origin="work",
        locator=loc,
    )
    assert Path(ref.ref_string()) == loc.resolve()
    assert ref.overlay_dir() == loc


def test_file_locator_ref_string_is_harness_id(tmp_path: Path) -> None:
    store = tmp_path / "store.db"
    store.write_bytes(b"")
    ref = SessionRef(
        harness="demo",
        session_id="ses_1",
        origin="host",
        locator=store,
    )
    assert ref.ref_string() == "demo:ses_1"
    assert ref.overlay_dir().parts[-2:] == ("demo", "ses_1")


def test_resolve_unknown_harness_id_is_none() -> None:
    assert resolve_session_ref("unknown:ses_does_not_exist") is None
    assert resolve_session_ref("") is None


def test_require_adapter_accepts_harness_ref_as_path() -> None:
    """Browser/control pass catalog path through ``Path``; that must still bind."""
    from anqa.harness.registry import require_adapter

    raw = "opencode:ses_fb126f42fffebvoGOuSoFc457J"
    assert require_adapter(raw).id == "opencode"
    assert require_adapter(Path(raw)).id == "opencode"
