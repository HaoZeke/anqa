# AGENTS.md — groket

Groket is a [Textual](https://github.com/Textualize/textual) TUI for evaluating
Grok Build sessions (Python 3.13+). Similar in spirit to
[posting](https://github.com/darrenburns/posting),
[harlequin](https://github.com/tconbeer/harlequin), and
[toolong](https://github.com/Textualize/toolong).

This file is the contract for humans and coding agents working in the repo.

---

## 1. Quick start

```bash
make install        # .venv (test+dev) + ``groket`` on PATH via ``uv tool install -e .``
groket              # interactive TUI (or: uv run groket)
make test           # pytest
make lint           # ruff + mypy
```

Non-TUI CLI: ``groket gen …`` scaffolds extensions; ``groket self-test`` runs
host checks without the TUI; ``groket batch run|validate|schema`` runs task
YAML catalogs through Docker (see ``examples/tasks/``). Default ``groket`` /
``groket PATH`` is the interactive TUI.

Prefer **`uv run …`** for project tools (`pytest`, `ruff`, `groket`) so the
environment matches the lockfile.

### Dependencies

**Heavy deps are fine.** Prefer mature libraries over hand-rolled minimal
alternatives when they improve DX or correctness:

| Area | Library | Role |
|------|---------|------|
| TUI | **Textual** (+ **Rich**) | Screens, widgets, themes |
| CLI | **Typer** (+ **Click**, **shellingham**) | Subcommands, Rich help, shell completion (`groket --install-completion`) |
| i18n | **fluent.runtime** | `locale/<lang>/main.ftl` + `ui_text` |
| Data | **Pydantic v2**, **PyYAML** | Config / serialised models; rule & task YAML |
| Docker | **python-on-whales** | Container orchestration |

Do not avoid a dependency only to “stay lean.” Avoid new deps that duplicate
an existing choice (e.g. don’t add argparse helpers alongside Typer).

---

## 2. Agent / commit hygiene

**Commit each finished unit of work in the same turn** — do not batch an
entire conversation into one mega-commit at the end. A “unit” is one coherent
change the user could revert alone (one feature slice, one rename, one test
layout move). After that unit is verified, **commit before starting the next**.
Leaving a green, multi-theme working tree uncommitted is a process failure.

Before **any** commit from an agent session:

1. **`uv run pytest tests/ -q`** passes (or the owning subset, then full suite).
   Do not commit on red tests.
2. **`git status`** — stage intended files only; no secrets.
3. **Commit** with a clear imperative message (why, not only what). Prefer one
   logical change per commit when practical.
4. If GPG signing fails in a non-TTY environment:
   `git -c commit.gpgsign=false commit …` and note it (re-sign locally if
   required).

Do not leave related edits unstaged after claiming work is done. Re-run tests
after the final diff for that commit — not only “earlier in the session.”

---

## 3. Architecture

Root modules are **foundational concepts** only. Domain logic lives in packages.

```
groket/
  cli.py, models.py, parser.py, paths.py, constants.py, utils.py, flags.py
  runs/                  # personas, run_configs, run_manager, batch, live_share, services
  session/               # usage_stats, workspace_diff, turns, turn_gate
  diagnostics/           # host self-test (Docker, Grok auth, paths)
  analysis/              # Analyzer protocol, service, registry
  engine/                # detectors, rules loader, runner
  capabilities/          # MCP / skills / Grok Build plugins
  docker/                # orchestration (orchestrator.py, base_profiles.py, resources.py loader)
  extensions/            # scaffold for groket gen (detectors, rules, plugins, tasks)
  locale/                # Fluent .ftl (+ help.rich.txt) — UI strings
  ui/                    # Textual UI (app, screens, widgets, helpers, app.tcss)
    app.py               # TraceEvalApp — sessions home
    screens/             # browser, runner, jobs, personas, rules, run_configs
    widgets/             # timeline, detail, help_modal, controls, activity_bar
    forms.py, fuzzy.py, session_summary.py   # presentation helpers (not domain)
    panel_render.py, data_table.py, render_detail.py
    prefs.py, styles.py, i18n.py, text.py, bindings.py, commands.py
  assets_loader.py       # resolve non-Python assets (repo assets/ or embedded)

assets/                  # **non-Python assets** (not importable modules; not coverage source)
  docker/                # entrypoint.sh, Dockerfile.*, groket-share-once.py, setup-empty.sh
  config/                # empty rules.yaml / composites.yaml stubs (rules live under ~/.groket)

examples/                # copy-in reference packs (detection, analysis, tasks) — not auto-loaded
Optional wheel mirror: ``groket/_embedded_assets/`` (copy of ``assets/`` for installs).
Do **not** put executable/product templates back under ``groket/**/*.py`` modules.
```

**Data flow:** `models/parser → runs|session|analysis|engine → ui`. Screens
delegate; no file I/O or JSON parsing in screen code. Static Docker/YAML
templates are read via :mod:`groket.assets_loader`, not embedded in Python strings.

### 3.0 Path layout (product contract)

Two roots — do not write app identity into the process cwd by default.

| Root | Default | Holds |
|------|---------|--------|
| **Config home** (`APP_HOME`) | ``~/.groket`` | ``config.json``, personas, detectors, rules, analysis plugins, tasks scaffolds, analysis cache, exported reports, flag fallbacks, optional ``models.yaml`` |
| **Work dir** | ``~/.groket/work`` (CLI path overrides) | Session/run data only: ``runs/traces/``, ``runs/run_configs/``, feedback cache, Docker build contexts, batch ``eval_results.json`` |

- TUI **Traces** banner label reflects the active traces root; it does not invent a second work tree under the git checkout.
- Pass a path to ``groket`` to choose what is loaded and, when that path is a work root, where new runs go (:func:`groket.paths.resolve_work_and_traces`).
- Gitignored trees under a developer checkout (``/runs/``, ``/flags/``, ``/config.json``, ``/_meta_cache.json``) are **local runtime leftovers**, not the install layout. Prefer ``~/.groket`` + ``~/.groket/work`` for day-to-day use.

**Localization (mandatory for UI copy):** Project Fluent under
`groket/locale/<lang>/`.

| Source | Role |
|--------|------|
| `locale/<lang>/main.ftl` | **All** user-visible UI strings (labels, buttons, toasts, table headers, placeholders, status chips, binding descriptions used in the Footer, palette titles/help via `cmd-*`, etc.) |
| `locale/<lang>/help.rich.txt` | Long Rich help for `?` only (Fluent cannot treat `[bold]` as plain text) |
| `ui/text.py` | Dynamic accessors: `text.foo_bar()` → Fluent id `foo-bar`; `text.cmd_x()` → `(cmd-x, cmd-x-help)`. Prefer this or `i18n.t("message-id")` |
| `ui/i18n.py` | `setup_i18n` / `t` / `ngettext` |

Language: default `en` (pass a language to ``setup_i18n`` when adding a locale).

### 3.1 Zero hardcoded user-facing UI strings (hard rule)

**Requirement:** Under `groket/ui/`, there must be **no hardcoded user-facing
English (or other language) copy** in Python. Agents and humans **must** add or
reuse Fluent message IDs and call them via `t("…")` or `ui.text` / `U.*`.

**User-facing** includes anything the operator reads in the TUI: notifications,
button labels, `Static`/`Label` text, input placeholders, DataTable column
titles, select options shown to the user, modal titles, activity-bar wording,
follow-up / mark-done prompts, and Footer/palette human descriptions.

**Do:**

1. Add the string to `groket/locale/en/main.ftl` (kebab-case id, e.g.
   `follow-up-sent = Follow-up sent to eval container`).
2. Use placeholders for variables: `flag-saved = Flag saved on event #{$index}`
   and call `t("flag-saved", index=n)` or `U.flag_saved(n)` (positional args
   bind to `{$var}` in definition order from `en/main.ftl`).
3. Prefer `from .. import text as U` then `U.some_label()`, or
   `from ..i18n import t` then `t("some-label")`.
4. For Ctrl+P entries, define `cmd-foo` + `cmd-foo-help` in Fluent and use
   `U.cmd_foo()` / the existing commands wiring.

**Do not:**

- Embed operator-facing prose in `notify(...)`, `Button(...)`, `Label(...)`,
  `Static(...)`, `placeholder=...`, `add_columns(...)`, or Footer binding
  descriptions as Python string literals.
- Keep parallel English tables in Python (`_PLAIN = {"bind_foo": "Foo"}`,
  message catalogs in `.py`).
- Put **TCSS / `DEFAULT_CSS`** or Rich **style tokens** (`"bold red"`,
  `"dim"`) in Fluent — those are not copy. Leave them as CSS/style constants.
- Treat **widget ids**, **log format strings for `logger.*`**, or **docstrings**
  as UI copy (they stay in Python).

**Exceptions (only):**

| Kind | Where |
|------|--------|
| Stylesheets | `app.tcss`, widget `DEFAULT_CSS` (CSS, not sentences) |
| Style tokens | `ui/styles.py` Rich style names (`bold red`, …) |
| Long help markup | `locale/<lang>/help.rich.txt` via `text.help_markup()` |
| Non-UI packages | `cli.py` may still print for non-TUI tools; prefer Fluent when the same phrase is also shown in the TUI |

**Agent checklist before claiming UI work done:** no new/changed user-facing
literals under `groket/ui/`; new phrases exist in `en/main.ftl`; call sites use
`t` / `U`. When unsure, put it in Fluent.

---

## 4. Code conventions

### 4.1 Style

- `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants.
- `from __future__ import annotations` in every module (ruff enforces this).
- Type-annotate all public signatures. Use `X | None`, lowercase generics.
- **Typed values only:** do not use `Any` or `object` as value types (both
  mean “unknown”). Use `JsonValue` / `JsonObject` at JSON/YAML boundaries,
  `ChatMessage` / `ChatHistory`, `RuleParams` (`Mapping[str, JsonValue]`),
  `ToolInput` (`JsonObject`), `MatchVariables`, concrete classes, `Protocol`s,
  or `TypedDict` + `Unpack[...]` for open kwargs. Exception: a **forced
  third-party signature** you cannot wrap (one-line comment naming the
  library). Prefer importing Textual’s `App` / `Widget` / `SelectionList[...]`
  over a loose annotation.
- Detectors: `(tool_calls: list[ToolCall], messages: Sequence[ChatMessage], params: RuleParams) -> list[Match]`.
- Analyzers: `analyze(self, session_dir: Path, **kwargs: Unpack[AnalyzeContext]) -> AnalysisResult`.
- `logger = logging.getLogger(__name__)`. No `print()` except in `cli.py`.
- Initialise all instance attributes in `__init__`.
- Remove dead code; prefer delete over “for later” stubs.

### 4.2 Comments and prose (present product only)

**Ship only the product as it exists.** Comments, docstrings, module banners,
commit messages that land in the tree, CLI ``--help`` text, UI strings, and
``AGENTS.md`` describe **current behaviour and contracts** — where data lives,
who writes it, who reads it, failure modes, ordering guarantees.

There is **no published version line** to migrate from: do not document
alternate historical layouts, rename stories, or “until release” scaffolding in
sources or agent docs. Put change rationale in the **git commit message** only.

**Write** (when a comment earns its lines):

- Invariants: input shape, outputs, errors callers rely on.
- Non-obvious *why* tied to behaviour — e.g. “Persona stores marketplace plugin
  **names**; launch writes ``plugins-manifest.json`` (URL + SHA); the eval
  entrypoint clones into ``/root/.grok/plugins``.”
- Owning module for cross-layer calls: “Screens delegate to
  ``runs.run_manager``; do not start Docker from widgets.”

**Omit** from in-tree prose: design-process narration, agent self-talk, rejected
alternatives, and any framing that defines behaviour only by contrasting a
prior revision. Delete comments that only applied to removed code.


### 4.2a Sphinx documentation in code

Docstrings and module documentation use **Sphinx / reStructuredText field
lists**, in the spirit of [coredis](https://github.com/alisaifee/coredis)
(not Google/NumPy napoleon style as the primary form).

**Module** docstrings: one short paragraph on responsibility; optional
``.. note::`` / ``.. warning::`` for behavioural contracts.

**Callables** (public functions and methods):

.. code-block:: text

    """One-line summary.

    Longer explanation of behaviour and invariants when needed.

    :param name: Description of ``name``.
    :param other: Description; mention defaults only when non-obvious.
    :returns: What the caller receives.
    :raises KeyError: When the detector name is unknown.
    """

**Classes**: class docstring describes the type; important attributes may use
``:ivar:`` / ``:cvar:`` or be documented on the attribute. Prefer linking types
with intersphinx-friendly names (``:class:`~groket.models.ToolCall```) in
longer docs.

**Do not** put process narration in docstrings (see §4.2). **Do** document
parameters, returns, and exceptions that callers rely on. Private helpers may
use a single-line summary without full field lists.



### 4.2b Local tooling (Makefile)

``make lint`` / CI run **mypy on the entire ``groket`` package** (not a subset of modules). Do **not** use ``disable_error_code`` (or per-module ``ignore_errors``) to silence real issues — narrow types with :class:`~groket.models.ParamBag`, :class:`~groket.models.ToolInputBag`, and :func:`~groket.models.json_as_str` / friends instead.


Mirror of coredis-style targets — always via ``uv run`` / ``make``:

| Target | Action |
|--------|--------|
| ``make install`` | ``uv sync --group test --group dev`` then ``uv tool install --force --editable .`` (``groket`` on PATH) |
| ``make lint`` | ruff check + format --check + **mypy on all of ``groket/``** |
| ``make lint-fix`` | ruff autofix + format + mypy |
| ``make test`` | pytest |
| ``make test-cov`` | pytest + coverage |
| ``make schema`` | Regenerate ``schemas/tasks.schema.json`` from Pydantic |
| ``make schema-check`` | Fail if committed tasks schema drifts |
| ``make ci`` | ``lint`` + ``schema-check`` + ``test`` (default PR gate) |

**Published tasks schema:**
``https://indynull.github.io/groket/schemas/tasks.schema.json``
(GitHub Pages from ``main`` / ``master`` via ``.github/workflows/pages.yml``).
Repo also keeps ``schemas/tasks.schema.json`` for offline editors. Point task
YAML at the URL with ``# yaml-language-server: $schema=…`` or your editor’s
schema mapping. Enable **Settings → Pages → Source: GitHub Actions** once.
| ``make clean`` | caches and build artefacts |

GitHub Actions (``.github/workflows/ci.yml``) runs the same lint/test path.
Prefer ``make ci`` before claiming work complete.

### 4.3 Module purity (no junk in type / domain modules)

**A module’s top-level contents must match its job.** Do not park helpers in
the nearest open file. If you need a private `_foo` that is not a type,
factory on that type, or the module’s single responsibility, **move it** to
`utils.py`, a dedicated `*_helpers.py` / package submodule, or the caller’s
layer (UI vs domain).

| Module / package | Allowed at module level | **Forbidden** (examples) |
|------------------|-------------------------|---------------------------|
| `models.py`, `engine/models.py`, `analysis/base.py` | Types, enums, type aliases, TypedDicts, dataclasses/Pydantic models, trivial `@property` / dunders on those types | Standalone `_strip_*`, regex caches, I/O, loggers used only by helpers, presentation/formatting that is not a field property |
| `parser.py` | Parse/load functions + private parse helpers **used only by this module’s public API** | UI chrome, run orchestration, detectors |
| `paths.py`, `constants.py` | Path resolution, named constants | Business logic, Textual widgets |
| `utils.py` | Pure cross-cutting helpers (duration, text cleanup) | Domain models, imports from `ui` / `runs` |
| `runs/*`, `session/*` | Domain types + stores + orchestration for that concern | Textual screens, form widgets |
| `ui/*` | Screens, widgets, presentation helpers (`forms`, `fuzzy`, `session_summary`) | Trace JSON parsing, Docker orchestration (delegate to domain) |

**Hard rules for agents:**

1. **No module-level `_private_helper` in model/type files** unless it is only
   referenced by methods on types defined in that same file *and* cannot live
   in `utils` without a circular import. Prefer public names in `utils`
   (`strip_control_chars`, not `_strip_control_chars` in `models.py`).
2. **No “I’ll add a helper here because the method needs it.”** If a type
   method needs non-trivial logic, either (a) keep it as a method body, (b)
   import from `utils` / the correct package, or (c) introduce a focused
   module — keep `models.py` limited to types and type-adjacent members.
3. **No unused `logger = logging.getLogger(__name__)`** (or other imports)
   left behind after moving helpers.
4. **Before finishing a change that touches `models.py` / `*/models.py` /
   `analysis/base.py`**, re-read the file: if anything is not a type or a
   one-liner property on a type, move it out in the same PR.
5. Prefer **public** helpers in `utils` over private `_` copies scattered in
   domain modules — one implementation, many callers.

### 4.4 Imports

Module-level imports at the top (stdlib → third party → local), after
`from __future__ import annotations`.

- Do **not** use function-level imports to hide circular dependencies. Break
  cycles with leaf modules, `TYPE_CHECKING` for types only, and
  screens → services/models (not screens importing `app` at module load).
- Rare exceptions (one factual line: e.g. dynamic plugin `importlib`; CLI
  defers TUI import so `--help` stays light) — still no process narrative.
- Help markup is loaded via `ui_text.help_markup()` / locale files so
  `bindings` can import help helpers without cycles.

### 4.5 Error handling

- Catch the **narrowest** exception type that the code actually handles.
- **Never** `except Exception: pass` or `except Exception: logger.debug(...)` around
  logic that must succeed for the product to work (table population, session
  load results, core render paths). Prefer **no try** so bugs fail loudly in
  tests and at the terminal.
- TUI **event handlers** (`on_*`, key actions) may catch broadly so one bad
  keypress does not kill the process — but must **`logger.exception` / `warning`**
  with context, and must **not** invent an empty UI state that looks successful.
- Optional I/O and best-effort chrome (banner text, “last directory” prefs,
  cache write) may use a narrow try; still log at least `warning` if user-visible.
- Fail early in constructors — do not silently swallow init errors.
- Worker threads (`@work`) that update the UI: on failure, notify the user or
  set an explicit error state; do not return as if zero items were found when
  the failure was an exception mid-loop.

### 4.5a Quality gates (non-negotiable)

Before claiming work complete (and before every agent commit):

1. **`uv run mypy groket`** — **zero errors** on the entire package (all modules
   under ``groket/``). Do **not** use ``disable_error_code``, module-level
   ``ignore_errors = true``, or blanket ``# type: ignore`` to hide debt.
2. **`uv run pytest tests/ --cov=groket --cov-fail-under=100`** — **100% line
   coverage of every line in ``groket/``**. This is **non-negotiable**. Do not
   lower ``[tool.coverage.report] fail_under``, do not add ``omit`` / ``exclude``
   for our package, and do not use ``pragma: no cover`` except for the rare
   forced third-party trap door already allowed for typing (document why).
   Work is **not done** while coverage is below 100%, even if tests “pass”
   without ``--cov-fail-under=100``.
3. **`uv run ruff check groket tests`** and format check — clean.
4. Prefer **delete / merge duplicates** over new abstractions: unused modules,
   copy-pasted UI helpers, and parallel “almost the same” JSON accessors are
   why type errors and coverage holes multiply. Refactor toward one path
   (e.g. ``ParamBag`` / ``ToolInputBag`` / ``json_as_*``) instead of N variants.

### 4.5b UI and Docker test drivers

- **Textual:** use ``App.run_test()`` + Pilot (async pytest + ``pytest-asyncio``).
  That mounts the app, runs bindings, and queries widgets. Prefer Pilot over
  constructing ``TraceEvalApp`` and only asserting attributes. Optional visual
  gate: ``pytest-textual-snapshot`` (SVG). Shared wait helpers live in
  ``tests/ui/pilot_helpers.py`` (condition-based; see §4.5c).
- **Docker:** default unit suite **must not** require a live daemon. Fake
  ``python_on_whales`` at the orchestrator boundary (as in
  ``tests/docker/test_orchestrator.py``). Optional integration job (CI label /
  ``DOCKER_IT=1``) may use real Docker — keep it out of ``make test``.
- **CLI progress:** domain code uses ``logging``; only ``cli.py`` may ``print`` /
  ``typer.echo``. Pytest default capture keeps the suite quiet; use ``capsys`` /
  ``capfd`` when asserting CLI output. Never set ``--capture=no`` in default
  ``addopts`` (it dumps logger noise into the suite stream).

### 4.5c Test code quality (same bar as ``groket/``)

**``tests/`` is product code for agents.** The conventions in §4.1–4.5 apply to
tests with the same force as to ``groket/``: typing, no process leakage in
comments, no silent broad ``except``, no ``Any``/``object`` convenience bags,
Sphinx-style docstrings on shared helpers, ruff on ``tests/`` in the gate
(``uv run ruff check groket tests``). Flaky or “sleep until green” tests are a
**quality failure**, not an acceptable shortcut to coverage.

**Synchronisation (Textual Pilot and other async UI):**

- **Do** wait on an **observable condition** (timeline non-empty, tab id,
  widget mounted, gate file written, counter advanced). Prefer a small shared
  helper (e.g. ``wait_until(pilot, pred)`` in ``tests/ui/pilot_helpers.py``)
  that drains the message loop with ``await pilot.pause()`` (no delay argument)
  between attempts and fails with a clear ``AssertionError`` after a **bounded
  attempt count** (safety timeout, not the primary signal).
- **Do not** use fixed wall-clock sleeps as the strategy: ``time.sleep(0.2)``,
  ``await pilot.pause(0.2)`` once then assert, or ``for _ in range(N):
  pause(0.15)`` without checking state each turn. Those races under load (CI,
  busy hosts) and encode hope instead of a contract.
- **Do** stop irrelevant timers in tests when they fight assertions (e.g.
  ``BrowserScreen._stop_live_refresh()`` after the screen is ready) so
  background ticks are not part of the scenario.
- **Do** prefer setting authoritative widget state and then waiting
  (``tabs.active = "tab-summary"`` + ``wait_until``) when actions only schedule
  work; still call the real action/binding so the path is covered.
- **Unit-test pure domain** (parser, turn gate, activity line builder) without
  Pilot when no widget tree is required — zero event-loop waits.

**Structure and naming:**

- Mirror ``groket/`` under ``tests/`` (``tests/ui/…`` for ``groket/ui/…``).
- Domain-shaped names (``test_browser_follow_up_enter_and_queue``), not
  wave / smoke / extra_cov suffixes.
- Shared fixtures and Pilot helpers in ``tests/conftest.py`` or
  ``tests/ui/pilot_helpers.py`` — not copy-pasted sleep loops in every file.
- Parametrize variants; one behaviour per test where practical.
- Fake **only** external boundaries (Docker API, network, interactive git). Do
  not mock ``groket`` modules against each other to invent coverage.

**Stdout / logging in tests:**

- Domain under test must not ``print`` (see §4.5b). Assert CLI output with
  ``capsys`` / ``capfd`` or Typer’s runner; do not rely on suite capture being
  off.
- Prefer asserting return values and filesystem/domain state over scraping logs.
- **UI display:** assert what the user reads — ``Static.content`` / Rich plain
  text / table cell values — not merely that a widget exists or a renderable
  is non-``None``. Shared helpers: ``tests/ui/pilot_helpers.assert_rich_contains``
  / ``assert_static_contains`` / ``rich_plain``. Do not ship “pause then pass”
  or ``assert x is not None`` as the only check for a user-visible surface.

**How to reach 100% without cheating (see also §11):** domain-named tests under
``tests/`` mirroring ``groket/``; assert real outcomes; fake only Docker /
network / interactive git; delete dead code; extract pure functions when a
branch is hard to hit from the TUI. **Not allowed as a path to 100%:** silent
broad ``except`` loops, mocking internal ``groket`` modules against each other,
fixed-sleep “eventually green” UI tests, or running a live Docker daemon in the
default unit suite.

**Only allowed typing escape:** a **forced third-party signature** that
exposes ``Any`` (or an untyped C extension) where we cannot wrap it. Document
with a **one-line** comment naming the library (e.g. ``# Textual Select.value``).
Never use ``object`` or ``Any`` as a convenience for our own JSON/YAML/tool bags —
use ``JsonValue`` / ``JsonObject`` / ``ParamBag`` / concrete models.

Recursive JSON (and similar tree types) **must** use PEP 695 ``type`` aliases on
3.12+ (we require 3.13+), following the pattern in
`coredis <https://github.com/alisaifee/coredis>`_ ``coredis/_py_312_typing.py``::

    type JsonValue = str | int | float | bool | dict[str, JsonValue] | list[JsonValue] | None

Do **not** fall back to ``dict[str, Any] | list[Any]`` (the 3.11 workaround in
coredis ``_py_311_typing.py``) unless we drop below 3.12. Prefer
:func:`~groket.models.as_json_object` when building mappings so mypy accepts
them despite dict invariance.

``make lint`` must run ruff **and** mypy; ``make test`` / CI must enforce
**coverage 100%**. Do not split the gate to “look green” while mypy or coverage
is red. Agents **push back** only on *method* (bad tests), never on *whether*
100% applies.

### 4.6 Size limits (ruff / `pyproject.toml`)

| Rule | Limit | What |
|------|-------|------|
| PLR0913 | 5 | max function arguments |
| PLR0911 | 5 | max return statements |
| PLR0912 | 12 | max branches |
| PLR0915 | 50 | max statements per function |
| PLR0904 | 20 | max public methods per class |

Split code when limits are exceeded. Optional follow-ups may be listed in
[TODO.md](TODO.md).

### 4.7 Models

- **Pydantic v2** for serialised models (Flag, EvalRun).
- **Plain dataclasses** for hot-path models (TraceEvent, ToolCall, Issue).
- Model modules are **types only** — see §4.3. Formatting helpers belong in
  `utils` (e.g. `strip_control_chars`), not private helpers next to `ToolCall`.

### 4.8 Naming (functions, privates, tests) — coredis-inspired

Name things after **behaviour and domain**. Prefer the style of
[coredis](https://github.com/alisaifee/coredis): short, readable identifiers
(`_parse_url`, `_reset`, `test_hget_and_hset`, `tests/commands/test_hash.py`).

#### Production code

- Prefer **verb + object** public names: `prepare_persona_plugins_dir`,
  `list_marketplace_catalog`, `plugin_install_specs`.
- **Class methods** may use a leading underscore for real encapsulation
  (`_apply_persona_capabilities_config`, `_git_clone_plugin`) — still
  **descriptive**. Avoid vague privates: `_helper`, `_fix`, `_go`, `_do2`,
  `_x`, `_process`, `_handle` with no object.
- **Do not** sprinkle module-level `_helper()` functions merely to split a
  file — and **never** in `models.py` / type modules (see §4.3). Prefer a
  clear public name in `utils` or a small dedicated module, or a method on a
  cohesive class.
- Leading-underscore **modules** (e.g. `analysis/_cache.py`) are fine for
  non-API packages; public import paths should still read cleanly.
- Rename when a private grew past its original meaning — do not pile version
  suffixes (`_v2`, `_new`, `_alt`).

#### Tests (files and cases)

Mirror the package (see §11.1), as coredis mirrors Redis surfaces.

1. **File name** = unit under test (`test_<module>.py`) under the matching
   domain folder (`tests/engine/`, `tests/ui/`, `tests/runs/`, …), or at
   `tests/` root for foundational modules (`test_parser.py`).
2. **Test / class name** = behaviour or scenario (`test_empty_manifest_returns_none`,
   `TestClient`, `test_hget_and_hset`) — not how the suite was built or how much
   of the module it covers.
3. **File paths are domain names only** — not suite intensity (`full`, `smoke`,
   `extra`, `unit`, `real`, `helpers`, `_all`). Put cases in the domain file or
   a second **domain** name (`ui/test_text.py` for `ui/text.py`). Pytest marks
   may note intent; the path should not.
4. Prefer **`Test…` classes** when grouping scenarios for one type.
5. Use **`@pytest.mark.parametrize`** for variants instead of numbered copies.
6. One test (or class) should fail for **one conceptual reason**.

---

## 5. Linting and dead code

Ruff is the source of truth for mechanical cleanup. Run on `groket/` before
claiming a cleanup is done.

| What | How |
|------|-----|
| Unused imports | `uv run ruff check groket/ --select F401` (`--fix` when safe) |
| Unused locals | `uv run ruff check groket/ --select F841` |
| Complexity | PLR limits (table above) |
| Missing `from __future__ import annotations` | isort `required-imports` |
| `print()` outside CLI | T20 (allowed only in `cli.py`) |

Keep `groket/` F401/F841-clean.

**Not dead without checking call paths:**

- Textual hooks: `compose`, `action_*`, `on_*`, `_on_*`, `BINDINGS`, `CSS_PATH`, `TITLE`.
- `@detector("rule_id")` functions — driven from `config/rules.yaml`.
- Analysis plugins listed in `config.json` `analysis.plugins` as `module:ClassName`.
- Public model fields / enums populated from traces or tests even if the TUI
  does not display them yet.

**Manual passes:** unreferenced helpers, unused constants/facades, stale
`__pycache__` for deleted modules.

---

## 6. TUI and keyboard UX

Groket is **keyboard-first**. Mouse is optional acceleration — every feature
must be reachable with keys and/or **Ctrl+P**. Prefer patterns from mature
TUIs and WAI-ARIA “tab between / arrows within”.

**Sources of truth for keys**

| File | Role |
|------|------|
| [`ui/bindings.py`](groket/ui/bindings.py) | Bindings; shared `LIST_SELECT`, `CAPABILITY_PICKER`, `FORM_SAVE`, … |
| [`commands.py`](groket/commands.py) | Ctrl+P palette (contextual) |
| Fluent / `ui_text` / `help.rich.txt` | User-visible help and labels |

Do **not** invent ad-hoc key legends in banners or button labels
(`"save [ctrl+s]"`).

### 6.1 Focus model

| Input | Role |
|-------|------|
| **Tab / Shift+Tab** | Focus **between** widgets |
| **↑ ↓ ← →**, Home/End, PgUp/PgDn | Move **inside** focused widgets |
| **Enter / Space** | Activate control or selected row |
| **Esc** | Back (pushed screen) or dismiss modal |
| **Mouse** | Optional; never the only path |

- Focus order follows `compose()` DOM order.
- Visible **`:focus`** styles in `app.tcss`.
- After filling a primary list, call `focus_primary_list(widget)`. Prefer the
  list over path inputs or inert chrome. Do not steal focus on incidental
  re-renders or from a search field the user just opened (`/`).
- Bindings that need selection+focus (e.g. **Flag**) use `check_action` and
  `refresh_bindings()` — hide from Footer when inert.

### 6.2 Two layers of “tabs” (do not conflate)

1. **App / session panes** (Timeline, Findings, …; persona Identity / GitHub / …).
   Switch with **`[` / `]`** and **digits `1`–`N`**. Pane titles include the
   digit (`1 Timeline`). After switch, defer focus into the pane
   (`call_after_refresh` — inactive panes are hidden).
2. **In-pane filters / views** (timeline **Filter** Select: All / Tools / …).
   Prefer one visible **Select** + a focus key (`v`). No silent cycle keys
   without UI feedback.

| Layer | Example | Keys | Feedback |
|-------|---------|------|----------|
| Session panes | Browser Timeline / Summary / Diff / Findings / Report | `[` `]` , `1`–`5` | Tab strip + titles |
| Editor panes | Persona Identity … Plugins | `[` `]` , `1`–`6` | Tab strip + titles |
| Runner panes | Recipe / Runtime / Extras | `[` `]` , `1`–`3` | Tab strip + titles |
| In-pane view | Timeline event filter | `v` → Select | Dropdown label |
| Row actions | Flag event | `f` when actionable | Footer via `check_action` |
| Multi-select | Sessions, configs, capability pickers | **`s` / `space`** | Green `*` column 0 |

### 6.3 Multi-select lists (mandatory consistency)

| Key | Action | Where |
|-----|--------|--------|
| **`s`** | Toggle row selected (Footer: **Select**) | Sessions, run configs, MCP / plugins / skills pickers |
| **`space`** | Same as `s` (hidden binding) | Same |
| **`S`** | Select all / clear all (where implemented) | Sessions, run configs |

- Implement `action_toggle_select` (or delegate). Use `LIST_SELECT`,
  `LIST_SELECT_ALL`, `CAPABILITY_PICKER` from `bindings.py`.
- **Marker:** bold green `*` in **column 0** via `data_table.selection_mark` /
  `set_selection_marker`. Not a trailing `yes` / `on` text column.
- Do not invent alternate toggle keys on pickers; Footer must say **Select** for `s`.

### 6.3a Destructive delete (``x`` / Delete)

List deletes that remove user data from disk use **double-press ``x``**
(binding may also map Delete):

| Screen | Action | Pending state |
|--------|--------|---------------|
| Sessions home | `action_delete_sessions` | `_delete_pending_paths` |
| Run configs | `action_delete_config` | `_delete_pending_ids` |
| Personas | `action_delete_persona` | `_delete_pending_ids` |

- First ``x``: arm via :func:`groket.ui.delete_confirm.second_press_armed` and
  toast “press [x] again …”; do **not** delete.
- Second ``x`` with the **same** target set: commit delete; clear pending.
- Changing selection / cursor between presses re-arms (new pending set).
- Explicit **Delete** buttons on the same screens call the same ``action_*``
  (also double-press). Modals with an in-form Delete control (e.g. flag modal)
  keep a single confirm button — that is modal UX, not list ``x``.

Do **not** implement single-press list delete for one screen while others use
double-press.

### 6.4 DataTable UX (`data_table.py`)

Do not reimplement these patterns in screens.

| Practice | API |
|----------|-----|
| Row cursor + zebra | `style_data_table(table)` on mount |
| Preserve highlight across `clear()` + rebuild | `preserving_cursor(table)` |
| Read stable row key | `cursor_row_key(table)` |
| Restore without context manager | `restore_cursor(table, key)` |
| Toggle selection without cursor jump | `set_selection_marker` / `update_row_cell` — never `clear()` only to flip a mark |
| First populate focus | `focus_primary_list(table)` once; not after every in-place toggle |

Optional subclass: `ListDataTable` (helpers on the widget).

### 6.5 Tips and callouts (`ui/panel_render.py`)

- **UI callouts:** always **`TipSurface`** (CSS class `tip-surface`) so
  `show_tips` can refresh by widget type / class. Prefer one TipSurface per
  guidance role (do not duplicate the same shortcuts on title and action bar).
- Kinds: `tip`, `info`, `note`, `warning`, `danger`, `success` via `kind=`.
- Frame is **CSS border** (width-adaptive). Content uses key chips for
  `` `backticks` `` (Footer-like styling).
- **`admonition()` / `tip_line()`** remain Rich helpers for geometry tests /
  rare `append_text` — not for embedding inside other `Static` trees.
- **Hide tips globally:** `show_tips` in `~/.groket/config.json`, Analysis
  settings, or Ctrl+P → **Toggle tips / callouts**.

### 6.6 Context-sensitive shortcuts

Bindings are screen- or modal-scoped (widget → screen → app).

- Give each major screen/modal keys for *its* job.
- Keep a **stable global** set: `?`, `F5`/`Ctrl+R`, `j` (jobs), `Esc`, `Ctrl+P`;
  `q` only on sessions home.
- Do not make the same key mean different things based on *which cell* is
  focused unless the widget owns it (arrows in a table). Prefer pane digits
  over “if focus is in X then Y means …”.
- Prefer distinct symbols for different concepts (⚑ human flag vs ⚠ automated
  finding). Prefer **full words** in Type/Tool columns (`Session` not `SESS`).

### 6.7 Shortcut discovery (three layers only)

1. **Footer** — few primary keys (`show=True`). Lean.
2. **`?`** — unified help (`notify_help` / Fluent `help.rich.txt` via `ui_text`).
3. **Ctrl+P** — every action for the current screen (`ui/commands.py`).

Adding a key: update `bindings.py` → implement `action_*` → palette line if
non-obvious → help text if major workflow.

### 6.8 Keyboard-only checklist (new UI / modals)

- [ ] Primary list gets focus when populated (`focus_primary_list`).
- [ ] Multi-section UI uses **tabbed panes** with **`[` `]`** + **digit titles**.
- [ ] In-pane filters use a **visible** control, not silent key cycles.
- [ ] Multi-select uses **`s` / `space`** + green `*` (`data_table`).
- [ ] Table rebuilds use **`preserving_cursor`** or in-place cell updates.
- [ ] Tips use **`TipSurface`** with `` `keys` `` in backticks.
- [ ] Contextual actions use **`check_action`** where needed.
- [ ] Every button is Tab-reachable with Enter/Space **or** has a binding / palette entry.
- [ ] Modals: Esc cancels; Save uses **Ctrl+S** (`FORM_SAVE` / priority binding);
      do not embed key names in button labels.
- [ ] No mouse-only feature; no conflicting legends (banner vs Footer).

### 6.9 Global key reference

| Key | Action |
|-----|--------|
| `?` | Help modal |
| `F5` / `Ctrl+R` | Refresh current context |
| `j` | Jobs / logs |
| `Esc` | Back / dismiss |
| `q` | Quit (sessions home) |
| `Ctrl+P` | Command palette |
| `Ctrl+S` | Save (forms) or Done (multi-pickers) — priority binding |
| `[` / `]` | Previous / next **pane** (where panes exist) |
| `1`…`N` | Jump to pane N (titles show the digit) |
| `s` / `space` | **Select** (multi-select lists / capability pickers) |

---

## 7. Styling

**User theme owns aesthetics.** Prefer Textual design tokens (`$primary`,
`$surface`, `$text`, `$text-muted`, `$success`, `$warning`, `$error`) so themes
stay coherent. Do not invent a parallel palette of hardcoded Rich colours for
chrome.

| Layer | File | Role |
|-------|------|------|
| Layout / borders / focus | `app.tcss` | `$` tokens; `.panel-card`, filter bars |
| Table semantics | `_styles.py` | Severity, timeline type/tool colours |
| Callout widgets | `TipSurface` / `.tip-surface` | Adaptive CSS border |

### UI chrome vs Markdown payloads

| Kind | Examples | How |
|------|----------|-----|
| **UI structure** | Titles, keys, sections, status, tips | `panel_render` / `TipSurface`; full words; no `#` / `**` as chrome |
| **Panel frames** | Summary / Report | TCSS **`.panel-card`** — prefer `Vertical(classes="panel-card")` |
| **Markdown content** | Assistant text, plugin reports, MD diffs | `md_content()` / `content_block()` only |

Active tab uses `$primary` tint. New panels: `panel_group(...)`; do not
`widget.update("# Report\n**x**")`.

---

## 8. Filter bars

**One pattern** for exclusive filters (Timeline **and** Report **and** sessions):

| Piece | What |
|-------|------|
| Bar | `Horizontal` + `FILTER_BAR_CLASS` (`filter-bar`) |
| Label | Bold `FILTER_LABEL_CLASS` — e.g. **Filter** |
| Control | **`Select`** (exclusive mode) |
| Optional | Search `Input` (Timeline only) |

Constants: `widgets/controls.py`. Do **not** use Button chips or checkbox rows
for exclusive “which section to show.”

| Need | Control | Where |
|------|---------|--------|
| Exclusive view / section | **`Select`** in filter bar | Sessions model/task, Timeline, Report |
| Form field boolean | Full-width **`Checkbox`** | Persona editor, analysis settings |
| Read-only status | Dim text / `Static` | Selection counts (not selection *marks*) |

---

## 9. Browser Report tab

- One scroll with **inline** `panel-card` sections (Overview; Flags; per-analyzer
  panels). Not nested TabbedContent for sources.
- **Filter** dropdown (same UX as Timeline) sets exclusive visibility via
  `display` only.

---

## 10. Plugins and capabilities

Users extend groket **without editing package source** via ``~/.groket/`` and
``uv run groket gen …``.

### 10.1 User extension layout

| Path | Purpose |
|------|---------|
| ``~/.groket/detectors/*.py`` | Detector modules (``@detector("name")``). Loaded by the engine rule loader. |
| ``~/.groket/rules/*.yaml`` | Rule YAML overrides (same schema as package ``config/rules.yaml``; same ``id`` replaces bundled). |
| ``~/.groket/plugins/*.py`` | Analysis ``Analyzer`` classes; also scanned for ``@detector`` if present. On ``sys.path`` for ``module:ClassName`` config entries. |
| ``~/.groket/tasks/*.yaml`` | Optional task lists (scaffold via ``groket gen tasks``; never auto-loaded). |
| ``~/.groket/config.json`` | App prefs + ``analysis.plugins`` list. |

Worked examples (copy into `~/.groket/`): [`examples/`](examples/README.md)
(detection packs, analysis plugins, tasks).

Scaffold:

```bash
uv run groket gen detector my_check
uv run groket gen rule my-rule --detector my_check
uv run groket gen plugin my_stats --register   # writes plugin + config entry
uv run groket gen tasks                         # ~/.groket/tasks/example_tasks.yaml
```

Rules and detectors are **user-installed**: the engine loads
``~/.groket/detectors/*.py`` and ``~/.groket/rules/*.yaml`` (and detectors
declared in ``~/.groket/plugins/*.py``). Package ``assets/config/rules.yaml`` and
``composites.yaml`` are empty stubs so the loader has a stable asset path.
Reference packs live under ``examples/detection/`` (``minimal/``, ``starters/``,
``catalog/``) — copy into ``~/.groket`` to enable. Analysis output type is
:class:`~groket.analysis.base.Finding` (rules, composites, and plugins).

### 10.2 Three “plugin” concepts (do not conflate)

| Kind | Config / field | Notes |
|------|----------------|--------|
| **Analysis plugins** | `analysis.plugins` | `module:AnalyzerClass` implementing `Analyzer`. User: `~/.groket/plugins/`; examples in `examples/analysis/plugins/`. |
| **Detectors + rules** | `@detector` + rule YAML | Engine findings. User detectors + `~/.groket/rules/`; examples in `examples/detection/`. |
| **Grok Build plugins** | `Persona.plugins` / `RunConfig.run_plugins` | Marketplace **names** on the persona/run; launch writes `plugins-manifest.json` (catalog URL + SHA); eval entrypoint installs under ``/root/.grok/plugins`` and enables via ``[plugins]`` (`capabilities/marketplace.py`, `apply.prepare_persona_plugins_dir`). UI: persona tab **Plugins**, runner pick plugins. |

**MCP** and **standalone skills** are separate persona fields (`mcp_servers` /
`skills`). MCP configure may create an implicit companion skill
(`use-<server>-mcp`, frontmatter `x-groket: groket-mcp-companion`). Those are
**hidden from skill pickers** by default but still resolvable for apply.

---

## 11. Testing

Inspired by [coredis](https://github.com/alisaifee/coredis/tree/master/tests):
domain-shaped layout, behavioural names, fixtures at **system boundaries**.

### 11.1 Layout and naming

**Mirror `groket/`** (foundational modules may live at `tests/` root):

```
tests/
  conftest.py
  test_models.py, test_parser.py, test_paths.py, test_flags.py, test_utils.py
  analysis/     engine/     capabilities/     runs/     session/     docker/     ui/
```

Examples: `tests/engine/test_loader.py`, `tests/capabilities/test_marketplace.py`,
`tests/ui/test_data_table.py` (for `groket/ui/data_table.py`). Prefer
`ui/test_text.py` over a redundant `test_ui_text.py` at the root.

- Shared fixtures live in `tests/conftest.py` (and optional per-package
  `conftest.py`). Use **`tmp_path`** for filesystem I/O; isolate `APP_HOME` so
  developer `~/.groket` never leaks into tests.
- **Naming:** §4.8 — behavioural test names; domain file paths only.

### 11.2 What to mock (boundaries only)

- **Do not mock internal groket modules** against each other.
- **Do mock external boundaries:** Docker daemon / **python-on-whales**
  (`DockerClient`), network (`urllib`, HTTP), interactive `git` credential
  prompts, wall-clock where determinism matters.
- **Default unit suite is daemon-free:** no real `docker run` / image build.
  Orchestrator and run-manager tests inject a **fake client** (stubs for `run` /
  `wait` / `logs` / `build`). A full container lifecycle is integration /
  manual work, not the default `pytest` path.
- **No real network `git clone` in unit tests.** Patch `subprocess.run` or
  inject a fake at the orchestrator boundary.
- Textual: **`app.run_test()`** for one screen or action with fixtures on disk —
  not eval launches that need Docker.

If a test needs a live daemon, gate it explicitly (e.g. `@pytest.mark.integration`
+ opt-in in CI). Default `uv run pytest tests/` must stay fast on a laptop.

### 11.3 Style of cases

- `@pytest.mark.parametrize` for multi-variant inputs.
- Async TUI: `@pytest.mark.asyncio` + `run_test()` (`pytest-asyncio`).
- Assert **outcomes** (return values, files written, messages, raised types).
- Prefer **small focused tests** over suites that import half the package only
  to raise line counts.

### 11.4 Coverage (100% is mandatory)

- **`fail_under = 100`** on ``groket`` is a **product gate**, not a stretch goal
  (§4.5a). Closing a PR or agent turn with partial coverage is incomplete work.
- Meet 100% with **domain-named** tests and real assertions (files written,
  return values, UI state, raised types). Prefer **deleting dead code** or
  extracting a small pure function over untestable TUI-only branches.
- **Refuse** (and say so) tactics that only inflate the meter: silent
  ``except Exception: pass`` sweeps, internal mocks of ``groket`` modules,
  live Docker in the default suite, lowering ``fail_under``, or package
  ``omit`` lists. Offer a **legitimate** path instead (fixture + fake client,
  pilot one action, delete unused code).
- After structural test moves or large deletes, **re-run**
  ``pytest tests/ --cov=groket --cov-fail-under=100`` before claiming done —
  suite green without coverage is not enough.

### 11.5 Quick checklist (new test)

1. File path reflects the module under test (domain folder)?
2. Function/class name states behaviour?
3. External I/O (Docker, network, git) faked at the boundary?
4. Fails for one conceptual reason?
5. Runs without a Docker daemon and without network?
6. Leaves the package on track for **100%** line coverage (§4.5a / §11.4)?
