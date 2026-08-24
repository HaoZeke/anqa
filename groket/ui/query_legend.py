"""Search-box tooltip: a Rich token table for the current list."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from groket.integrations.control_contract import list_query_help_intro, list_query_help_pairs


def search_tooltip(scope: str) -> Group:
    """Token | meaning table for ``Input.tooltip``.

    :param scope: ``catalog``, ``turns``, or ``timeline``.
    """
    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(justify="right", no_wrap=True, style="bold")
    table.add_column(justify="left", style="dim")
    for label, body in list_query_help_pairs(scope):
        table.add_row(label, body)
    return Group(Text(list_query_help_intro(scope), style="dim"), table)
