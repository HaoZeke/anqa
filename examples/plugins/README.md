# Example analysis plugins

Flat `*.py` files. Config is always **`module:ClassName`**.

| File | Config |
|------|--------|
| `token_tracker.py` | `token_tracker:TokenTrackerAnalyzer` |
| `security_scanner.py` | `security_scanner:SecurityScannerAnalyzer` |
| `latency_profiler.py` | `latency_profiler:LatencyProfilerAnalyzer` |
| `diff_reviewer.py` | `diff_reviewer:DiffReviewerAnalyzer` |
| `conversation_flow.py` | `conversation_flow:ConversationFlowAnalyzer` |
| `session_health.py` | `session_health:SessionHealthAnalyzer` |

```bash
uv run groket --config examples/plugins/config-all-plugins.json
```

Product plugin: `plugins/gte_feedback_grok.py` → `gte_feedback_grok:FeedbackReportAnalyzer`.

## Important: pass the example config into the TUI

```bash
uv run groket runs/traces --config examples/plugins/config-all-plugins.json
```

Without `--config`, groket uses `~/.groket/config.json` (e.g. feedback only) and
you will not see example plugin findings. Re-run analysis (`a`) after switching configs.

---

For **detector + rule + tasks** scaffolds and a walkthrough, see
[`examples/extensions/README.md`](../extensions/README.md) and
`uv run groket gen --help`.

