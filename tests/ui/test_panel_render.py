"""panel_render empty states and chrome helpers."""

from __future__ import annotations

import pytest
from rich.text import Text

from .pilot_helpers import assert_rich_contains, rich_plain


def test_empty_state_quiet_chrome():
    from groket.ui.panel_render import EMPTY_STATE_CLASS, EmptyState

    empty = EmptyState("No flags yet", id="es")
    assert EMPTY_STATE_CLASS in empty.classes
    assert "No flags yet" in empty._render_body().plain
    assert "┌" not in empty._render_body().plain
    empty.clear_message()
    assert empty._empty_message == ""
    assert "empty-state-hidden" in empty.classes


# ── Rich text helpers ─────────────────────────────────────────────────────

from groket.ui.panel_render import (
    bullet,
    content_block,
    dim_rule,
    format_stamp,
    key_chip,
    keys_rich,
    kv_line,
    list_row,
    looks_like_markdown,
    md_content,
    meta_strip,
    panel_group,
    section_header,
    status_chip,
)
from rich.text import Text as RichText


class TestLooksLikeMarkdown:
    def test_heading(self):
        assert looks_like_markdown("# Title") is True

    def test_fenced_code(self):
        assert looks_like_markdown("some ```code```") is True

    def test_bullet(self):
        assert looks_like_markdown("- item") is True
        assert looks_like_markdown("* item") is True
        assert looks_like_markdown("> quote") is True

    def test_bold(self):
        assert looks_like_markdown("**bold** text") is True

    def test_link(self):
        assert looks_like_markdown("[link](http://x)") is True

    def test_plain_text(self):
        assert looks_like_markdown("just text") is False

    def test_empty(self):
        assert looks_like_markdown("") is False

    def test_newline_heading(self):
        assert looks_like_markdown("abc\n## heading") is True


class TestMdContent:
    def test_empty_shows_placeholder(self):
        r = md_content("")
        assert "empty" in str(r).lower() or isinstance(r, RichText)

    def test_long_text_truncated(self):
        r = md_content("x" * 200_000, max_chars=1000)
        plain = rich_plain(r)
        assert "x" in plain
        assert len(plain) < 200_000

    def test_no_indent(self):
        r = md_content("hello", indent=0)
        assert_rich_contains(r, "hello")

    def test_headings_stay_left(self):
        from groket.ui.panel_render import LeftMarkdown, _LeftHeading

        r = md_content("# Title", indent=0)
        assert isinstance(r, LeftMarkdown)
        assert _LeftHeading.LEVEL_ALIGN["h1"] == "left"


class TestContentBlock:
    def test_markdown_content(self):
        r = content_block("# Title\n\nSome text")
        assert_rich_contains(r, "Title")

    def test_plain_text(self):
        r = content_block("just plain text")
        assert_rich_contains(r, "just plain text")

    def test_long_text_truncated(self):
        r = content_block("x" * 200_000, max_chars=1000)
        plain = rich_plain(r)
        assert "x" in plain
        assert len(plain) < 200_000


class TestSectionHeader:
    def test_contains_title(self):
        t = section_header("My Section")
        assert "My Section" in t.plain

    def test_has_rule(self):
        t = section_header("Test")
        assert "─" in t.plain


def test_format_stamp_short_card_time() -> None:
    assert format_stamp("2026-08-22T03:25:29.924849+00:00") == "Aug 22, 03:25"
    assert format_stamp("2026-01-08T18:02:00Z") == "Jan 8, 18:02"
    assert format_stamp("") == ""
    assert format_stamp("not-a-date") == "not-a-date"


class TestKvLine:
    def test_key_and_value(self):
        t = kv_line("Key", "Value")
        assert "Key" in t.plain
        assert "Value" in t.plain


class TestListRow:
    def test_basic(self):
        t = list_row("item")
        assert "item" in t.plain

    def test_with_meta(self):
        t = list_row("item", meta=" (note)")
        assert "(note)" in t.plain


class TestBullet:
    def test_basic(self):
        t = bullet("item")
        assert "item" in t.plain


class TestStatusChip:
    def test_ok(self):
        t = status_chip("PASS", kind="ok")
        assert "PASS" in t.plain

    def test_bad(self):
        t = status_chip("FAIL", kind="bad")
        assert "FAIL" in t.plain

    def test_unknown(self):
        t = status_chip("???", kind="unknown")
        assert "???" in t.plain

    def test_source(self):
        t = status_chip("nvim", kind="source")
        assert t.plain == "nvim"


class TestKeyChip:
    def test_basic(self):
        t = key_chip("s")
        assert "s" in t.plain

    def test_alias_slash(self):
        t = key_chip("slash")
        assert "/" in t.plain

    def test_ctrl_chord_uses_words(self):
        t = key_chip("ctrl+s")
        assert "Ctrl+S" in t.plain
        assert "^" not in t.plain

    def test_space_named(self):
        t = key_chip("space")
        assert "Space" in t.plain

    def test_empty(self):
        t = key_chip("")
        assert "?" in t.plain


class TestKeysRich:
    def test_inline_keys(self):
        t = keys_rich("`s` select · `space` toggle")
        assert "s" in t.plain
        assert "Space" in t.plain


class TestMetaStrip:
    def test_basic(self):
        t = meta_strip(["a", "b", "c"])
        assert "·" in t.plain

    def test_single(self):
        t = meta_strip(["only"])
        assert "only" in t.plain


class TestDimRule:
    def test_returns_rule(self):
        r = dim_rule()
        assert "─" in rich_plain(r) or "─" in str(r) or rich_plain(r) != ""


class TestPanelGroup:
    def test_empty(self):
        r = panel_group()
        assert isinstance(r, RichText)

    def test_single(self):
        t = RichText("hello")
        r = panel_group(t)
        assert r is t

    def test_multiple(self):
        r = panel_group(RichText("a"), RichText("b"))
        plain = rich_plain(r)
        assert "a" in plain and "b" in plain

    def test_none_filtered(self):
        r = panel_group(None, RichText("a"), None)
        assert_rich_contains(r, "a")


class TestMdContentExceptionFallback:
    def test_markdown_parse_exception(self):
        """Markdown parse exception falls back to plain Text."""
        from unittest.mock import patch

        with patch("groket.ui.panel_render.LeftMarkdown", side_effect=ValueError("bad")):
            r = md_content("# Title")
            assert_rich_contains(r, "Title")


class TestFooterKeyRichStyle:
    def test_with_mock_app(self):
        """_footer_key_rich_style reads CSS vars from a running app."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        css_vars = {"footer-key-foreground": "#aabbcc"}
        app = SimpleNamespace(
            get_css_variables=lambda: css_vars,
            theme="textual-dark",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert "bold" in result
            assert "#aabbcc" in result

    def test_with_accent_fallback(self):
        """accent CSS var is used when footer-key-foreground is missing."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        app = SimpleNamespace(
            get_css_variables=lambda: {"accent": "#112233"},
            theme="",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert "#112233" in result

    def test_with_primary_fallback(self):
        """primary CSS var is used as final colour fallback."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        app = SimpleNamespace(
            get_css_variables=lambda: {"primary": "#334455"},
            theme="dark",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert "#334455" in result

    def test_no_css_vars(self):
        """_footer_key_rich_style returns bold when no CSS vars are set."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        app = SimpleNamespace(
            get_css_variables=lambda: {},
            theme="some-theme",
        )
        with patch("textual.app.App") as mock_app_cls:
            mock_app_cls.get_running_app = lambda: app
            result = _footer_key_rich_style()
            assert result == "bold"

    def test_exception_returns_bold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_footer_key_rich_style returns bold on app detection exception."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from groket.ui.panel_render import _footer_key_rich_style

        # Make get_css_variables raise inside the try block
        app = SimpleNamespace(
            get_css_variables=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            theme="dark",
        )

        class _FakeApp:
            @staticmethod
            def get_running_app() -> SimpleNamespace:
                return app

        with patch("textual.app.App", _FakeApp):
            result = _footer_key_rich_style()
            assert result == "bold"


class TestAppendTipBodyEmpty:
    def test_empty_message_returns_early(self):
        """_append_tip_body is a no-op for empty messages."""
        from groket.ui.panel_render import _append_tip_body

        t = Text()
        _append_tip_body(t, "")
        assert t.plain == ""

        _append_tip_body(t, "   ")
        assert t.plain == ""
