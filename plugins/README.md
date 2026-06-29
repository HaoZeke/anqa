# External analysis plugins

One `.py` file per plugin. **Config must name the class** — there is no other entry style.

| File | Config |
|------|--------|
| `plugins/gte_feedback_grok.py` | `"gte_feedback_grok:FeedbackReportAnalyzer"` |

```python
# plugins/my_tool.py — define the class only (no register(), no ANALYZER=)
class MyAnalyzer:
    ...
```

```json
{ "analysis": { "plugins": ["my_tool:MyAnalyzer"] } }
```

Groket adds `plugins/` to `sys.path`, imports `MyAnalyzer`, instantiates it, registers it.
