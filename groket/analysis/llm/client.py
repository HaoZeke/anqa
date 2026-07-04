"""Headless Grok CLI client for structured LLM reviews."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ...models import JsonObject, JsonValue
from .schema import REVIEW_FINDINGS_SCHEMA

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GrokStructuredResult:
    """Outcome of a structured headless Grok call."""

    payload: JsonObject | None
    raw: str | None


def find_grok_bin() -> str | None:
    """Locate the ``grok`` executable."""
    for candidate in ("grok", str(Path.home() / ".grok" / "bin" / "grok")):
        found = shutil.which(candidate)
        if found:
            return found
        if Path(candidate).is_file():
            return candidate
    return None


def _as_json_object(value: JsonValue) -> JsonObject | None:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return None


def _looks_like_review(data: JsonObject) -> bool:
    return "findings" in data or "summary" in data or "all_clear" in data


def extract_structured_payload(text: str) -> JsonObject | None:
    """Parse review JSON from headless Grok stdout (envelope or raw)."""
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    candidates: list[str] = [text]
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.I):
        candidates.insert(0, m.group(1).strip())
    brace = text.find("{")
    if brace >= 0:
        end = text.rfind("}")
        if end > brace:
            candidates.append(text[brace : end + 1])

    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        obj: JsonObject = {str(k): v for k, v in data.items()}
        for key in (
            "structuredOutput",
            "structured_output",
            "result",
            "output",
            "response",
            "data",
            "message",
        ):
            inner = obj.get(key)
            if isinstance(inner, dict):
                inner_obj = {str(k): v for k, v in inner.items()}
                if _looks_like_review(inner_obj):
                    return inner_obj
            if isinstance(inner, str):
                try:
                    parsed = json.loads(inner)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    parsed_obj = {str(k): v for k, v in parsed.items()}
                    if _looks_like_review(parsed_obj):
                        return parsed_obj
        if _looks_like_review(obj) and "findings" in obj:
            return obj
        if _looks_like_review(obj) and "structuredOutput" not in obj:
            return obj
        for val in obj.values():
            if isinstance(val, str) and "findings" in val:
                try:
                    parsed = json.loads(val)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return {str(k): v for k, v in parsed.items()}
    return None


class GrokCliClient:
    """Call ``grok`` headless with ``--json-schema`` for structured output."""

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: JsonObject | None = None,
        model: str | None = None,
        effort: str = "medium",
        timeout_sec: int = 600,
    ) -> GrokStructuredResult:
        """Run a single structured completion.

        :param prompt: Full prompt text (already assembled).
        :param schema: JSON Schema object; defaults to review findings schema.
        :param model: Optional ``-m`` model id.
        :param effort: Reasoning effort level.
        :param timeout_sec: Subprocess timeout.
        :returns: Parsed payload and raw stdout (or error text).
        """
        grok_bin = find_grok_bin()
        if grok_bin is None:
            logger.info("grok not found on PATH — skipping LLM review")
            return GrokStructuredResult(payload=None, raw=None)

        schema_obj = schema if schema is not None else REVIEW_FINDINGS_SCHEMA
        schema_json = json.dumps(schema_obj, separators=(",", ":"))
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(prompt)
            prompt_path = Path(f.name)

        try:
            cmd = [
                grok_bin,
                "--prompt-file",
                str(prompt_path),
                "--output-format",
                "json",
                "--json-schema",
                schema_json,
                "--no-memory",
                "--no-plan",
                "--no-subagents",
                "--effort",
                effort,
                "--max-turns",
                "1",
                "--yolo",
            ]
            if model:
                cmd.extend(["-m", model])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=tempfile.gettempdir(),
            )
            raw = (result.stdout or "").strip()
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip()
                err = re.sub(r"\x1b\[[0-9;]*m", "", err)
                logger.warning("grok exited %d: %s", result.returncode, err[:500])
                payload = extract_structured_payload(raw) if raw else None
                if payload is not None:
                    return GrokStructuredResult(payload=payload, raw=raw)
                return GrokStructuredResult(payload=None, raw=err or raw or None)

            payload = extract_structured_payload(raw)
            if payload is None:
                logger.warning("grok review: could not parse JSON from output")
                return GrokStructuredResult(payload=None, raw=raw or None)
            return GrokStructuredResult(payload=payload, raw=raw)
        except subprocess.TimeoutExpired:
            logger.warning("grok review timed out after %ds", timeout_sec)
            return GrokStructuredResult(payload=None, raw=None)
        except OSError:
            logger.warning("grok review failed", exc_info=True)
            return GrokStructuredResult(payload=None, raw=None)
        finally:
            prompt_path.unlink(missing_ok=True)
