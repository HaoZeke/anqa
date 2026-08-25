"""Unit tests for Select helpers."""

from __future__ import annotations

from groket.ui.forms import (
    select_is_blank,
    select_null,
    select_value_str,
    selection_list_selected_ids,
)


class TestSelectValueStr:
    def test_normal_str(self) -> None:
        assert select_value_str("a") == "a"

    def test_none_returns_default(self) -> None:
        assert select_value_str(None, default="d") == "d"

    def test_int_coerced(self) -> None:
        assert select_value_str(3) == "3"

    def test_bool_returns_default(self) -> None:
        assert select_value_str(True, default="d") == "d"
        assert select_value_str(False, default="d") == "d"

    def test_none_str_returns_default(self) -> None:
        assert select_value_str("None", default="d") == "d"

    def test_false_str_returns_default(self) -> None:
        assert select_value_str("False", default="d") == "d"

    def test_empty_str_returns_default(self) -> None:
        assert select_value_str("", default="d") == "d"

    def test_no_selection_sentinel(self) -> None:
        class NoSelection:
            pass

        assert select_value_str(NoSelection(), default="x") == "x"

    def test_select_null_and_is_blank(self) -> None:
        from textual.widgets import Select

        assert select_is_blank(None)
        assert select_is_blank(False)
        assert select_is_blank(select_null())
        null = getattr(Select, "NULL", None)
        if null is not None:
            assert select_is_blank(null)
            assert select_null() is null
        assert not select_is_blank("medium")
        assert select_value_str(select_null(), default="") == ""


class TestSelectionListSelectedIds:
    def test_basic(self) -> None:
        class SL:
            selected = ["a", "b"]

        assert selection_list_selected_ids(SL()) == ["a", "b"]  # type: ignore[arg-type]

    def test_dedup(self) -> None:
        class SL:
            selected = ["a", "a", "b"]

        assert selection_list_selected_ids(SL()) == ["a", "b"]  # type: ignore[arg-type]

    def test_empty(self) -> None:
        class SL:
            selected: list[str] = []

        assert selection_list_selected_ids(SL()) == []  # type: ignore[arg-type]

    def test_exception_handling(self) -> None:
        class BadSL:
            @property
            def selected(self):
                raise RuntimeError("boom")

        assert selection_list_selected_ids(BadSL()) == []  # type: ignore[arg-type]
