# Development

Python 3.13, FastAPI with uvicorn, pydantic for the configuration and the records, croniter
for the schedule, a dependency-free page in plain JavaScript. The package is `remotebackups`
under `src/`, installed into a virtualenv with [uv](https://docs.astral.sh/uv/).

```bash
make install           # uv sync --frozen
make check             # lock check, ruff format and lint, mypy --strict, unit and web tests, coverage gate 85 %
make lock-check        # uv lock --check: pyproject.toml and uv.lock agree
make lint              # ruff format --check and ruff check, no changes
make type              # mypy --strict
make test              # unit and web tests with the coverage gate
make fmt               # ruff format and the auto-fixes
make build             # docker build -t remotebackups:dev .
make run               # build, then docker compose up with the tracked docker-compose.yaml
make test-integration  # the compose stack in compose/: real sshd, the image, pytest, then a docker stop mid-run
make clean             # remove the virtualenv, the tool caches, coverage data and __pycache__
```

`make check` is `lock-check`, `lint`, `type` and `test` in that order.

## Layout

```
src/remotebackups/
  app.py          the service: wires the parts, owns shutdown order and the remote probe
  cli.py          --version, run, check-config, print-config, print-example-config, run-once, healthcheck
  scheduler.py    the cron loop; re-reads the expression every minute, skips when paused
  clock.py        time as a dependency, so tests never sleep
  logging_setup.py
                  logging to stderr; uvicorn's access log kept to warnings
  paths.py        where files live: /config, the runtime dir, /run/secrets
  config/         schema.py (pydantic models), env.py (variables, FILE__), loader.py (merge, validate), paused.py,
                  config.example.yaml (the template written on first start)
  sync/           engine.py (one run at a time), command.py (the rsync argv), executor.py (the process,
                  signals), ssh.py (key copy, ssh config, probe), history.py (records on disk),
                  log.py (the per-run log with its cap), stats.py (--stats parsing), exit_codes.py
  truenas/        client.py (the DDP WebSocket call, blocking), service.py (cache, de-duplicated warnings),
                  models.py (pools and alerts as the UI shows them)
  web/            app.py, api.py, health.py, pages.py, sse.py, schemas.py, services.py (the protocols
                  the web layer needs), static/ (app.js, app.css, favicon.svg), templates/index.html
tests/
  unit/           one module per source module; fake rsync, ssh, ssh-keygen and sshpass binaries in tests/fakes/bin,
                  FakeClock and the control file in tests/fakes
  web/            the API through httpx against the ASGI app, with fake engine and TrueNAS
  integration/    against the compose stack: real sshd, key and password users, a stop mid-run
compose/          the integration stack and its sshd image
docs/wiki/        these pages; pushed to the GitHub wiki
```

The rule that shapes most of it: everything with a side effect (time, processes, the
network) is behind a small interface, so the unit tests run in seconds and the integration
stack is the only place a real ssh happens.

## Tests

- **Unit** (`tests/unit`): the fake binaries on `PATH` record their arguments and are
  steered through a control file under a temporary `HOME`, so a test can make `rsync` fail
  with exit 23 after printing a stats block, or make `ssh` hang. `FakeClock` moves time by
  hand. Fixtures take the real shape of the data: a run record is built with the real model,
  a config with the real schema.
- **Web** (`tests/web`): every endpoint, the CSRF guard, the SSE stream against a log file
  that grows, the custom files.
- **Integration** (`tests/integration`, `make test-integration`): the image under test
  against a real `sshd` with a key user and a password user, started by Compose; then
  `compose/stop-test.sh` starts a slow run and stops the container, asserting exit 0 within
  the grace period and a *cancelled* record.

`make check` is what the CI quality job runs. `pytest -k name` and `uv run pytest tests/web`
work as usual; `filterwarnings = error`, so a deprecation fails the test that triggers it.

## CI and the images

Every push to `main`, `develop` and `wip`, and every pull request, runs: the quality job on
Python 3.13 and 3.14 (the latter informational), an amd64 image build with a smoke test, the integration stack
against that very image, and an arm64 build. The Release workflow publishes images on pushes
to `main` and `develop` and on tags:

| Event | Tags published |
|---|---|
| push to `develop` | `develop` |
| push to `main` | `latest` |
| tag `vX.Y` | `X.Y` and `X` |
| every Monday, and on demand | `latest`, `X.Y` and `X` rebuilt from `main` without the layer cache |

Images go to Docker Hub and GHCR for amd64 and arm64, with provenance and an SBOM. The
runtime image is Alpine with rsync, openssh-client, sshpass and tini, plus the locked
virtualenv; pip and the other interpreter tooling are removed, so the only Python packages
present are the application's. To scan a build:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy image remotebackups:dev
```

Actions are pinned to commit SHAs; Dependabot keeps them, the Python lock file and the base
images current, grouped, with a 14-day cooldown after a release.

## Branches and releases

Work happens on `develop`. `wip` is the scratch branch ahead of it. A release:

1. Set `version` in `pyproject.toml` (two parts: after 2.5 comes 2.6) and turn the
   changelog's *Unreleased* section into the version with the date.
2. Commit on `develop`, merge `develop` into `main`, tag `vX.Y`, push both branches and the
   tag. The Release workflow refuses a tag that does not match the package version.
3. Create the GitHub release from the tag with the changelog section as its notes.

Commit messages are semantic (`fix:`, `feat:`, `docs:`, `ci:`, `build:`, `refactor:`,
`style:`, `chore:`) with a short title and one explanatory paragraph.

## The wiki

These pages live in `docs/wiki/` in the repository, so they are versioned with the code
and reviewed like it. Publishing them:

```bash
git clone git@github.com:EduOnieva/remotebackups.wiki.git /tmp/wiki
cp docs/wiki/*.md /tmp/wiki/ && cd /tmp/wiki && git add -A && git commit -m "Sync from docs/wiki" && git push
```

## Contributing

Issues are disabled on the repository. A pull request against `develop` with a passing
`make check` is welcome; a security problem goes through the private channel in
[SECURITY.md](https://github.com/EduOnieva/remotebackups/blob/main/SECURITY.md).
