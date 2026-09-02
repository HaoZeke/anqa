"""Tests for fzf-style fuzzy matching."""

from __future__ import annotations

from anqa.ui.fuzzy import (
    _char_bonus,
    _fzf_score,
    filter_diff_hunks,
    first_match_line,
    fzf_match,
    iter_diff_hits,
    mark_unified_hit,
    split_tokens,
)
from rich.text import Text

# ── _char_bonus ──────────────────────────────────────────────────────────


class TestCharBonus:
    def test_start_of_string(self):
        assert _char_bonus(None, "a") == 10  # _BONUS_BOUNDARY_WHITE

    def test_slash_boundary(self):
        assert _char_bonus("/", "m") == 8  # _BONUS_BOUNDARY

    def test_dash_boundary(self):
        assert _char_bonus("-", "x") == 8

    def test_space_boundary(self):
        assert _char_bonus(" ", "w") == 10  # _BONUS_BOUNDARY_WHITE

    def test_camel_case(self):
        assert _char_bonus("a", "B") == 10  # _BONUS_CAMEL

    def test_no_bonus(self):
        assert _char_bonus("a", "b") == 0


# ── _fzf_score ───────────────────────────────────────────────────────────


class TestFzfScore:
    def test_exact_match(self):
        score, positions = _fzf_score("abc", "abc")
        assert score > 0
        assert positions == [0, 1, 2]

    def test_subsequence_match(self):
        score, positions = _fzf_score("ac", "abc")
        assert score > 0
        assert positions == [0, 2]

    def test_no_match(self):
        score, positions = _fzf_score("xyz", "abc")
        assert score == 0
        assert positions == []

    def test_empty_query(self):
        score, positions = _fzf_score("", "abc")
        assert score == 0
        assert positions == []

    def test_query_longer_than_candidate(self):
        score, positions = _fzf_score("abcdef", "abc")
        assert score == 0
        assert positions == []

    def test_boundary_match_scores_higher(self):
        boundary_score, _ = _fzf_score("m", "src/main.py")
        mid_score, _ = _fzf_score("r", "src/main.py")
        assert boundary_score > mid_score


# ── fzf_match ────────────────────────────────────────────────────────────


class TestFzfMatch:
    def test_direct_match_returns_highlighted_text(self):
        score, text = fzf_match("abc", "xabcx")
        assert score > 0
        assert isinstance(text, Text)
        assert str(text) == "xabcx"

    def test_token_substring_fallback(self):
        # "longtoken" can't match as subsequence in "prefix_longtoken",
        # but the token "longtoken" IS a substring → fallback fires.
        score, text = fzf_match("xx-longtoken", "prefix_longtoken_suffix")
        assert score > 0
        assert isinstance(text, Text)

    def test_no_match_returns_zero(self):
        score, text = fzf_match("zzz", "abc")
        assert score == 0
        assert str(text) == "abc"


# ── split_tokens ─────────────────────────────────────────────────────────


class TestFilterDiffHunks:
    def test_empty_query_keeps_all(self) -> None:
        hunks = [("a.py", "+foo"), ("b.py", "+bar")]
        hits = filter_diff_hunks("", hunks)
        assert [h[0] for h in hits] == ["a.py", "b.py"]
        assert all(h[2] is None and h[3] == "path" for h in hits)

    def test_path_query_keeps_matching_file(self) -> None:
        hunks = [("src/app.py", "+alpha"), ("lib/util.py", "+beta")]
        hits = filter_diff_hunks("app.py", hunks)
        assert [h[0] for h in hits] == ["src/app.py"]
        assert hits[0][3] == "path"
        assert hits[0][2] is None

    def test_body_query_keeps_hunk_and_line(self) -> None:
        hunks = [
            ("a.py", "@@\n-old\n+alpha unique\n"),
            ("b.py", "@@\n-old\n+other\n"),
        ]
        hits = filter_diff_hunks("unique", hunks)
        assert [h[0] for h in hits] == ["a.py"]
        assert hits[0][3] == "body"
        assert hits[0][2] == first_match_line("unique", hunks[0][1])
        assert hits[0][2] is not None
        marked = mark_unified_hit(hunks[0][1], hits[0][2])
        assert any(line.startswith("> ") and "unique" in line for line in marked.splitlines())


def test_iter_diff_hits_collects_every_matching_line() -> None:
    hunks = [
        ("a.py", "@@\n-old\n+needle one\n+keep\n+needle two\n"),
        ("b.py", "@@\n+other\n"),
        ("c.py", "@@\n+needle three\n"),
    ]
    hits = iter_diff_hits("needle", hunks)
    assert hits == [
        ("a.py", 2),
        ("a.py", 4),
        ("c.py", 1),
    ]
    assert iter_diff_hits("b.py", hunks) == [("b.py", None)]
    assert iter_diff_hits("", hunks) == []


class TestSplitTokens:
    def test_splits_on_delimiters(self):
        assert split_tokens("foo/bar-baz_qux") == ["foo", "bar", "baz", "qux"]

    def test_filters_short_tokens(self):
        tokens = split_tokens("a/bb/c")
        assert "a" not in tokens
        assert "c" not in tokens
        assert tokens == ["bb"]
