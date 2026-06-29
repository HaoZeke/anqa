"""Marshal work onto the Textual app thread safely.

``App.call_from_thread`` **must** run from a worker thread. Calling it on the
app thread raises ``RuntimeError``. Worker callbacks and ``@work`` methods use
:func:`call_ui`; code that may run on either thread should use it too.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

from textual.app import App


def call_ui[R](app: App, callback: Callable[..., R], *args: object, **kwargs: object) -> R | None:
    """Run *callback* on the app thread.

    From a worker: ``call_from_thread``. Already on the app thread: call
    directly. Returns the callback result when invoked inline; ``None`` when
    scheduled via ``call_from_thread`` (async relative to the worker).
    """
    try:
        return app.call_from_thread(callback, *args, **kwargs)
    except RuntimeError:
        return callback(*args, **kwargs)
    except Exception:
        with suppress(Exception):
            return callback(*args, **kwargs)
        return None
