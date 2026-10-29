UV ?= uv
PY_TARGETS := src/remotebackups tests

.PHONY: install fmt lint type test lock-check check clean

install:  ## Create the virtualenv from the lock file
	$(UV) sync --frozen

fmt:  ## Format and auto-fix
	$(UV) run ruff format $(PY_TARGETS)
	$(UV) run ruff check --fix $(PY_TARGETS)

lint:  ## Formatting and lint checks, no changes
	$(UV) run ruff format --check $(PY_TARGETS)
	$(UV) run ruff check $(PY_TARGETS)

type:  ## Strict type check
	$(UV) run mypy

test:  ## Unit and web tests with the coverage gate
	$(UV) run pytest --cov --cov-report=term-missing

lock-check:  ## Fail if pyproject.toml and uv.lock disagree
	$(UV) lock --check

check: lock-check lint type test  ## Everything CI runs

clean:
	rm -rf .venv .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
