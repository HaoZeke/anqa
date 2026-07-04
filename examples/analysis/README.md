# Analysis plugin examples

Each ``plugins/*.py`` file defines an ``Analyzer`` class. Config entries are
always ``module_stem:ClassName`` (filename without ``.py`` = module stem when
the directory is on ``sys.path``).

| File | Config entry |
|------|----------------|
| `token_tracker.py` | `token_tracker:TokenTrackerAnalyzer` |
| `security_scanner.py` | `security_scanner:SecurityScannerAnalyzer` |
| `latency_profiler.py` | `latency_profiler:LatencyProfilerAnalyzer` |
| `diff_reviewer.py` | `diff_reviewer:DiffReviewerAnalyzer` |
| `conversation_flow.py` | `conversation_flow:ConversationFlowAnalyzer` |
| `session_health.py` | `session_health:SessionHealthAnalyzer` |
| `session_event_count.py` | `session_event_count:SessionEventCountAnalyzer` |
| `gte_feedback_grok.py` | `gte_feedback_grok:FeedbackReportAnalyzer` |
| `llm_instruction_check.py` | `llm_instruction_check:InstructionCheckAnalyzer` |

## LLM review plugins

Use :class:`~groket.analysis.llm.LlmReviewAnalyzer` for structured Grok reviews:

1. Subclass and implement ``build_instructions(pack)`` with your rubric only.
2. Core attaches operator turns, timeline digest, and runtime fairness
   (permission mode, sandbox, non-interactive, …).
3. Results: **Findings tab** rows with timeline links, and **Report tab**
   via ``artifacts["report"]`` (full did / should / why).

See ``gte_feedback_grok.py`` (full multi-turn feedback) and
``llm_instruction_check.py`` (minimal example).

Do **not** embed magic tags like ``<runtime_context>`` in instructions — use
``pack`` fields if you need dynamic text (e.g. ``pack.turn_count``).

## Install into your profile

```bash
mkdir -p ~/.groket/plugins
cp examples/analysis/plugins/gte_feedback_grok.py ~/.groket/plugins/
# optional minimal example:
cp examples/analysis/plugins/llm_instruction_check.py ~/.groket/plugins/
```

Enable in ``~/.groket/config.json`` (merge with your existing file):

```json
{
  "analysis": {
    "plugins": ["gte_feedback_grok:FeedbackReportAnalyzer"]
  }
}
```

Or scaffold + register:

```bash
uv run groket gen plugin session_event_count --register
```

## Run sample configs from the repo

``configs/`` point at the plugins in ``plugins/`` via the config directory
search path (no copy required):

```bash
uv run groket --config examples/analysis/configs/all-plugins.json
uv run groket runs/traces --config examples/analysis/configs/security-only.json
```

Re-run analysis (``a``) after switching configs.
