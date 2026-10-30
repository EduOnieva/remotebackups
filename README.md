# remotebackups

Scheduled rsync backups of your home-server folders to another host, with a web UI.

One container. Mount the folders, point it at a host you can SSH into, and it keeps a copy
there: on a schedule, or when you press **Run**. The destination can be anything with `sshd`
and `rsync`: a NAS (TrueNAS gets extra status), a second server at a friend's place reached
through Tailscale, or a machine on your LAN.

- **Scheduled and manual runs.** A cron expression for the schedule, a Run button for now,
  a *Run with delete* button when you want the backup to drop files you deleted locally, and
  a *Dry run* button that reports what a run would transfer. A folder can be paused from the
  UI until the container restarts.
- **Stop.** A running backup can be stopped from the UI; rsync keeps partial files and the
  next run continues where it stopped.
- **Live log and history.** Every run has its own log, streamed live while it runs and kept
  for 40 days with what happened to each folder: files, bytes, exit codes explained.
- **Honest status.** `/healthz` for Docker, `/api/status` for your dashboards, and the SSH
  login is probed every five minutes so you see a sleeping NAS before a run fails.
- **Safe defaults.** Key authentication, host keys pinned on first use, rsync and ssh never
  see the application's secrets, and `--delete` is never part of a scheduled run.
- **TrueNAS SCALE aware.** Optional card with each pool's usage, health and last scrub plus the
  current alerts, from the TrueNAS API; `/api/pool_status` keeps the v1 shape for dashboards.

## Quick start

1. Create a folder for the container's state. Your SSH private key stays where it is: the
   compose file below mounts that one file read-only at `/run/secrets/ssh_key`, the place the
   container looks by default.

   ```bash
   mkdir -p remotebackups/config
   ```

   The matching public key goes into the destination user's `authorized_keys` (on TrueNAS:
   the user's *Public SSH Key* field). The key must have no passphrase, since nobody is there
   to type it. A key used only for these backups is the safer choice:
   `ssh-keygen -t ed25519 -N "" -f ~/.ssh/remotebackups`, then mount that file instead.
   Compose's own `secrets:` feature also lands files in `/run/secrets`, so
   `secrets: {ssh_key: {file: /home/you/.ssh/id_ed25519}}` works without any other setting.

   Write the host path of the key in full. Compose expands `~` to the home of whoever runs the
   deploy, which is root under `sudo` or Portainer, and Docker turns a bind mount whose source
   does not exist into an empty directory. The UI then reports
   `No SSH key at /run/secrets/ssh_key`; `docker exec remotebackups ls -la /run/secrets` shows
   the directory.

2. Save a compose file next to it. For a destination you can reach directly:

   ```yaml
   services:
     remotebackups:
       image: eduonieva/remotebackups:latest
       container_name: remotebackups
       restart: unless-stopped
       stop_grace_period: 30s
       security_opt:
         - no-new-privileges:true
       ports:
         - "8080:8080"
       volumes:
         - ./config:/config
         - /home/you/.ssh/id_ed25519:/run/secrets/ssh_key:ro
         - /srv/documents:/data/documents:ro
         - /srv/photos:/data/photos:ro
       environment:
         - REMOTE_HOST=192.168.1.20
         - REMOTE_USER=backup
         - TZ=Europe/Madrid
   ```

   For a destination reached through Tailscale, use
   [`docker-compose.tailscale.yaml`](docker-compose.tailscale.yaml): the app runs behind the
   official Tailscale sidecar, and the UI is then reachable only from your tailnet.

3. `docker compose up -d`. The first start writes a commented `config/config.yaml`. Add the
   folders to back up and they are picked up before the next run, no restart needed:

   ```yaml
   folders:
     - source: /data/documents
       destination: /mnt/backup/documents
     - source: /data/photos
       destination: /mnt/backup/photos
   ```

4. Open `http://<host>:8080`, press **Run**, watch the log.

## Configuration

Settings come from three layers: built-in defaults, then `/config/config.yaml`, then
environment variables. The recommended split is connection and secrets in the environment,
folders and rsync options in the file. `remotebackups print-config --redact` shows the result.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `REMOTE_HOST` | required | Destination host: IP, DNS or MagicDNS name |
| `REMOTE_USER` | required | SSH user on the destination |
| `REMOTE_PORT` | `22` | SSH port |
| `SSH_KEY` | `/run/secrets/ssh_key` | Path of the private key inside the container; a bare name is looked up under `/run/secrets` |
| `SSH_PASSWORD` | | Password authentication instead of a key. Works, but a key is the recommendation |
| `SSH_HOST_KEY_POLICY` | `tofu` | `tofu` pins the host key on first use and refuses changes; `strict` requires a known key; `insecure` ignores host keys |
| `CRON_SCHEDULE` | `30 2 * * 1` | When scheduled runs happen. Empty disables them |
| `SCHEDULED_RSYNC_TIMEOUT` | `3600` | Seconds of rsync inactivity before a *scheduled* run gives up on a folder. `0` disables. Manual runs never time out |
| `RSYNC_EXTRA_OPTIONS` | | Extra rsync flags for every folder, e.g. `--bwlimit=20m` |
| `TRUENAS_API_KEY` | | Enables the TrueNAS status card and `/api/pool_status` details |
| `TRUENAS_HOST` | `REMOTE_HOST` | TrueNAS address when it differs from the SSH host |
| `TRUENAS_POOL_ALIASES` | | Rename pools in the response: `tank:disk-0,vault:disk-1` |
| `TRUENAS_VERIFY_TLS` | `false` | Verify the TrueNAS certificate (home NAS boxes usually use a self-signed one) |
| `TRUENAS_ENABLED` | `auto` | `auto` means on when an API key is set |
| `HISTORY_KEEP_DAYS` | `40` | Run records and logs older than this are deleted |
| `UI_DARK_THEME` | `true` | |
| `UI_LOCALE` | browser's | Date format in the UI as a BCP 47 tag, e.g. `es-ES` |
| `UI_CLOCK` | `24h` | `24h` or `12h` |
| `WEB_PORT` | `8080` | Only needed when another service in the same network namespace uses 8080 |
| `TZ` | `Etc/UTC` | Schedule and log timestamps |
| `LOG_LEVEL` | `INFO` | |
| `FILE__<VAR>` | | Read `<VAR>` from a file, e.g. `FILE__TRUENAS_API_KEY=/run/secrets/truenas` |

### config.yaml

Every key is optional; the file written on first start lists them all with comments.

```yaml
remote:
  host_key_policy: tofu
schedule:
  cron: "30 2 * * 1"
  rsync_timeout: 3600
rsync:
  options: ["-a", "-v", "-z", "--partial", "--stats"]
  preserve_ownership: false   # see "TrueNAS and other non-root destinations"
  extra_options: []
  excludes: ["*.Trash*", "lost+found", "System Volume Information", ".DS_Store", "Thumbs.db", "desktop.ini", ".venv"]
folders:
  - source: /data/documents          # path inside the container
    destination: /mnt/backup/documents
    name: documents                  # optional; defaults to the last part of source
    excludes: ["*.tmp"]              # added to rsync.excludes
    extra_options: []
    allow_delete: true               # false: "Run with delete" skips --delete for this folder
    enabled: true
truenas:
  pool_aliases: {tank: disk-0}
  cache_seconds: 30
ui:
  dark_theme: true
  locale: es-ES                    # dates in the UI; default: the browser's locale
  clock: 24h
history:
  keep_days: 40
  max_log_bytes: 52428800
```

Mistakes are reported with their path, for example
`folders[1].source: source must be an absolute path (got 'data/photos')`, and the container
exits so you notice. `remotebackups check-config` validates without starting anything.

### How a run works

For each enabled folder, in order, one `rsync` is started over SSH:

```
rsync -a -v -z --partial --stats --no-owner --no-group [--delete] [--timeout=N] --exclude=... \
  -e "ssh -F /run/remotebackups/ssh/config" /data/documents/ backup@host:/mnt/backup/documents/
```

- `--delete` is added only to a manual *Run with delete*, and only for folders with
  `allow_delete: true`. Scheduled runs never delete.
- `--timeout` is added only to scheduled runs, so a NAS that fell asleep cannot hold a
  scheduled run forever, while a manual run you are watching is left alone.
- `--dry-run` is added by the *Dry run* button (or `dry_run` in the API body, which can be
  combined with `delete` to preview a deletion): rsync reports what it would transfer and
  touches nothing. The run is recorded like any other, marked as a dry run.
- Editing `schedule.cron` in `config.yaml` moves the next scheduled run within a minute.
- **Stop** sends SIGTERM to the running rsync and skips the remaining folders. `--partial`
  keeps what was transferred; the next run resumes.
- Only one run at a time. A second start is refused, and a scheduled run that finds another
  run active is skipped with a warning.

## The web UI and API

The page shows the current state with progress during a run, the last and next run, whether
the destination is reachable (with a **Check now** button), the TrueNAS card when enabled, the
log of the selected run with a folded table of what happened to each of its folders, and the history
of runs and the configured folders in two cards that fold. The log follows new lines while you
are at the bottom and offers a "jump to bottom" pill when you scroll up; the file name in its
title downloads the whole file. A run you selected stays on screen when a scheduled run
starts; click the new run to follow it. Buttons: **Run**, **Run with delete**, **Dry run**,
**Stop**, **Clear history**. The layout works on a phone.

Each row of the Folders card has a **Pause** box. Ticking it pauses the folder: runs skip
it until the container restarts, which forgets every pause, and the card says so while any
folder is paused. The permanent switch stays in `config.yaml` (`enabled: false`); a folder
disabled there has no box, since it cannot be turned on from the UI. Pausing is refused
while a run is active, since it could only apply to the next one.

| Endpoint | What it returns |
|---|---|
| `GET /healthz` | 200 while the scheduler loop is alive, 503 otherwise (for Docker's HEALTHCHECK) |
| `GET /api/status` | State, current and last run, next scheduled run, remote reachability |
| `GET /api/folders` | The configured folders, whether each source exists and whether it is paused |
| `PUT /api/folders/{name}` | Body `{"paused": true}` or `false`. 409 while a run is active, 400 for a folder disabled in `config.yaml` |
| `POST /api/runs` | Start a run; body `{"delete": false, "dry_run": false, "folders": ["documents"]}` is optional. 409 while a run is active |
| `POST /api/runs/current/cancel` | Stop the active run |
| `POST /api/remote/check` | Probe the SSH login now instead of waiting for the periodic check |
| `GET /api/runs?limit=20` | Run history, newest first |
| `GET /api/runs/{id}` | One run with its per-folder results and rsync statistics |
| `GET /api/runs/{id}/log?offset=0` | The log as text, in chunks |
| `GET /api/runs/{id}/log/stream` | The log as Server-Sent Events, live while the run is going |
| `DELETE /api/runs` | Delete all history |
| `GET /api/config` | The effective configuration, secrets masked |
| `GET /api/truenas` | Pools (usage, health, scrub) and alerts as the TrueNAS card shows them |
| `GET /api/pool_status` | `{"sync_errors": N, "alerts": M, "<pool>": "X TB"}`, or `{"sync_errors": N, "truenas": "disabled"}` |
| `GET /docs` | Interactive API documentation |

The UI has no login. Keep it where only you can reach it: on your tailnet (the Tailscale
compose file does exactly that), on your LAN, or behind your reverse proxy's authentication.
`POST` and `DELETE` calls must carry `Content-Type: application/json` and come from the same
origin, so a page on another site cannot start or stop a run through your browser; from a
script, add the header: `curl -X POST -H 'Content-Type: application/json' host:8080/api/runs`.

## Networking

- **Tailscale sidecar** ([`docker-compose.tailscale.yaml`](docker-compose.tailscale.yaml)):
  the official `tailscale/tailscale` container joins your tailnet, and remotebackups shares
  its network namespace. Set `REMOTE_HOST` to the destination's tailnet IP or MagicDNS name.
  The UI is at `http://<node-name>:8080` from any device on the tailnet and is not published
  on the LAN. Tailscale Serve can put HTTPS in front of it if you want.
- **Host already on the tailnet or a VPN**: use the plain compose file; the container reaches
  whatever the host can reach.
- **LAN**: the plain compose file with the destination's LAN address.

## TrueNAS and other non-root destinations

Follow the TrueNAS SCALE documentation: enable the SSH service, create a user with a home
directory on a dataset, shell access and *SSH Access*, paste your public key in its
*Public SSH Key* field, and give it write permission on the destination dataset.

A user that is not root cannot change file ownership, so with rsync's plain `-a` every run
would end with "partial transfer due to error" (exit 23). That is why
`rsync.preserve_ownership` defaults to `false`, which adds `--no-owner --no-group`. Set it to
`true` only when the destination user is root.

With `TRUENAS_API_KEY` set, the UI shows the NAS alert count and the free space of each
pool, and `/api/pool_status` carries the same numbers for dashboards. Pool names can be
renamed with `TRUENAS_POOL_ALIASES` so dashboards keep stable keys.

## Operations

- **Logs.** The application logs to stdout (`docker logs remotebackups`); the compose files
  rotate them through Docker. Run logs are files under `config/runs/`, one per run, deleted
  after `HISTORY_KEEP_DAYS`.
- **State.** Everything is under `/config`: `config.yaml`, `ssh/` (the pinned
  `known_hosts`), `runs/`. Back that folder up and the container is disposable.
- **Changed destination host key.** With the default `tofu` policy a changed key fails the
  connection and the status says so. If the server was reinstalled, delete its line from
  `config/ssh/known_hosts`.
- **Updating.** `docker compose pull && docker compose up -d`. Image tags follow the
  branches: `latest` is `main`, which moves at releases; `2.1` pins a release and `2` its
  newest; `develop` is the `develop` branch, where changes land first. On Docker Hub
  (`eduonieva/remotebackups`) and GHCR (`ghcr.io/eduonieva/remotebackups`), for amd64 and arm64.
- **CLI.** `docker exec remotebackups remotebackups check-config | print-config --redact |
  print-example-config | run-once [--delete] [--dry-run] [--folder NAME]`. After an upgrade,
  `print-example-config` shows the current template with any new keys; nothing in `/config`
  is rewritten behind your back.

## Migrating from version 1 (rsync-tailscale-docker)

Version 2 is a rewrite; the v1 code is tagged `v1.2.0`. What changes for a v1 deployment:

- The routes JSON becomes `folders:` in `config/config.yaml`; `ROUTES_FILE`, `SSH_KEY_FILE`
  and `/mnt/ssh_keys` are gone. Mount the key file read-only at `/run/secrets/ssh_key` instead.
- `REMOTE_API_TOKEN` is now `TRUENAS_API_KEY`; `TRUENAS_DISABLED` is gone (TrueNAS is on
  when a key is set). `TRUENAS_POOL_ALIASES` is unchanged.
- The UI moved from port 80 to 8080 inside the container; update your port mapping.
- rsync runs with `--no-owner --no-group` unless `rsync.preserve_ownership: true`.
- `/api/pool_status` keeps its shape. `sync_errors` now counts the folders that failed in
  the last run instead of error lines in a log file.
- Run logs are per run under `config/runs/`; the old `sync.log` is not read.

## Development

```bash
make install           # uv sync
make check             # lock check, ruff, mypy --strict, unit and web tests (coverage gate 85 %)
make build             # docker build -t remotebackups:dev .
make test-integration  # real sshd + the image under test, then a docker stop mid-run
```

Unit tests drive fake `rsync`, `ssh`, `ssh-keygen` and `sshpass` binaries, so they run
anywhere in a few seconds. The integration stack in `compose/` is what CI runs before an
image is published.

Work happens on the `develop` branch, which publishes the `develop` image on every push.
A release merges `develop` into `main`, which publishes `latest`, and tags it `vX.Y`, which
publishes the numbered images. Versions have two parts: after 2.1 comes 2.2.

## Acknowledgements

This project started as a fork of [insrch/rsync](https://hub.docker.com/r/insrch/rsync).
Version 2 shares no code with it but owes it the idea.

## License

[MIT](LICENSE)
