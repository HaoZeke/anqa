# User extensions (examples)

Extend groket **without editing package source**. Copy these samples into
`~/.groket/…`, or generate stubs with `uv run groket gen …` and adapt them.

| Kind | Example files | Install location | Activate |
|------|---------------|------------------|----------|
| **Detector** | `detectors/repeated_shell_cd.py` | `~/.groket/detectors/` | Loaded automatically on engine start |
| **Rule** | `rules/repeated-shell-cd.yaml` | `~/.groket/rules/` | Merged with bundled `groket/config/rules.yaml` (same `id` replaces) |
| **Analysis plugin** | `plugins/session_event_count.py` | `~/.groket/plugins/` | List in `~/.groket/config.json` → `analysis.plugins` |
| **Tasks** | `tasks/demo_tasks.yaml` | anywhere (e.g. `~/.groket/tasks/`) | Pass explicitly: `batch --tasks <path>` |

Detectors implement the **same** `@detector` pattern as built-ins under
`groket/engine/detectors/`; they are part of the user plugin story, not a
separate API.

---

## 1. Detector + rule (engine findings)

### What you get

- A Python function registered as `repeated_shell_cd`
- A rule YAML that calls that detector and sets severity / copy

### Install

```bash
mkdir -p ~/.groket/detectors ~/.groket/rules

cp examples/extensions/detectors/repeated_shell_cd.py ~/.groket/detectors/
cp examples/extensions/rules/repeated-shell-cd.yaml   ~/.groket/rules/
```

### Or scaffold from scratch

```bash
uv run groket gen detector repeated_shell_cd
uv run groket gen rule repeated-shell-cd --detector repeated_shell_cd
# then edit the generated files under ~/.groket/
```

### Verify

```bash
uv run groket                    # start TUI, open a session, run analysis
# or
uv run groket audit ~/groket/runs/traces
```

The rule id is `repeated-shell-cd`. Findings use the engine analyzer (built-in
`engine` plugin) when that rule fires.

### Detector contract

```python
from collections.abc import Sequence

from groket.engine.detectors import detector
from groket.engine.models import Match
from groket.models import ChatMessage, RuleParams, ToolCall

@detector("my_name")
def detect_my_name(
    tool_calls: list[ToolCall],
    messages: Sequence[ChatMessage],
    params: RuleParams,
) -> list[Match]:
    ...
    return [Match(tool_calls=[...], variables={"key": "value"})]
```

Rule YAML references `detector: my_name` and may pass `params:` into the function.

---

## 2. Analysis plugin (Report / Findings by plugin)

### What you get

- Class `SessionEventCountAnalyzer` implementing `Analyzer`
- Plugin id `session-event-count` on findings

### Install

```bash
mkdir -p ~/.groket/plugins
cp examples/extensions/plugins/session_event_count.py ~/.groket/plugins/
```

Enable in `~/.groket/config.json` (merge with your existing file):

```json
{
  "analysis": {
    "plugins": ["session_event_count:SessionEventCountAnalyzer"]
  }
}
```

### Or scaffold + register in one step

```bash
uv run groket gen plugin session_event_count --register
```

(`--register` appends the `module:ClassName` entry to `config.json`.)

### Verify

```bash
uv run groket
# Sessions list → select session(s) → a (analyze) → Report / Findings
```

### Analysis plugin contract

```python
class MyAnalyzer:
    @property
    def info(self) -> AnalyzerInfo: ...

    def analyze(self, session_dir: Path, **kwargs) -> AnalysisResult: ...
```

Config is always **`module_stem:ClassName`** (filename without `.py` = module stem
when the file lives in `~/.groket/plugins/` on `sys.path`).

More analysis samples: repo root `plugins/gte_feedback_grok.py`,
`examples/plugins/*.py`.

---

## 3. Tasks (batch only)

There is **no** default task catalog. Batch always needs an explicit file.

```bash
uv run groket batch --tasks examples/extensions/tasks/demo_tasks.yaml
# optional:
#   --models <id> ...
#   --task-id demo-list-workspace
#   --category custom
```

Scaffold an empty template:

```bash
uv run groket gen tasks
# → ~/.groket/tasks/example_tasks.yaml
uv run groket batch --tasks ~/.groket/tasks/example_tasks.yaml
```

Each task entry supports `task_id`, `prompt`, optional `repo_url` /
`repo_branch`, and `initial_commands` / `setup_instructions`.

---

## Quick reference

| Goal | Command |
|------|---------|
| New detector stub | `uv run groket gen detector NAME` |
| New rule YAML | `uv run groket gen rule ID [--detector NAME]` |
| New analysis plugin | `uv run groket gen plugin NAME [--register]` |
| New tasks YAML | `uv run groket gen tasks [PATH]` |
| Run batch | `uv run groket batch --tasks PATH` |
| TUI | `uv run groket` |

User directories are created on demand under `~/.groket/` (`paths.ensure_user_extension_dirs()`).
