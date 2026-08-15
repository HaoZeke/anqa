# Local development recipes (uv + cargo).

# List recipes.
default:
    @just --list

# Project venv for lint/test. Product install: uv tool install --editable .
install:
    uv sync --group test --group dev

# ruff + format check + mypy + fluent + typing policy.
lint:
    uv run ruff check --select I groket tests
    uv run ruff check groket tests
    uv run ruff format --check groket tests
    uv run mypy groket
    uv run python scripts/check_fluent.py
    uv run python scripts/check_typing_policy.py

# Autofix ruff + format + mypy.
lint-fix:
    uv run ruff check --select I --fix groket tests
    uv run ruff check --fix groket tests
    uv run ruff format groket tests
    uv run mypy groket

# Size-limit report only (AGENTS §4.6). Not part of just lint / CI.
lint-complexity:
    uv run ruff check --preview \
        --select PLR0911,PLR0912,PLR0913,PLR0915,PLR0904 \
        groket

# Regenerate schemas/*.schema.json from Pydantic.
schema:
    uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; emit_tasks_schema(Path('schemas/tasks.schema.json'))"
    uv run python -c "from pathlib import Path; from groket.engine.rule_schema import emit_rules_schema; emit_rules_schema(Path('schemas/rules.schema.json'))"
    uv run python -c "from pathlib import Path; from groket.config import emit_config_schema; emit_config_schema(Path('schemas/config.schema.json'))"

# Fail if committed schemas are out of date.
schema-check:
    #!/usr/bin/env bash
    set -euo pipefail
    check() {
      local emit="$1" dest="$2"
      local tmp
      tmp=$(mktemp)
      uv run python -c "$emit" "$tmp"
      if ! diff -q "$tmp" "$dest" >/dev/null; then
        echo "$dest is stale — run just schema and commit" >&2
        rm -f "$tmp"
        exit 1
      fi
      rm -f "$tmp"
    }
    check 'from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; import sys; emit_tasks_schema(Path(sys.argv[1]))' schemas/tasks.schema.json
    check 'from pathlib import Path; from groket.engine.rule_schema import emit_rules_schema; import sys; emit_rules_schema(Path(sys.argv[1]))' schemas/rules.schema.json
    check 'from pathlib import Path; from groket.config import emit_config_schema; import sys; emit_config_schema(Path(sys.argv[1]))' schemas/config.schema.json

# Validate examples/ packs.
examples-check:
    uv run python scripts/check_examples.py

# Regenerate groket-hud/assets/textual-themes.json.
hud-themes:
    uv run python scripts/gen_textual_themes.py

# Fail if the HUD Textual theme map is stale.
hud-themes-check:
    #!/usr/bin/env bash
    set -euo pipefail
    tmp=$(mktemp)
    uv run python scripts/gen_textual_themes.py "$tmp"
    if ! diff -q "$tmp" groket-hud/assets/textual-themes.json >/dev/null; then
      echo "groket-hud/assets/textual-themes.json is stale — run just hud-themes and commit" >&2
      rm -f "$tmp"
      exit 1
    fi
    rm -f "$tmp"

# Theme map + rustfmt + clippy + HUD cargo test (+ cov if installed).
hud-check: hud-themes-check
    cargo fmt --check --manifest-path groket-hud/Cargo.toml
    CARGO_INCREMENTAL=0 cargo clippy --manifest-path groket-hud/Cargo.toml --all-targets -- -D warnings
    CARGO_INCREMENTAL=0 cargo test --manifest-path groket-hud/Cargo.toml
    just hud-cov

# cargo llvm-cov fail-under on non-paint HUD logic (optional).
hud-cov:
    #!/usr/bin/env bash
    set -euo pipefail
    if cargo llvm-cov --version >/dev/null 2>&1; then
      CARGO_INCREMENTAL=0 cargo llvm-cov --manifest-path groket-hud/Cargo.toml --lib \
        --fail-under-lines 70 \
        --ignore-filename-regex 'src/(app|view|typo|main|x11focus|control)\.rs' \
        --summary-only
      rm -rf groket-hud/target/llvm-cov-target groket-hud/target/llvm-cov
    else
      echo "hud-cov: cargo-llvm-cov not installed; skip fail-under (fmt/clippy/test already ran)"
    fi

# Rebuild brand/ from the geometric mark.
brand:
    uv run --group brand python brand/build.py

# Optional Limited API listwalk (remote builder only).
ext:
    GROKET_BUILD_EXT=1 uv run --with setuptools python setup.py build_ext --inplace

# cargo test groket-core (remote builder; not this laptop).
native-check:
    cargo test --manifest-path native/groket-core/Cargo.toml

# pytest (no coverage flag).
test:
    uv run pytest tests/ -q --tb=short

# pytest with coverage report (`fail_under` applies).
test-cov:
    uv run pytest tests/ --cov=groket --cov-report=term-missing --cov-report=html

# Set the product version in pyproject, __init__, and both crates.
bump version:
    uv run python scripts/bump_version.py {{version}}

# This-platform wheel into dist/ (needs Rust).
wheel:
    uv build --wheel

# cibuildwheel for this host (Linux needs Docker).
wheels:
    uvx cibuildwheel

# Source distribution into dist/.
sdist:
    uv build --sdist

# lint + schema-check + hud-check + examples-check + test.
ci: lint schema-check hud-check examples-check test

# Python caches plus groket-hud cargo target.
clean:
    rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ \
        htmlcov/ .coverage coverage.json docs/_build/
    find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
    cargo clean --manifest-path groket-hud/Cargo.toml || true
