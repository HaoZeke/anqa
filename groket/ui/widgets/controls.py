"""Shared toolbar / filter chrome — exclusive filters use Select.

Standard pattern (sessions list, Timeline **Filter**):

- Horizontal bar with classes ``filter-bar`` + bold ``filter-view-label``
- One or more **Select** widgets for exclusive modes (All / …) — shared styling
  in ``app.tcss`` (``#timeline-view-select``, ``.field-select``)
- Timeline search is a second full-width row under the picks (same as the HUD)
- Live Tail is a compact label + switch on the trailing end of the picks row

Form field booleans still use full-width ``Checkbox`` (persona editor).
Do **not** use Button chips or ad-hoc multi-checkbox rows for
section filters.
"""

from __future__ import annotations

FILTER_BAR_CLASS = "filter-bar"
FILTER_LABEL_CLASS = "filter-view-label"
