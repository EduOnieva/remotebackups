# Operations

## What a run does

For each enabled folder, in order, one `rsync` is started over SSH:

```
rsync -a -v -z --partial --stats --no-owner --no-group [--delete] [--dry-run] [--timeout=N] --exclude=... \
  -e "ssh -F /run/remotebackups/ssh/config" /data/documents/ backup@host:/mnt/backup/documents/
```

Before the first folder the run takes a lock in `/config/runs`, copies the key into the
runtime directory and writes the ssh config. Each folder's rsync output goes to the run's
log as it arrives, and its `--stats` block is parsed into the files and bytes the UI shows.
A folder whose source directory is missing inside the container fails without starting
rsync, so a bind mount that silently became an empty directory is caught.

Only one run at a time. Starting a second one answers 409, and a scheduled run that finds
another run active is skipped with a warning, never queued.

Outcomes: **completed**, **completed with errors** (some folders failed, the rest ran),
**failed** (every folder failed, or the run could not start: no key, lock held),
**cancelled** (Stop, or the container was stopped) and **aborted** (the application was
killed mid-run; marked at the next start).

## Scheduled and manual

| | Scheduled | Manual |
|---|---|---|
| Started by | `schedule.cron` | the Run buttons, `POST /api/runs`, `run-once` |
| `--delete` | never | with the `--delete` toggle, for folders with `allow_delete: true` |
| `--dry-run` | never | with the `--dry-run` toggle |
| `--timeout` | `schedule.rsync_timeout` | never |
| Folders | every enabled, unpaused folder | the same, or the one named |

The toggles beside Run apply to the next run and are consumed by it: while the run is active
they show its flags and are locked, afterwards they are off. `--delete` with `--dry-run`
previews a deletion: rsync lists what it would delete and touches nothing.

## Pausing

**Pause schedule** in the Status card makes the scheduler skip due runs; they are never
deferred, so resuming starts nothing by itself. **Pause** on a folder row makes runs skip
that folder; the button then reads **Resume**. Both last until the container restarts, which
forgets every pause; the permanent switches are `schedule.cron` and the folder's
`enabled: false` in `config.yaml`. A folder cannot be paused or resumed while a run is
active, since the toggle could only apply to the next one.

## Stopping

**Stop** sends SIGTERM to the running rsync's process group, which includes its ssh, and
skips the remaining folders. After ten seconds without exit it sends SIGKILL. `--partial`
keeps the partially transferred file on the destination, so the next run resumes it.

`docker stop` does the same: the scheduler is stopped first so nothing new starts, then the
active run is cancelled, then the web server closes. The compose files give it 30 seconds.

## Logs and history

The application itself logs to stderr: `docker logs remotebackups`. The compose files cap
that at three files of 10 MB through Docker. The application's own messages are at `INFO`
and above; `LOG_LEVEL=DEBUG` only adds the debug output of the libraries it uses.

Each run has a record and a log under `/config/runs/`, `<id>.json` and `<id>.log`. The log
holds the engine's own lines, timestamped and tagged `[INFO]`, `[WARN]`, `[ERROR]`, and the
raw rsync output between them. The UI loads the last 256 KiB of a big log and keeps 2,000
lines on screen; the file name in the log title downloads the whole file. A single log is
capped at `history.max_log_bytes` (50 MiB); past it rsync lines are counted, not stored.

Records older than `history.keep_days` (40) are deleted at startup and after every run.
**Clear history** deletes everything at once, when no run is active.

## The CLI

Every command runs inside the container with the same configuration as the service:

```bash
docker exec remotebackups remotebackups check-config            # validate; "OK: 8 enabled folder(s) of 8, schedule 30 2 * * *, TrueNAS on"
docker exec remotebackups remotebackups print-config --redact   # the effective configuration as YAML, secrets masked
docker exec remotebackups remotebackups print-example-config    # the commented template of this version
docker exec remotebackups remotebackups run-once                # one run in the foreground, exit 0 when clean
docker exec remotebackups remotebackups run-once --dry-run --folder documents
docker exec remotebackups remotebackups run-once --delete
docker exec remotebackups remotebackups healthcheck             # what Docker's HEALTHCHECK runs; exit 0 when /healthz answers 200
docker exec remotebackups remotebackups --version
```

`print-config` without `--redact` prints the SSH password and the TrueNAS API key in clear;
keep `--redact` unless you need to see them. `--folder` can be given more than once.

`run-once` takes the same lock as the service, so it refuses to overlap a run started from
the UI, and the service's scheduler refuses to overlap it. Its run is recorded and shows in
the UI like any other. Exit codes: 1 when the run did not complete cleanly, 2 for a
configuration problem, 3 when the service cannot start (the port is taken).

## Backing up the backup tool

Everything the container owns is under `/config`: `config.yaml`, `ssh/known_hosts` with the
pinned host key, `runs/` with the history, and the optional `custom.css` and `custom.js`.
Copy that directory and the container is disposable. The private key is not in it; it lives
wherever you mounted it from.

Moving to another server: copy `/config`, mount the same key, start the same compose file.
The pinned host key is still valid because it is the destination's, not the server's.

## Restoring

The backup is a plain directory tree on the destination, owned by the backup user, with the
original permissions and timestamps. Nothing proprietary: any rsync, scp or a file manager
on the NAS can read it. To bring a folder back, run rsync the other way from the server,
with the same key:

```bash
# Preview first: what would come back, and what would be overwritten
rsync -av --dry-run -e 'ssh -i ~/.ssh/remotebackups' backup@192.168.1.20:/mnt/tank/backup/documents/ /srv/documents/

# Then for real; drop --dry-run. Without --delete nothing on the server is removed
rsync -av -e 'ssh -i ~/.ssh/remotebackups' backup@192.168.1.20:/mnt/tank/backup/documents/ /srv/documents/
```

A single file: `rsync -av -e '...' backup@host:/mnt/tank/backup/documents/taxes/2025.pdf /srv/documents/taxes/`.
Files come back owned by whoever runs the command, since ownership was not kept on the copy.

Restoring into a different place and comparing before overwriting is the safe order; the
backup user has no way to write to your server, so a restore is always something you do
deliberately from the server's side.

## A changed destination host key

With the default `tofu` policy the first connection pins the destination's host key in
`/config/ssh/known_hosts`, and a different key afterwards fails every connection: the Remote
card says "the remote host key changed or is unknown under the current policy". If the
destination was reinstalled or its keys regenerated, delete its line from that file and the
next connection pins the new key. If it was not, find out why the key changed before
trusting it.

## Health

`GET /healthz` answers 200 while the scheduler loop is alive and 503 otherwise; Docker's
`HEALTHCHECK` calls it every 30 seconds. An unreachable destination is not a health failure:
the Remote card shows it, with the ssh message, and the probe every five minutes notices
when it is back. **Check now** runs the probe immediately.
