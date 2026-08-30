"""Tests for shared utilities."""

from __future__ import annotations

import time

import pytest
from anqa.utils import (
    collapse_blank_lines,
    fmt_context_usage,
    fmt_duration,
    fmt_local_card,
    fmt_local_created,
    fmt_local_hms,
    fmt_token_count,
    widget_id,
)


class TestFmtDuration:
    def test_subsecond(self):
        assert fmt_duration(0.5) == "<1s"
        assert fmt_duration(0) == "<1s"

    def test_seconds(self):
        assert fmt_duration(1) == "1s"
        assert fmt_duration(59) == "59s"

    def test_minutes(self):
        assert fmt_duration(60) == "1m00s"
        assert fmt_duration(90) == "1m30s"
        assert fmt_duration(155) == "2m35s"

    def test_hours(self):
        assert fmt_duration(3600) == "1h00m"
        assert fmt_duration(3661) == "1h01m"
        assert fmt_duration(7200) == "2h00m"

    def test_fmt_token_count(self):
        assert fmt_token_count(500) == "500"
        assert fmt_token_count(178996) == "179k"
        assert fmt_token_count(500000) == "500k"
        assert fmt_token_count(1_200_000) == "1.2M"

    def test_fmt_context_usage(self):
        assert fmt_context_usage(35, 178996, 500000) == "35% (178,996 / 500,000)"
        assert "35%" in fmt_context_usage(35, 178996, 500000, compact=True)
        assert fmt_context_usage(None, None, None) == ""
        assert fmt_context_usage(12) == "12%"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset is Unix")
class TestLocalStamps:
    def test_card_and_created_use_host_zone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TZ", "America/Los_Angeles")
        time.tzset()
        assert fmt_local_card("2026-08-08T18:02:00Z") == "Aug 8, 11:02"
        assert fmt_local_created("2026-08-08T18:02:00.123Z") == "2026-08-08 11:02:00"
        ts = 1786212120  # 2026-08-08 18:02:00 UTC
        assert fmt_local_hms(ts) == "11:02:00"

    def test_unparsed_iso_stays(self) -> None:
        assert fmt_local_card("") == ""
        assert fmt_local_card("not-a-date") == "not-a-date"
        assert fmt_local_created("already plain") == "already plain"


class TestCollapseBlankLines:
    def test_no_blanks(self):
        assert collapse_blank_lines("a\nb\nc") == "a\nb\nc"

    def test_double_blank_preserved(self):
        assert collapse_blank_lines("a\n\nb") == "a\n\nb"

    def test_triple_collapsed(self):
        assert collapse_blank_lines("a\n\n\nb") == "a\n\nb"

    def test_many_blanks(self):
        result = collapse_blank_lines("a\n\n\n\n\nb")
        assert "\n\n\n" not in result
        assert result == "a\n\nb"


from anqa.utils import strip_control_chars


class TestWidgetId:
    def test_model_effort_colon_removed(self) -> None:
        assert ":" not in widget_id("anqa-2bffe270c1a3-zingster:hig")
        assert widget_id("anqa-2bffe270c1a3-zingster:hig").startswith("anqa-")

    def test_leading_digit_prefixed(self) -> None:
        assert not widget_id("9abc").startswith("9")
        assert widget_id("9abc") == "n-9abc"

    def test_empty_fallback(self) -> None:
        assert widget_id("", fallback="x") == "x"


class TestStripControlChars:
    def test_empty_string(self):
        assert strip_control_chars("") == ""

    def test_plain_text_unchanged(self):
        assert strip_control_chars("hello world") == "hello world"

    def test_ansi_csi_removed(self):
        assert strip_control_chars("\x1b[31mred\x1b[0m") == "red"

    def test_ansi_osc_removed(self):
        assert strip_control_chars("\x1b]8;;http://example.com\x07link\x1b]8;;\x07") == "link"

    def test_c0_controls_removed_except_tab_newline(self):
        result = strip_control_chars("a\x00b\x01c\td\ne")
        assert result == "abc\td\ne"

    def test_mixed_escapes(self):
        result = strip_control_chars("\x1b[1;32mOK\x1b[0m\x00\x01done")
        assert result == "OKdone"


class TestNormalizeMaxTurns:
    def test_default(self) -> None:
        from anqa.constants import DEFAULT_MAX_TURNS, normalize_max_turns

        assert DEFAULT_MAX_TURNS == 50
        assert normalize_max_turns(None) == 50
        assert normalize_max_turns(True) == 50  # bool is not an int count
        assert normalize_max_turns("") == 50
        assert normalize_max_turns("nope") == 50
        assert normalize_max_turns(0) == 50
        assert normalize_max_turns(-1) == 50
        assert normalize_max_turns([]) == 50  # unsupported type

    def test_valid(self) -> None:
        from anqa.constants import normalize_max_turns

        assert normalize_max_turns(1) == 1
        assert normalize_max_turns("75") == 75
        assert normalize_max_turns(200) == 200
        assert normalize_max_turns(12.9) == 12
