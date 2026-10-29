UV ?= uv
PY_TARGETS := src/remotebackups tests
IMAGE ?= remotebackups:dev
TEST_STACK := docker compose -f compose/docker-compose.test.yml

.PHONY: install fmt lint type test lock-check check build run test-integration clean

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

check: lock-check lint type test  ## Everything the quality CI job runs

build:  ## Build the image for this machine
	docker build -t $(IMAGE) .

run: build  ## Run the app-only compose file against the local image
	IMAGE=$(IMAGE) docker compose up

test-integration:  ## Real sshd + the image under test; then docker stop mid-run
	$(TEST_STACK) down -v --remove-orphans
	$(TEST_STACK) build
	$(TEST_STACK) up --abort-on-container-exit --exit-code-from tests tests
	./compose/stop-test.sh
	$(TEST_STACK) down -v

clean:
	rm -rf .venv .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
