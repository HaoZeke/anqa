"""Rich highlighter for catalog search tokens."""

from __future__ import annotations

from rich.highlighter import Highlighter
from rich.text import Text

from ..session.query import highlight_query_spans
from .styles import QUERY_SPAN_STYLE


class CatalogQueryHighlighter(Highlighter):
    """Color known ``field:`` tokens, closed values, and ``AND`` / ``OR`` / ``NOT``."""

    def highlight(self, text: Text) -> None:
        """Apply :func:`~anqa.session.query.highlight_query_spans` in place.

        :param text: Current search box value.
        """
        for span in highlight_query_spans(text.plain):
            text.stylize(QUERY_SPAN_STYLE[span.kind], span.start, span.end)
