"""Async condition waits for non-Textual tests (control owner, FS, pools).

Poll a predicate until true. The short interval is only a pump delay, not a
product-timer fudge (contrast ``asyncio.sleep(DEBOUNCE + 0.6)``).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable

Predicate = Callable[[], bool | Awaitable[bool]]


async def wait_until(
    pred: Predicate,
    *,
    timeout: float = 15.0,
    interval: float = 0.05,
    description: str = "condition",
) -> None:
    """Await *pred* becoming true within *timeout* seconds.

    :param pred: Sync or async zero-arg predicate.
    :param timeout: Wall-clock bound for the wait.
    :param interval: Delay between failed polls (not a product debounce).
    :param description: Included in the timeout assertion message.
    :raises AssertionError: When *timeout* elapses with *pred* still false.
    """
    deadline = time.monotonic() + max(0.01, timeout)
    while time.monotonic() < deadline:
        result = pred()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out waiting for {description} (timeout={timeout}s)")


def wait_until_sync(
    pred: Callable[[], bool],
    *,
    timeout: float = 15.0,
    interval: float = 0.05,
    description: str = "condition",
) -> None:
    """Blocking poll until *pred* is true (subprocess / thread helpers)."""
    deadline = time.monotonic() + max(0.01, timeout)
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {description} (timeout={timeout}s)")
