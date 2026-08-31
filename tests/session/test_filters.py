"""Saved filters: holes, expand, and the on-disk store."""

from __future__ import annotations

import pytest
from anqa.filters import (
    FilterHole,
    SavedFilter,
    expand,
    holes,
    load_filters,
    remove_filter,
    upsert_filter,
)


def test_holes_choice_and_text() -> None:
    found = holes("is:complete AND harness:{grok,claude} AND in:?")
    assert found == [
        FilterHole("harness", "choice", ("grok", "claude")),
        FilterHole("in", "text", ()),
    ]


def test_holes_trim_choice_spaces() -> None:
    found = holes("harness:{ grok , claude }")
    assert found == [FilterHole("harness", "choice", ("grok", "claude"))]


def test_holes_empty_braces_are_not_a_choice() -> None:
    assert holes("harness:{}") == []


def test_expand_fills_holes() -> None:
    q = "is:complete AND harness:{grok,claude} AND in:?"
    assert (
        expand(q, {"harness": "grok", "in": "~/src"}) == "is:complete AND harness:grok AND in:~/src"
    )


def test_expand_quotes_spaces() -> None:
    assert expand("in:?", {"in": "my dir"}) == 'in:"my dir"'


def test_expand_missing_answer_raises() -> None:
    with pytest.raises(ValueError, match="harness"):
        expand("harness:{grok,claude}", {})


def test_store_round_trip() -> None:
    row = SavedFilter("Awaiting notes", "catalog", "has:note AND is:awaiting")
    upsert_filter(row)
    loaded = load_filters()
    assert loaded == [row]
    upsert_filter(SavedFilter("Awaiting notes", "catalog", "has:note"))
    assert load_filters()[0].query == "has:note"
    assert remove_filter("Awaiting notes", "catalog") is True
    assert load_filters() == []
    assert remove_filter("Awaiting notes", "catalog") is False


def test_store_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        upsert_filter(SavedFilter("  ", "catalog", "has:note"))


def test_store_rejects_bad_scope() -> None:
    with pytest.raises(ValueError, match="scope"):
        upsert_filter(SavedFilter("x", "diff", "has:note"))


def test_store_rejects_long_name() -> None:
    with pytest.raises(ValueError, match="name"):
        upsert_filter(SavedFilter("n" * 81, "catalog", "has:note"))


def test_filters_for_scope_sorts_names(tmp_path) -> None:
    from anqa.filters import filters_for_scope

    dest = tmp_path / "filters.toml"
    upsert_filter(SavedFilter("Zebra", "catalog", "has:note"), path=dest)
    upsert_filter(SavedFilter("alpha", "catalog", "is:awaiting"), path=dest)
    upsert_filter(SavedFilter("tools", "timeline", "is:tool"), path=dest)
    names = [row.name for row in filters_for_scope("catalog", path=dest)]
    assert names == ["alpha", "Zebra"]


def test_load_skips_bad_rows(tmp_path) -> None:
    dest = tmp_path / "filters.toml"
    dest.write_text(
        'filter = [{name = "x", scope = "catalog"}, "nope"]\n',
        encoding="utf-8",
    )
    assert load_filters(dest) == []


def test_load_missing_and_malformed(tmp_path) -> None:
    missing = tmp_path / "nope.toml"
    assert load_filters(missing) == []
    dest = tmp_path / "filters.toml"
    dest.write_text("not = toml [", encoding="utf-8")
    assert load_filters(dest) == []
    dest.write_text('name = "x"\n', encoding="utf-8")
    assert load_filters(dest) == []


def test_saved_filter_mapping_includes_holes() -> None:
    row = SavedFilter("Harness", "catalog", "harness:{grok,claude} AND in:?")
    payload = row.mapping()
    assert payload["name"] == "Harness"
    assert payload["holes"] == [
        {"field": "harness", "kind": "choice", "choices": ["grok", "claude"]},
        {"field": "in", "kind": "text", "choices": []},
    ]


def test_holes_keep_document_order() -> None:
    found = holes("in:? AND harness:{grok,claude}")
    assert [h.field for h in found] == ["in", "harness"]
