# Migrating from v1

Version 2 is a rewrite of rsync-tailscale-docker: one Python application replaces the bash
sync script, busybox cron and the stdlib web server. The v1 code is tagged `v1.2.0`. The
backups themselves do not change: the destination directories are the same plain copies,
and the first v2 run against them transfers only what differs.

## What changes

| v1 | v2 |
|---|---|
| `ROUTES_FILE` and the routes JSON | `folders:` in `/config/config.yaml` |
| `SSH_KEY_FILE` with `~/.ssh` mounted at `/mnt/ssh_keys` | the one key file mounted read-only at `/run/secrets/ssh_key` |
| `REMOTE_API_TOKEN` | `TRUENAS_API_KEY`, ideally as `FILE__TRUENAS_API_KEY` |
| `TRUENAS_DISABLED=true` | nothing: TrueNAS is on when a key is set, `TRUENAS_ENABLED=false` forces it off |
| `TRUENAS_POOL_ALIASES` | unchanged |
| UI on port 80 inside the container | 8080; update the port mapping |
| rsync with plain `-a` | `--no-owner --no-group` unless `rsync.preserve_ownership: true` |
| `sync.log`, rotated per run | one record and one log per run under `/config/runs/`; the old file is not read |
| `/api/pool_status` | same shape; `sync_errors` counts the folders that failed in the last run, not error lines |

## Converting the routes

A v1 routes file:

```json
[
  {"source": "/data/rsync/documents", "destination": "/mnt/tank/backup/documents"},
  {"source": "/data/rsync/photos", "destination": "/mnt/tank/backup/photos", "note": "no deletes"}
]
```

becomes, in `config.yaml`:

```yaml
folders:
  - source: /data/rsync/documents
    destination: /mnt/tank/backup/documents
  - source: /data/rsync/photos
    destination: /mnt/tank/backup/photos
    allow_delete: false
```

Keep the same container paths under `/data` and the same destinations, and nothing is
copied twice. Per-folder `excludes`, `extra_options`, `allow_delete` and `enabled` are new.

## The compose file

Replace the image with `eduonieva/remotebackups:latest`, drop the `/mnt/ssh_keys` and
routes mounts, mount the key file at `/run/secrets/ssh_key`, map port 8080, and keep the
`/config` volume. The first start writes a commented `config.yaml` next to the old files; the
old files are ignored and can be deleted once the first v2 run is green. The tracked
[`docker-compose.yaml`](https://github.com/EduOnieva/remotebackups/blob/main/docker-compose.yaml)
and [`docker-compose.tailscale.yaml`](https://github.com/EduOnieva/remotebackups/blob/main/docker-compose.tailscale.yaml)
are the reference, hardening included.
