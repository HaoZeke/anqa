# Design: rich task files + interactive multi-turn runs

Status: proposed (implementation not started). Aligns with AGENTS: typed contracts,
domain modules under `runs/`, daemon-free unit tests, no process-leak comments in code.

## Goals

1. **Batch / task authoring** — make complex eval scenarios easy to write and
   validate; make every allowed `tasks.yaml` field obvious without reading
   `load_tasks()` source.
2. **TUI runner multi-turn** — opt into a session that does **not** end when the
   first prompt finishes; operator can send follow-up prompts until they mark
   the run **done**.

Non-goals for v1: remote task marketplaces; multi-user collaboration on a live
container; changing analysis/detector contracts.

---

## Feature A — Task file schema and authoring

### Current state

- Loader: `groket.runs.batch.load_tasks` / `EvalTask` dataclass.
- Documented-by-example only (`examples/tasks/demo_tasks.yaml`,
  `groket gen` / `write_tasks_file` scaffold).
- Fields today (implicit):

| Field | Required | Notes |
|-------|----------|--------|
| `task_id` | yes | Stable id; used in session naming |
| `prompt` | yes | Primary user message |
| `repo_url` | no | Empty = no-repo job |
| `repo_branch` | no | Default `main` if repo set |
| `initial_commands` / `setup_instructions` / `setup` | no | Pre-agent shell (string or list of lines) |
| `docker_image` | no | Default `fully-loaded` |
| `description` | no | Human blurb |
| `category` | no | Filter for `groket batch --category` |
| `domain` | no | Tag: `general-swe`, `firmware`, … |
| `horizon` | no | Tag: `short` / `long` / `autonomous` |

Missing for “complex” authoring: multi-step prompts in one task, models per
task, persona/plugins/MCP overrides, env, timeouts, expectations/assertions,
tags, `extends` / includes, JSON Schema for editors and CI.

### Proposed model

Introduce a **versioned tasks document** with a Pydantic model as source of
truth, and emit **JSON Schema** from that model for tooling.

```text
groket/runs/task_schema.py   # Pydantic TaskFile / TaskDefinition (public API)
groket/runs/batch.py         # load_tasks delegates to task_schema; keep EvalTask
                             # as runtime DTO or alias fields 1:1 for compatibility
schemas/tasks.schema.json    # generated artifact (committed or CI-produced)
docs/source/tasks.rst        # Sphinx page: fields + examples (human docs)
```

**Document shape (v1):**

```yaml
# $schema: https://<pages-host>/schemas/tasks.schema.json  # optional in file
schema_version: 1

# Optional document defaults (task fields inherit unless overridden)
defaults:
  docker_image: fully-loaded
  domain: general-swe
  horizon: long
  persona_id: ""           # optional override for batch (if we wire it)
  models: []               # optional; CLI --models still wins unless policy says task wins

tasks:
  - task_id: complex-api-migration
    description: Multi-file migration with setup and verification cues
    category: migration
    domain: general-swe
    horizon: long
    docker_image: fully-loaded
    repo_url: https://github.com/org/app.git
    repo_branch: main
    tags: [python, fastapi]
    env:
      APP_ENV: test
    initial_commands: |
      pip install -e ".[dev]"
      pytest -q --collect-only
    # Single-shot (today) OR multi-turn scripted (batch only — see turns)
    prompt: |
      Migrate X to Y. Run tests before claiming done.
    # Optional scripted follow-ups for batch (non-interactive)
    turns:
      - prompt: |
          Address any failing tests; do not change public APIs.
      - prompt: |
          Add a short CHANGELOG entry for the migration.
    # Optional authoring metadata (ignored by runner unless we add checks later)
    success_hints:
      - pytest exits 0
      - no TODO(migration) left in src/
```

**Compatibility:** `schema_version` optional → treat as `1`. Unknown keys:
warn in CLI (`groket batch` / `groket tasks validate`), fail in `--strict`.
Keep accepting `setup_instructions` / `setup` aliases forever (or one release
with deprecation warning only in CLI, not in code comments).

**Validation entrypoints:**

| Command | Behaviour |
|---------|-----------|
| `groket tasks validate PATH` | Load + Pydantic validate; print errors with line/field |
| `groket tasks schema` | Print or write `tasks.schema.json` (stdout or `--out`) |
| `groket tasks init` | Scaffold (existing gen path) with `$schema` comment + rich example |
| IDE | Point YAML at schema URL or local `schemas/tasks.schema.json` |

**Complex authoring DX (v1–v1.1):**

1. **JSON Schema** — autocomplete + red squiggles in VS Code / JetBrains.
2. **Sphinx `tasks` page** — table of fields + 3 worked examples (no-repo,
   repo+setup, multi-turn `turns`).
3. **`defaults` + `tags` + `env`** — less repetition in large catalogs.
4. **`turns`** — scripted multi-prompt for **batch only** (each turn is a
   sequential prompt in the **same** container after prior agent exit; see
   Feature B for interactive TUI).
5. Later: `include: other.yaml` / `extends: template_id` if catalogs grow;
   defer until we have >1 real multi-file catalog.

### Schema hosting (GitHub Pages + CI)

**Recommendation: yes, host the schema**, but keep a **committed copy** in the
repo so offline/clone always works.

| Artifact | Location | Consumer |
|----------|----------|----------|
| Source of truth | Pydantic models in `groket/runs/task_schema.py` | runtime + tests |
| Generated schema | `schemas/tasks.schema.json` (committed) | editors, CI |
| Published URL | `https://<org>.github.io/groket/schemas/tasks.schema.json` | `$schema` in YAML |

**CI job (on `main` / release tags):**

1. `uv run python -m groket.runs.task_schema emit --out schemas/tasks.schema.json`
2. Fail if working tree differs (schema drift).
3. Deploy `schemas/` (+ optional Sphinx HTML) to **GitHub Pages** via
   `actions/upload-pages-artifact` + `actions/deploy-pages` (or existing
   Sphinx docs job if we fold schema into `docs/_build/html/schemas/`).

**Versioning:** embed `"$id"` / title `groket-tasks-v1`. Breaking field changes
bump `schema_version` and `$id` path (`…/tasks-v2.schema.json`) so old catalogs
keep resolving.

**Why not schema-only without Pages?** Local `schemas/tasks.schema.json` is
enough for contributors; Pages is for **stable absolute `$schema` URLs** in
files shared across machines and for non-clone consumers. Low cost if CI
already builds docs.

---

## Feature B — Interactive multi-turn runs (TUI runner)

### Current state

- Runner collects one `prompt` (+ setup, models, persona, …) and calls
  `RunManager.start_run(...)`.
- Orchestrator starts a container; entrypoint clones (optional), runs setup,
  runs **one** `grok` invocation, optional share loop, exits.
- Jobs UI tracks container until exit; no “send another prompt into the same
  session” path.

### Desired UX

1. On Runner (or Jobs), opt-in: **“Interactive / multi-turn”** (checkbox or
   mode toggle; default **off** = today’s one-shot).
2. Start run as today (first prompt).
3. When the agent **finishes the current turn** (container would normally
   exit), **do not tear down** the workspace; surface UI: status **waiting for
   follow-up**, input for next prompt, actions **Send follow-up** and **Done**.
4. Operator may send **N follow-ups**. Each continues in the **same
   workspace / session continuity** (see mechanics).
5. **Done** ends the interactive session (stop container, finalize share,
   mark job completed). Cancel/abort still available.

### Mechanics (recommended)

**Same container, multi-invocation entrypoint** (best fidelity to “follow-up”):

1. New `ContainerConfig` / env flags, e.g. `GROKET_INTERACTIVE=1`,
   `GROKET_TURN_GATE=/tmp/groket-turn-gate` (or FIFO under session dir).
2. Entrypoint loop:
   - Read prompt from `/groket-prompt.txt` (or turn-specific file).
   - Run `grok` once.
   - If interactive: write `turn_status=awaiting_followup` to a small JSON
     under the **sessions volume** (host-visible); **block** on gate file or
     long-poll a host-written `next-prompt.txt` with timeout heartbeat.
   - Host writes next prompt + signals gate; entrypoint loops.
   - Host writes `done` sentinel → entrypoint exits cleanly (share once if
     configured).
3. `RunManager` / Jobs:
   - Track `BackgroundRun.mode = one_shot | interactive`.
   - Interactive runs expose `submit_follow_up(prompt: str)` and
     `complete_interactive()` (Done).
   - Status enum extension: `awaiting_follow_up` alongside running/exited.
4. TUI:
   - Runner: toggle **Interactive multi-turn**.
   - While awaiting: modal or Jobs detail pane with `TextArea` + **Send** /
     **Done** (bindings: e.g. primary send, explicit Done button).
   - Session browser can open mid-flight traces as today (sessions volume
     still updating).

**Alternative (simpler, weaker continuity):** each follow-up is a **new**
container with the same `work_dir` / volume snapshot. Reject for v1 if we can
ship the gate loop — follow-ups that cannot see prior agent file edits are
misleading.

**Batch `turns:`** uses the **same entrypoint loop** non-interactively: gate is
fed from the task’s `turns` list automatically (no TUI). One implementation
serves scripted batch multi-turn and interactive TUI.

### Safety and product rules

- Default remains **one-shot** (no behaviour change for existing users).
- Interactive runs should show a clear Jobs label (`interactive`, turn index).
- Timeouts: optional max idle waiting for follow-up (env / prefs) so abandoned
  gates do not leak containers forever; default e.g. 24h or “until Done/Cancel”.
- Persona / plugins / MCP apply **once** at container start (manifest install
  unchanged); follow-ups only change the prompt file, not capability remounts
  (v1). Remounting plugins mid-session is out of scope.

### Testing (AGENTS §11 / 100% gate)

- Unit-test Pydantic schema + `load_tasks` fixtures (valid/invalid YAML).
- Unit-test gate protocol with temp files (no Docker): “write status → wait →
  read next prompt”.
- Orchestrator tests keep **fake** `DockerClient`; assert env/volumes for
  interactive flag.
- TUI: pilot toggle + message handlers with fake `RunManager` methods — no
  live daemon.

---

## Implementation plan (PR-sized units)

Commit each unit separately (AGENTS §2).

| PR | Scope |
|----|--------|
| **1** | `task_schema.py` + Pydantic models; `load_tasks` validates via schema; `groket tasks validate` / `tasks schema`; commit `schemas/tasks.schema.json`; tests |
| **2** | Sphinx tasks page + example catalog refresh; optional CI schema-drift check |
| **3** | GitHub Pages (or docs job) publish `schemas/tasks.schema.json` with stable URL; document `$schema` in examples |
| **4** | Entrypoint turn gate + `ContainerConfig` interactive flags; `RunManager` follow-up / done API; unit tests with fakes |
| **5** | TUI Runner toggle + awaiting-follow-up UI (Jobs and/or Runner); batch `turns:` wired to same gate |

---

## Open choices (defaults proposed)

| Topic | Proposal |
|-------|----------|
| Schema host | **GitHub Pages** + **committed** `schemas/tasks.schema.json` |
| Multi-turn continuity | **Same container** + entrypoint gate (not new container per turn) |
| Batch multi-turn | Task field `turns: [{prompt}]` after initial `prompt` |
| Task-level `models` / `persona_id` | Support in schema v1; batch respects unless CLI overrides (document precedence: CLI > task > defaults) |
| Idle timeout for interactive | Configurable; sensible default so containers do not leak |

---

## Success criteria

- Author can open `tasks.yaml` in an editor with schema and see allowed
  properties without reading Python.
- `groket tasks validate` fails loudly on bad files in CI.
- Complex multi-step scenarios expressible via `turns` in batch and via
  interactive TUI until **Done**.
- Default one-shot path unchanged; 100% coverage gate still met with domain
  tests and fakes only.
