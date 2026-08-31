"""Shared assertions for adapter list Turn status."""

from __future__ import annotations

from pathlib import Path

from anqa.harness.registry import require_adapter


def assert_adapter_turn(ref: Path | str, label: str) -> None:
    """``load_meta`` label and ``list_turn_outcome`` agree on one locator."""
    adapter = require_adapter(ref)
    meta = adapter.load_meta(ref)
    listed = adapter.list_turn_outcome(ref)
    assert meta.list_status_label() == label
    assert listed == (meta.turn_outcome or "").strip()
