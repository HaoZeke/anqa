# Local development targets (uv-first), shaped after alisaifee/coredis.

.PHONY: help lint lint-fix lint-complexity test test-cov clean ci install schema schema-check

help:
	@echo "groket development targets"
	@echo "  install          project venv (test+dev) + groket on PATH (uv tool)"
	@echo "  lint             ruff check + format --check + mypy (whole groket/)"
	@echo "  lint-fix        ruff autofix + format + mypy (whole groket/)"
	@echo "  lint-complexity  ruff PLR on groket (informational / debt)"
	@echo "  schema           regenerate schemas/tasks.schema.json from Pydantic"
	@echo "  schema-check     fail if committed tasks schema is out of date"
	@echo "  test             pytest"
	@echo "  test-cov         pytest with coverage report"
	@echo "  ci               lint + schema-check + test"
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

lint-fix:
	uv run ruff check --select I --fix groket tests
	uv run ruff check --fix groket tests
	uv run ruff format groket tests
	uv run mypy groket

lint-complexity:
	uv run ruff check --select PLR groket

# JSON Schema for batch/task YAML (source of truth: groket.runs.task_schema).
# Committed under schemas/ for editors; published on GitHub Pages at
# https://indynull.github.io/groket/schemas/ (see .github/workflows/pages.yml).
schema:
	uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; emit_tasks_schema(Path('schemas/tasks.schema.json'))"

schema-check:
	@tmp=$$(mktemp) && \
	  uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; import sys; emit_tasks_schema(Path(sys.argv[1]))" "$$tmp" && \
	  diff -q "$$tmp" schemas/tasks.schema.json >/dev/null || \
	  (echo "schemas/tasks.schema.json is stale — run make schema and commit" >&2; rm -f "$$tmp"; exit 1) && \
	  rm -f "$$tmp"

test:
	uv run pytest tests/ -q --tb=short

test-cov:
	uv run pytest tests/ --cov=groket --cov-report=term-missing --cov-report=html

ci: lint schema-check test

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ \
		htmlcov/ .coverage coverage.json docs/_build/
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
