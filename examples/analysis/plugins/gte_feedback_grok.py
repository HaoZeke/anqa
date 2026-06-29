"""LLM-drafted feedback review plugin (example analyzer).

Not part of the installable ``groket`` package. Filename stem = config entry
import name (``gte_feedback_grok``)::

    "analysis": { "plugins": ["gte_feedback_grok:FeedbackReportAnalyzer"] }

Copy into ``~/.groket/plugins/``, or run with
``--config examples/analysis/configs/…`` so ``examples/analysis/plugins/``
is on the plugin search path.

Uses ``AnalyzerInfo.defer=True`` so ``AnalysisService`` runs this in the
second pass with ``prior_findings`` from built-in detectors.
"""

from __future__ import annotations

from typing import Unpack

import logging
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from groket.analysis.base import AnalyzeContext, AnalysisResult, AnalyzerInfo, Finding
from groket.parser import extract_prompt, load_session_meta, parse_timeline
from groket.utils import fmt_duration

logger = logging.getLogger(__name__)

PLUGIN_ID = "feedback"

_EDIT_TOOLS = frozenset({"search_replace", "write_file", "create_file"})

# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@dataclass
class SessionEvidence:
    """Structured evidence extracted from a single trace session."""

    session_id: str
    session_dir: Path
    model_id: str = ""
    task_id: str = ""
    repo: str = ""
    branch: str = ""
    prompt: str = ""
    title: str = ""
    duration: str = ""
    turn_outcome: str = ""
    tool_count: int = 0
    tool_error_count: int = 0
    tool_mix: dict[str, int] = field(default_factory=dict)
    files_edited: list[str] = field(default_factory=list)
    assistant_final: str = ""

def gather_evidence(session_dir: Path) -> SessionEvidence:
    """Build a :class:`SessionEvidence` from parser data."""
    meta = load_session_meta(session_dir)
    timeline = parse_timeline(session_dir)
    prompt = extract_prompt(session_dir)

    tool_counts: Counter[str] = Counter()
    error_count = 0
    files: list[str] = []
    assistant_final = ""

    for ev in timeline:
        if ev.event_type == "tool_call":
            tool_counts[ev.tool_name] += 1
            if ev.tool_name in _EDIT_TOOLS:
                path = ev.raw_input.get("file_path") or ev.raw_input.get("target_file") or ""
                if path and path not in files:
                    files.append(path)
        elif ev.event_type == "tool_result" and ev.is_error:
            error_count += 1
        elif ev.event_type == "assistant":
            assistant_final = ev.content

    duration = fmt_duration(meta.duration_seconds) if meta.duration_seconds else ""

    return SessionEvidence(
        session_id=meta.session_id or session_dir.name,
        session_dir=session_dir,
        model_id=meta.model_id,
        task_id=meta.task_id,
        repo=meta.git_repo,
        branch=meta.git_branch,
        prompt=prompt,
        title=meta.title,
        duration=duration,
        turn_outcome=meta.turn_outcome,
        tool_count=sum(tool_counts.values()),
        tool_error_count=error_count,
        tool_mix=dict(tool_counts.most_common(12)),
        files_edited=files,
        assistant_final=assistant_final,
    )

# ---------------------------------------------------------------------------
# LLM review prompt + invocation
# ---------------------------------------------------------------------------

def build_review_prompt(
    evidence: SessionEvidence,
    findings: list[Finding] | None = None,
) -> str:
    """Build a short LLM prompt from evidence and detector findings."""
    findings = findings or []

    lines = [
        "Here is a trace from a coding agent session. Review it and describe",
        "any actions that could be considered problematic.",
        "",
        "<evidence>",
        f"Session: {evidence.session_id}",
    ]
    if evidence.model_id:
        lines.append(f"Model: {evidence.model_id}")
    if evidence.duration:
        lines.append(f"Duration: {evidence.duration}")
    if evidence.turn_outcome:
        lines.append(f"Outcome: {evidence.turn_outcome}")
    if evidence.repo:
        repo = evidence.repo
        if evidence.branch:
            repo += f" ({evidence.branch})"
        lines.append(f"Repo: {repo}")
    if evidence.prompt:
        lines.extend(["", "Prompt:", evidence.prompt[:2000]])

    mix_str = ", ".join(f"{k}: {v}" for k, v in evidence.tool_mix.items())
    lines.extend([
        "",
        f"Tools: {evidence.tool_count} ({mix_str})",
        f"Errors: {evidence.tool_error_count}",
    ])
    if evidence.files_edited:
        lines.append(f"Files edited: {', '.join(evidence.files_edited[:20])}")

    if findings:
        lines.extend(["", "Detector findings:"])
        for f in sorted(findings, key=lambda f: f.severity):
            detail = f" — {f.detail[:200]}" if f.detail else ""
            lines.append(f"- [{f.severity.value.upper()}] {f.title}{detail}")

    if evidence.assistant_final:
        lines.extend([
            "",
            "Final assistant message (truncated):",
            evidence.assistant_final[:3000],
        ])

    lines.extend([
        "</evidence>",
        "",
        "For each issue, briefly describe:",
        "- What the model did",
        "- Why it's a problem",
        "- What it should have done instead",
        "",
        "If nothing looks problematic, say so. Be concise.",
    ])
    return "\n".join(lines)

def find_grok_bin() -> str | None:
    """Find the ``grok`` executable, or ``None`` if not available."""
    for candidate in ("grok", str(Path.home() / ".grok" / "bin" / "grok")):
        found = shutil.which(candidate)
        if found:
            return found
        if Path(candidate).is_file():
            return candidate
    return None

def run_grok_review(
    prompt: str,
    *,
    model: str | None = None,
    timeout_sec: int = 300,
) -> str | None:
    """Call ``grok`` in headless mode and return the output.

    Returns ``None`` if ``grok`` is not available or the call fails.
    """
    grok_bin = find_grok_bin()
    if grok_bin is None:
        logger.info("grok not found on PATH — skipping LLM review")
        return None

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    ) as f:
        f.write(prompt)
        prompt_path = Path(f.name)

    try:
        cmd = [
            grok_bin,
            "--prompt-file", str(prompt_path),
            "--output-format", "plain",
            "--no-memory",
        ]
        if model:
            cmd.extend(["-m", model])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if result.returncode != 0:
            logger.warning(
                "grok exited %d: %s", result.returncode,
                (result.stderr or "")[:500],
            )
            return None

        output = (result.stdout or "").strip()
        return output if output else None
    except subprocess.TimeoutExpired:
        logger.warning("grok review timed out after %ds", timeout_sec)
        return None
    except Exception:
        logger.warning("grok review failed", exc_info=True)
        return None
    finally:
        prompt_path.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class FeedbackReportAnalyzer:
    """LLM-drafted session review via ``grok`` headless mode."""

    @property
    def info(self) -> AnalyzerInfo:
        return AnalyzerInfo(
            id=PLUGIN_ID,
            name="Feedback Review",
            description="LLM-drafted session review via grok.",
            optional=True,
            defer=True,
        )

    def analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult:
        prior_findings: list[Finding] = kwargs.get("prior_findings", [])
        model: str | None = kwargs.get("feedback_model")

        try:
            evidence = gather_evidence(session_dir)
        except Exception as exc:
            logger.warning("Evidence gathering failed for %s: %s", session_dir.name, exc)
            return AnalysisResult(
                session_id=session_dir.name,
                session_dir=str(session_dir),
                analyzer_id=PLUGIN_ID,
                ok=False,
                error=f"evidence gathering failed: {exc}",
            )

        prompt = build_review_prompt(evidence, findings=prior_findings)
        review = run_grok_review(prompt, model=model)

        artifacts: dict[str, str] = {}
        if review:
            artifacts["report"] = review

        summary_parts = [f"{len(prior_findings)} finding(s)"]
        summary_parts.append("LLM review ok" if review else "no LLM review")

        return AnalysisResult(
            session_id=evidence.session_id,
            session_dir=str(session_dir),
            analyzer_id=PLUGIN_ID,
            ok=True,
            summary=", ".join(summary_parts),
            artifacts=artifacts,
        )

