"""Host environment checks (config home, catalog, HUD seat) for self-test."""

from __future__ import annotations

from .self_test import CheckResult, SelfTestReport, run_self_test

__all__ = ["CheckResult", "SelfTestReport", "run_self_test"]
