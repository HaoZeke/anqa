"""Every registered adapter declares identity and a owning test."""

from __future__ import annotations

from pathlib import Path

from groket.harness.ref import HARNESS_IDS
from groket.harness.registry import adapters


def test_registered_adapters_declare_support() -> None:
    found = list(adapters())
    assert found
    ids = [item.id for item in found]
    assert ids[0] == "grok"
    for item in found:
        assert item.id in HARNESS_IDS
        assert item.product.strip()
        assert item.supported_version.strip()
        assert (Path("tests/session") / f"test_harness_{item.id}.py").is_file()
