# Local development targets (uv-first), shaped after alisaifee/coredis.

.PHONY: help lint lint-fix lint-complexity test test-cov clean ci install

help:
	@echo "groket development targets"
	@echo "  install          project venv (test+dev) + groket on PATH (uv tool)"
	@echo "  lint             ruff check + format --check + mypy (whole groket/)"
	@echo "  lint-fix        ruff autofix + format + mypy (whole groket/)"
	@echo "  lint-complexity  ruff PLR on groket (informational / debt)"
	@echo "  test             pytest"
	@echo "  test-cov         pytest with coverage report"
	@echo "  ci               lint + test"
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

test:
	uv run pytest tests/ -q --tb=short

test-cov:
	uv run pytest tests/ --cov=groket --cov-report=term-missing --cov-report=html

ci: lint test

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ \
		htmlcov/ .coverage coverage.json docs/_build/
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
