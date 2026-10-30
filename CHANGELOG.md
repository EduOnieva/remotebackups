# Changelog

## 2.0.0 (unreleased)

A rewrite of rsync-tailscale-docker as **remotebackups**: one Python application replaces
the bash sync script, busybox cron and the stdlib web server.

### Added
- Stop button: a running backup can be cancelled; rsync keeps partial files and resumes next time.
- Per-run history with a log per run, kept for 40 days, and per-folder results with rsync statistics.
- Live log streaming (Server-Sent Events) in the web UI.
- `config.yaml` with validated folders and options, re-read before every run; a commented example is written on first start.
- `/healthz` for Docker, `/api/runs` history and detail endpoints, `/api/config` (secrets masked), `/docs`.
- The private key is mounted read-only at `/run/secrets/ssh_key` (or `SSH_KEY`), never copied into `/config`.
- Host keys pinned on first use (`SSH_HOST_KEY_POLICY=tofu`), optional password authentication (`SSH_PASSWORD`), `FILE__<VAR>` secrets.
- Remote reachability probe every five minutes, shown in the UI.
- A Tailscale sidecar compose file with a health-checked dependency, and a plain compose file for direct destinations.
- Multi-arch images (amd64, arm64) on Docker Hub and GHCR, built by GitHub Actions; integration tests against a real sshd.

### Changed
- Image, package and repository renamed to `remotebackups`.
- The UI listens on port 8080 inside the container (was 80).
- rsync runs with `--no-owner --no-group` by default (`rsync.preserve_ownership: false`), the right choice for non-root destination users such as on TrueNAS.
- `REMOTE_API_TOKEN` is now `TRUENAS_API_KEY`; TrueNAS status is on whenever a key is set.
- `sync_errors` in `/api/pool_status` counts the folders that failed in the last run.
- Application logs go to stdout only; Docker rotates them.

### Removed
- `ROUTES_FILE` and the flat routes JSON, `SSH_KEY_FILE` and `/mnt/ssh_keys`, `TRUENAS_DISABLED`, `sync.log`.

## 1.2.0

Hardened TrueNAS client with pool aliases and an opt-out switch, pinned base image and test
dependencies, supervised cron and web server processes, tracked `.dockerignore`, pytest
configuration fixes, README cleanup.

## 1.1.0

Fixes to the v1 image: startup no longer aborts on a missing API token or an unreachable
remote, log lines are no longer duplicated, aborted runs are reported, `sync.log` rotates per
run, and scheduled runs get an rsync inactivity timeout.

## 1.0.0

The original rsync-over-Tailscale image: bash sync script driven by cron, a small web UI and
TrueNAS pool status.
