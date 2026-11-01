# Configuration

Three layers, each overriding the one before: built-in defaults, then `/config/config.yaml`,
then environment variables. The recommended split is connection and secrets in the
environment, folders and rsync options in the file.

`config.yaml` is read at startup and again before every run and every minute by the
scheduler, so editing folders or the cron expression never needs a restart. A file that does
not parse or fails validation is reported once in the log with the path of the mistake, for
example `folders[1].source: source must be an absolute path (got 'data/photos')`. While the
file is broken the scheduler skips its runs and a manual run answers 400 with the same
message; the log says when the file is readable again. At startup a broken file stops the
container with exit code 2 so you notice.

Two commands help:

```bash
docker exec remotebackups remotebackups check-config          # validate and summarise
docker exec remotebackups remotebackups print-config --redact  # the effective result as YAML
```

## Environment variables

| Variable | Default | Detail |
|---|---|---|
| `REMOTE_HOST` | required | Destination host: IP, DNS or MagicDNS name |
| `REMOTE_USER` | required | SSH user on the destination |
| `REMOTE_PORT` | `22` | SSH port |
| `SSH_KEY` | `/run/secrets/ssh_key` | Path of the private key inside the container. A bare name is looked up under `/run/secrets` |
| `SSH_PASSWORD` | | Password authentication through `sshpass` instead of a key. Works; a key is the recommendation |
| `SSH_HOST_KEY_POLICY` | `tofu` | `tofu` pins the host key on first use and refuses changes; `strict` requires an already known key; `insecure` ignores host keys |
| `CRON_SCHEDULE` | `30 2 * * 1` | Five-field cron expression in `TZ`. Empty disables scheduled runs |
| `SCHEDULED_RSYNC_TIMEOUT` | `3600` | Seconds of rsync inactivity before a *scheduled* run gives up on a folder (`--timeout`). `0` disables. Manual runs never time out |
| `RSYNC_EXTRA_OPTIONS` | | Extra rsync flags for every folder, split like a shell line: `--bwlimit=20m --chmod=D755,F644` |
| `TRUENAS_API_KEY` | | Enables the TrueNAS card and the pool details in `/api/pool_status` |
| `TRUENAS_HOST` | `REMOTE_HOST` | TrueNAS address when the API is not on the SSH host; may carry a port, `nas:8443` |
| `TRUENAS_POOL_ALIASES` | | Rename pools in the UI and the API: `tank:disk-0,vault:disk-1` |
| `TRUENAS_VERIFY_TLS` | `false` | Verify the TrueNAS certificate; home NAS boxes usually have a self-signed one |
| `TRUENAS_ENABLED` | `auto` | `auto` means on when an API key is set; `true` without a key is an error |
| `HISTORY_KEEP_DAYS` | `40` | Run records and logs older than this are deleted at startup and after every run |
| `UI_DARK_THEME` | `true` | Dark or light theme |
| `UI_LOCALE` | browser's | Date format in the UI as a BCP 47 tag, `es-ES`, `en-GB`, `de` |
| `UI_CLOCK` | `24h` | `24h` or `12h` |
| `WEB_PORT` | `8080` | Only needed when another service in the same network namespace uses 8080, as with a Tailscale sidecar and Serve |
| `TZ` | `Etc/UTC` | The schedule and every timestamp in logs |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` for the application log on stdout |
| `CONFIG_DIR` | `/config` | Where `config.yaml`, `ssh/` and `runs/` live; only for unusual layouts |
| `FILE__<VAR>` | | Read `<VAR>` from the named file; wins over a plain `<VAR>`. The way to pass `TRUENAS_API_KEY` and `SSH_PASSWORD` |

Every variable except `WEB_PORT`, `TZ`, `LOG_LEVEL` and `CONFIG_DIR` has a matching key in
`config.yaml`; the table below says which.

## config.yaml

Every key is optional. The file written on first start lists them all with comments;
`remotebackups print-example-config` prints the current template after an upgrade. Unknown
keys are an error, so a typo cannot be silently ignored.

```yaml
remote:
  host: 192.168.1.20          # REMOTE_HOST
  user: backup                # REMOTE_USER
  port: 22                    # REMOTE_PORT
  ssh_key: null               # SSH_KEY
  password: null              # SSH_PASSWORD; prefer FILE__SSH_PASSWORD in the environment
  host_key_policy: tofu       # SSH_HOST_KEY_POLICY
  host_key: null              # a pinned "ssh-ed25519 AAAA..." line; implies strict

schedule:
  cron: "30 2 * * 1"          # CRON_SCHEDULE; empty or null disables scheduled runs
  rsync_timeout: 3600         # SCHEDULED_RSYNC_TIMEOUT

rsync:
  options: ["-a", "-v", "-z", "--partial", "--stats"]
  preserve_ownership: false   # true adds nothing; false adds --no-owner --no-group
  extra_options: []           # RSYNC_EXTRA_OPTIONS; appended to every folder
  excludes: ["*.Trash*", "lost+found", "System Volume Information", ".DS_Store", "Thumbs.db", "desktop.ini", ".venv"]

folders:
  - source: /data/documents          # path inside the container, absolute
    destination: /mnt/backup/documents  # path on the destination, absolute
    name: documents                  # optional; defaults to the last part of source; unique
    excludes: ["*.tmp"]              # added to rsync.excludes for this folder
    extra_options: []                # added after rsync.extra_options for this folder
    allow_delete: true               # false: the --delete toggle never deletes here
    enabled: true                    # false keeps it listed but skipped

truenas:
  enabled: auto               # TRUENAS_ENABLED
  host: null                  # TRUENAS_HOST
  api_key: null               # TRUENAS_API_KEY; prefer FILE__TRUENAS_API_KEY in the environment
  verify_tls: false           # TRUENAS_VERIFY_TLS
  pool_aliases: {}            # TRUENAS_POOL_ALIASES
  cache_seconds: 30           # how long an answer from the NAS is reused

ui:
  dark_theme: true            # UI_DARK_THEME
  locale: null                # UI_LOCALE
  clock: 24h                  # UI_CLOCK

history:
  keep_days: 40               # HISTORY_KEEP_DAYS
  max_log_bytes: 52428800     # per-run log cap, 50 MiB; beyond it rsync lines are counted, not stored
```

### remote

`host` and `user` are required as soon as there is an enabled folder. With
`host_key_policy: tofu` the destination's host key is written to `/config/ssh/known_hosts`
on the first connection and any later change fails the connection with a message that says
what to do. `strict` refuses a host that is not already in that file. `insecure` accepts
anything and is for a throwaway test, not a backup. A `host_key` line pins the key from the
configuration itself and switches the policy to `strict`; the line is the public key as
`ssh-keyscan` prints it, without the host prefix.

### schedule

`cron` is a standard five-field expression evaluated in `TZ`. The scheduler re-reads it
every minute, so a change moves the next run within a minute. If the host sleeps past a due
time, the run fires once on wake instead of being lost or repeated; a run that was due while
the container was stopped is not made up, the next one on the schedule is simply the next.
A scheduled run that finds another run active is skipped with a warning, never queued.

`rsync_timeout` becomes `--timeout` on scheduled runs only: a NAS that went to sleep cannot
hold a run forever. Manual runs are left alone, since someone is watching them.

### rsync

`options` are the base flags for every rsync. `--delete`, `--dry-run` and `--timeout` are
decided per run and are refused here. `preserve_ownership: false` adds `--no-owner
--no-group`, because a destination user that is not root cannot change file ownership and
plain `-a` would end every run with exit 23. `excludes` become `--exclude=` arguments; the
defaults cover trash folders, OS metadata and Python virtualenvs.

Flags go on the command line in this order: `options`, the ownership flags, the per-run
flags, the excludes (global, then the folder's), `rsync.extra_options`, then the folder's
`extra_options`, so a folder can override a global flag.

### folders

`source` is the path inside the container, which is the right-hand side of your bind mount.
`destination` is the path on the destination host. Both must be absolute and may not contain
`..`. Both get a trailing slash on the command line, so the folder's contents land in the
destination, not the folder itself.

`name` defaults to the last part of `source` and must be unique; it is what the UI, the logs
and the API use, and what `run-once --folder` and the `folders` field of `POST /api/runs`
accept. `enabled: false` keeps the folder listed with no buttons. A pause from the UI is the
temporary version of that, forgotten at the next restart.

Useful per-folder options:

```yaml
  - source: /data/videos
    destination: /mnt/vault/backup/videos
    extra_options: ["--bwlimit=4000"]     # KiB/s; keep the uplink usable
    allow_delete: false                   # never prune the archive
  - source: /data/photos
    destination: /mnt/tank/backup/photos
    excludes: ["*.xmp", "@eaDir"]
```

### truenas

See [[Destinations]] for creating the API key. `host` defaults to `remote.host`; set it when
the web UI of the NAS is not on the SSH address, and add `:port` when it is not on 443.
`pool_aliases` renames pools in the card and in `/api/pool_status`, so a dashboard keeps a
stable key when a pool is rebuilt under another name.

### ui and history

`locale` decides the date format in the page; `null` uses the browser's. Runs older than
`keep_days` are deleted, records and logs together. A single run's log is capped at
`max_log_bytes`; past the cap rsync lines are counted but not stored, the engine's own lines
still are, and the run's detail says how many lines were dropped.
