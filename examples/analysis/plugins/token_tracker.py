"""Token & cost tracker — estimates API usage per session from trace data.

Parses tool calls, message content, and model IDs from updates.jsonl / signals.json
to produce per-session token estimates and approximate costs.

Config:
    "plugins": ["token_tracker:TokenTrackerAnalyzer"]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Unpack

from groket.analysis.base import AnalysisResult, AnalyzeContext, AnalyzerInfo, Finding
from groket.models import Severity
from groket.parser import load_session_meta, parse_timeline, parse_tool_calls

logger = logging.getLogger(__name__)


MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "claude-sonnet": (0.003, 0.015),
    "claude-opus": (0.015, 0.075),
}
DEFAULT_PRICING = (0.003, 0.015)


CHARS_PER_TOKEN = 4

class TokenTrackerAnalyzer:
    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id="token-tracker",
            name="Token & Cost Tracker",
            description="Estimates token usage and cost from trace event sizes",
            optional=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        meta = load_session_meta(session_dir)
        timeline = parse_timeline(session_dir)
        tool_calls = parse_tool_calls(session_dir)

        if not timeline and not tool_calls:
            return AnalysisResult(
                session_id=session_dir.name,
                analyzer_id="token-tracker",
                ok=True,
                summary="No trace data found",
            )

        model_id = meta.model_id or ""

        input_chars = 0
        output_chars = 0
        for ev in timeline:
            char_count = len(ev.content) if isinstance(ev.content, str) else 0
            if ev.event_type == "user":
                input_chars += char_count
            elif ev.event_type in ("assistant", "thought"):
                output_chars += char_count
            elif ev.event_type == "tool_result":
                input_chars += char_count


        for tc in tool_calls:
            output_chars += sum(len(str(v)) for v in tc.raw_input.values())
            input_chars += len(tc.result_content)

        input_tokens = input_chars // CHARS_PER_TOKEN
        output_tokens = output_chars // CHARS_PER_TOKEN
        total_tokens = input_tokens + output_tokens

        in_price, out_price = DEFAULT_PRICING
        for key, pricing in MODEL_PRICING.items():
            if key in model_id.lower():
                in_price, out_price = pricing
                break

        cost = (input_tokens / 1000 * in_price) + (output_tokens / 1000 * out_price)

        findings: list[Finding] = []
        if total_tokens > 100_000:
            findings.append(Finding(
                id="high-token-usage",
                plugin_id="token-tracker",
                severity=Severity.HIGH,
                title=f"Very high token usage: ~{total_tokens:,} tokens (~${cost:.4f})",
                detail=f"Input: ~{input_tokens:,}, Output: ~{output_tokens:,}",
                category="Cost",
            ))
        elif total_tokens > 50_000:
            findings.append(Finding(
                id="elevated-token-usage",
                plugin_id="token-tracker",
                severity=Severity.MEDIUM,
                title=f"Elevated token usage: ~{total_tokens:,} tokens (~${cost:.4f})",
                detail=f"Input: ~{input_tokens:,}, Output: ~{output_tokens:,}",
                category="Cost",
            ))

        return AnalysisResult(
            session_id=session_dir.name,
            analyzer_id="token-tracker",
            ok=True,
            findings=findings,
            summary=f"~{total_tokens:,} tokens, ~${cost:.4f} ({model_id or 'unknown'})",
            extras={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "tool_calls": len(tool_calls),
                "estimated_cost_usd": round(cost, 6),
                "model_id": model_id,
            },
        )

