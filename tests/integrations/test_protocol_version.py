"""Control protocolVersion is semver; same major is compatible."""

from __future__ import annotations

from groket.integrations.control import (
    PROTOCOL_VERSION,
    parse_protocol_version,
    protocol_compatible,
)


def test_protocol_version_is_semver() -> None:
    parsed = parse_protocol_version(PROTOCOL_VERSION)
    assert parsed is not None
    assert parsed[0] >= 1


def test_parse_protocol_version_rejects_int_and_junk() -> None:
    assert parse_protocol_version(1) is None
    assert parse_protocol_version("1") is None
    assert parse_protocol_version("1.0") is None
    assert parse_protocol_version("v1.0.0") is None
    assert parse_protocol_version("1.0.0-beta") is None


def test_protocol_compatible_same_major() -> None:
    assert protocol_compatible("1.0.0", "1.0.0")
    assert protocol_compatible("1.2.0", "1.0.0")
    assert protocol_compatible("1.0.0", "1.9.3")
    assert not protocol_compatible("2.0.0", "1.0.0")
    assert not protocol_compatible("0.1.0", "1.0.0")
    assert not protocol_compatible(1, "1.0.0")
    assert not protocol_compatible("nope", "1.0.0")
