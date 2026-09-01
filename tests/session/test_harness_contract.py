"""Every registered adapter declares identity and a owning test."""

from __future__ import annotations

from pathlib import Path

from anqa.harness.ref import HARNESS_IDS
from anqa.harness.registry import adapter_for, adapters


def test_registered_adapters_declare_support() -> None:
    found = list(adapters())
    assert found
    ids = [item.id for item in found]
    assert set(ids) == set(HARNESS_IDS)
    for item in found:
        assert item.id in HARNESS_IDS
        assert item.product.strip()
        assert item.supported_version.strip()
        assert (Path("tests/session") / f"test_harness_{item.id}.py").is_file()


def test_adapter_for_path_returns_matching_adapter(tmp_path: Path) -> None:
    sd = tmp_path / "sess-factory"
    sd.mkdir()
    (sd / "summary.json").write_text("{}", encoding="utf-8")
    item = adapter_for(sd)
    assert item is not None
    assert item.id == "grok"
    assert adapter_for(tmp_path / "missing") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert adapter_for(empty) is None
