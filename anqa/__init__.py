"""anqa — inspect coding-agent harness sessions.

* Root: ``models``, ``config``, ``paths``, ``constants``, ``utils``, ``cli``
* ``session/`` — catalog, notes helpers, usage, diffs, launch-record readers
* ``harness/`` — adapter protocol
* ``ui/`` — Textual presentation

Data flow: harness / models → session → ui.
"""

from __future__ import annotations

__version__ = "0.1.0"
