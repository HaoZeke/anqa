# Local development targets (uv-first), shaped after alisaifee/coredis.

.PHONY: help lint lint-fix lint-complexity test test-cov clean ci install schema schema-check examples-check

help:
	@echo "groket development targets"
	@echo "  install          project venv (test+dev) + groket on PATH (uv tool)"
	@echo "  lint             ruff check + format --check + mypy + policy scripts"
	@echo "  lint-fix        ruff autofix + format + mypy (whole groket/)"
	@echo "  lint-complexity  size limits only (args/returns/branches/statements/methods); debt report"
	@echo "  schema           regenerate schemas/*.schema.json from Pydantic"
	@echo "  schema-check     fail if committed schemas are out of date"
	@echo "  examples-check   validate examples/ packs (schema + import contract)"
	@echo "  test             pytest"
	@echo "  test-cov         pytest with coverage report"
	@echo "  ci               lint + schema-check + examples-check + test"
	@echo "  clean            caches and build artefacts"

install:
	# Editable project + test/dev groups in .venv (uv run pytest / make test).
	uv sync --group test --group dev
	# Console script on the user PATH (typically ~/.local/bin/groket).
	uv tool install --force --editable .
	@echo "groket installed — try: groket --help   (or: uv run groket)"

lint:
	uv run ruff check --select I groket tests
	uv run ruff check groket tests
	uv run ruff format --check groket tests
	uv run mypy groket
	uv run python scripts/check_fluent.py
	uv run python scripts/check_typing_policy.py

lint-fix:
	uv run ruff check --select I --fix groket tests
	uv run ruff check --fix groket tests
	uv run ruff format groket tests
	uv run mypy groket

# Size-limit rules only (AGENTS §4.6). Not part of make lint / CI.
# PLR0904 (public methods) needs ruff preview. Exit non-zero while debt remains.
lint-complexity:
	uv run ruff check --preview \
		--select PLR0911,PLR0912,PLR0913,PLR0915,PLR0904 \
		groket

# JSON Schema for batch tasks + detection rules (Pydantic sources).
# Committed under schemas/ for editors; published on GitHub Pages at
# https://indynull.github.io/groket/schemas/ (see .github/workflows/pages.yml).
schema:
	uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; emit_tasks_schema(Path('schemas/tasks.schema.json'))"
	uv run python -c "from pathlib import Path; from groket.engine.rule_schema import emit_rules_schema; emit_rules_schema(Path('schemas/rules.schema.json'))"

schema-check:
	@tmp=$$(mktemp) && \
	  uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; import sys; emit_tasks_schema(Path(sys.argv[1]))" "$$tmp" && \
	  diff -q "$$tmp" schemas/tasks.schema.json >/dev/null || \
	  (echo "schemas/tasks.schema.json is stale — run make schema and commit" >&2; rm -f "$$tmp"; exit 1) && \
	  rm -f "$$tmp"
	@tmp=$$(mktemp) && \
	  uv run python -c "from pathlib import Path; from groket.engine.rule_schema import emit_rules_schema; import sys; emit_rules_schema(Path(sys.argv[1]))" "$$tmp" && \
	  diff -q "$$tmp" schemas/rules.schema.json >/dev/null || \
	  (echo "schemas/rules.schema.json is stale — run make schema and commit" >&2; rm -f "$$tmp"; exit 1) && \
	  rm -f "$$tmp"

examples-check:
	uv run python scripts/check_examples.py

test:
	uv run pytest tests/ -q --tb=short

test-cov:
	uv run pytest tests/ --cov=groket --cov-report=term-missing --cov-report=html

ci: lint schema-check examples-check test

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ \
		htmlcov/ .coverage coverage.json docs/_build/
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
