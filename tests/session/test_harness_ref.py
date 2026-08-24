"""SessionRef parse / format."""

from __future__ import annotations

from pathlib import Path

from groket.harness.ref import SessionRef, parse_session_ref_string
from groket.harness.registry import resolve_session_ref


def test_parse_harness_id() -> None:
    assert parse_session_ref_string("opencode:ses_abc") == ("opencode", "ses_abc")
    assert parse_session_ref_string("grok:019f-uuid") == ("grok", "019f-uuid")


def test_parse_rejects_paths() -> None:
    assert parse_session_ref_string("/tmp/opencode:weird") is None
    assert parse_session_ref_string("~/opencode.db") is None
    assert parse_session_ref_string("unknown:ses") is None
    assert parse_session_ref_string("") is None


def test_grok_ref_string_is_directory() -> None:
    ref = SessionRef(
        harness="grok",
        session_id="sid",
        origin="work",
        locator=Path("/tmp/sid"),
    )
    assert ref.ref_string().endswith("sid")


def test_opencode_ref_string_is_harness_id() -> None:
    ref = SessionRef(
        harness="opencode",
        session_id="ses_1",
        origin="host",
        locator=Path("/tmp/opencode.db"),
    )
    assert ref.ref_string() == "opencode:ses_1"
    assert ref.overlay_dir().parts[-2:] == ("opencode", "ses_1")


def test_resolve_unknown_harness_id_is_none() -> None:
    assert resolve_session_ref("opencode:ses_does_not_exist") is None
    assert resolve_session_ref("") is None
