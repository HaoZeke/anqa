"""Shared Textual Pilot synchronisation helpers.

Wait on observable app/widget state; drain the message loop with
``await pilot.pause()`` (no fixed delay). See AGENTS.md §4.5c.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from textual.pilot import Pilot

T = TypeVar("T")

Predicate = Callable[[], bool]
AsyncPredicate = Callable[[], Awaitable[bool]]


async def wait_until(
    pilot: Pilot,
    pred: Predicate,
    *,
    attempts: int = 80,
    description: str = "condition",
) -> None:
    """Poll *pred* until true, pumping the Textual message loop between tries.

    :param pilot: Active :class:`~textual.pilot.PilotualPilot` from ``app.run_test()``.
    :param pred: Synchronous predicate; return True when the scenario is ready.
    :param attempts: Maximum loop iterations (safety bound, not a sleep budget).
    :param description: Included in the failure message when the bound is hit.
    :raises AssertionError: If *pred* is still false after *attempts* turns.
    """
    for _ in range(max(1, attempts)):
        if pred():
            return
        await pilot.pause()
    raise AssertionError(f"timed out waiting for {description} (attempts={attempts})")


async def wait_until_async(
    pilot: Pilot,
    pred: AsyncPredicate,
    *,
    attempts: int = 80,
    description: str = "condition",
) -> None:
    """Like :func:`wait_until` but *pred* is async (rare; prefer sync predicates)."""
    for _ in range(max(1, attempts)):
        if await pred():
            return
        await pilot.pause()
    raise AssertionError(f"timed out waiting for {description} (attempts={attempts})")
