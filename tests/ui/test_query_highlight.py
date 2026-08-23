"""Catalog search highlighter paints schema tokens in the box."""

from __future__ import annotations

from groket.ui.query_highlight import CatalogQueryHighlighter
from groket.ui.styles import CAUTION, DANGER, SUCCESS


def test_highlighter_colors_closed_value_and_unknown() -> None:
    painted = CatalogQueryHighlighter()("has:goals AND has:gooals")
    styles = {painted.plain[span.start : span.end]: str(span.style) for span in painted.spans}
    assert styles["has:"] == f"bold {CAUTION}"
    assert styles["goals"] == SUCCESS
    assert styles["AND"] == f"bold {CAUTION}"
    assert styles["gooals"] == DANGER


def test_highlighter_splits_has_quantity() -> None:
    painted = CatalogQueryHighlighter()("has:workflows:>=2")
    styles = {painted.plain[span.start : span.end]: str(span.style) for span in painted.spans}
    assert styles["has:"] == f"bold {CAUTION}"
    assert styles["workflows"] == SUCCESS
    assert styles[":>=2"] == SUCCESS


def test_highlighter_leaves_bare_words_unstyled() -> None:
    painted = CatalogQueryHighlighter()("palette")
    assert painted.plain == "palette"
    assert painted.spans == []
