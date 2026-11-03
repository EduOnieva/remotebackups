# Troubleshooting

By message, as the UI, the log or the CLI shows it. The rsync exit codes are at the end.

## Starting

**`No SSH key at /run/secrets/ssh_key and no SSH_PASSWORD set`**
The key file is not where the container looks. Usually the bind mount's host path does not
exist, so Docker created an empty directory: `docker exec remotebackups ls -la /run/secrets`
shows a directory where a file should be. Write the host path in full (`~` is root's home
under `sudo` or Portainer) and recreate the container. `SSH_KEY` points at another path.

**`SSH key not found: ...`**
`SSH_KEY` (or `remote.ssh_key`) names a file that does not exist; a relative path is looked
up under `/run/secrets`. Fix the path or the mount, or unset `SSH_KEY` to use
`/run/secrets/ssh_key`.

**`SSH key ... cannot be used non-interactively (passphrase-protected or invalid)`**
The key has a passphrase, or the file is not a private key (a public key, a certificate, an
empty file from a bad mount). Generate one without a passphrase for these backups:
`ssh-keygen -t ed25519 -N "" -f ~/.ssh/remotebackups`.

**`Configuration problems:` and the container exits with code 2**
`config.yaml` or the environment is invalid; each line names the key and the problem, for
example `folders[0].destination: destination must be an absolute path`. Unknown keys are refused too, so
check for typos. `remotebackups check-config` reproduces the check without starting anything.

**`config: remote.host and remote.user are required to back up folders`**
There is an enabled folder but no destination. Set `REMOTE_HOST` and `REMOTE_USER` (or
`remote.host` and `remote.user`); when only one is missing, the line names only that one.

**`truenas: truenas.enabled is true but no API key is configured`**
`TRUENAS_ENABLED=true` forces the card on, and it needs a key. Pass `FILE__TRUENAS_API_KEY`,
or use `auto`, which turns TrueNAS on only when a key is set.

**`rsync.options: '--delete' is decided per run and cannot be set here`**
`--delete`, `--dry-run` and `--timeout` (and any flag starting with them) are chosen per run,
so `rsync.options`, `rsync.extra_options`, `RSYNC_EXTRA_OPTIONS` and a folder's
`extra_options` refuse them. Remove the flag; use the toggles, `run-once --delete` or
`schedule.rsync_timeout` instead.

**`Cannot start: the web UI cannot listen on port 8080`** (exit 3)
Something else in the container's network namespace already uses the port, typically with
the Tailscale sidecar. `WEB_PORT` selects another one; the healthcheck follows it.

**`/config is not a mounted volume; configuration and history will be lost with the container`**
A warning at startup: `/config` is in the container's own filesystem. Add the volume.

**`No config.yaml found; wrote a commented example`**
Normal on the first start. Add `folders:` to the file; no restart needed.

## The Remote card says no

**`the remote host key changed or is unknown under the current policy`**
With `tofu`, the destination's host key differs from the one pinned in
`/config/ssh/known_hosts`; with `strict`, the host is not in that file yet. If the
destination was reinstalled, delete its line from the file. If it was not, find out why
before trusting the new key.

**`remote.host is required to pin a host key`**
`remote.host_key` is set but the host is not. Set `REMOTE_HOST`, or drop `host_key`.

**`ssh: connect to host ... port 22: Connection refused`** or **`No route to host`**
The address or port is wrong, SSH is not enabled on the destination, or a firewall is in
the way. With Tailscale, check that both nodes are online in the admin console and that an
ACL allows port 22.

**`no answer from ... within 20s`**
The host does not answer at all: asleep, off, or the name does not resolve from inside the
container. A sleeping NAS is normal; the card comes back when it does.

**`Permission denied (publickey)`**
The destination refused the key. Check that the public half is in the right user's
`authorized_keys`, that `REMOTE_USER` is that user, and that the user may log in over SSH at
all (Synology allows only administrators by default; TrueNAS needs *SSH* allowed for the
user). The exact login can be tried by hand with the generated configuration:
`docker exec -it remotebackups ssh -F /run/remotebackups/ssh/config user@host 'echo ok'`.

## Runs

**`rsync exit 23: partial transfer due to error`**
Some files were not transferred; the lines above it in the log say which and why. The two
usual causes: `preserve_ownership: true` with a destination user that is not root (every
file "fails" at the ownership step), and files the container cannot read. The run is marked
*completed with errors*, or *failed* when every folder failed; everything else was copied.

**`rsync exit 255: ssh could not connect or authenticate`**
The same causes as a red Remote card; the ssh message is in the log just above. The Remote
card and **Check now** show it in one line.

**`rsync exit 11: error in file I/O`**
Usually the destination directory cannot be created or written: the parent does not exist,
or the backup user has no write permission there. Check the path and the dataset or folder
permissions on the destination.

**`rsync exit 30: timeout in data send/receive`**
A scheduled run's `--timeout` (`schedule.rsync_timeout`) fired: no data moved for that long.
The destination went to sleep mid-run or the link dropped. The next run resumes.

**`rsync exit 12: error in rsync protocol data stream`**
Often the destination ran out of space, or the remote rsync died. The lines above say which.

**`source directory not found inside the container`**
The folder's `source:` does not exist in the container: the bind mount is missing or its
host path is wrong. The Folders card shows a *source missing* chip for it.

**`run ... is already active`** (409) and **`Scheduled run skipped: run ... is still active`**
One run at a time. Wait for it or press Stop.

**`another process holds the run lock; is run-once active?`**
A `run-once` from the CLI is in progress, or an earlier one was killed and its lock not
released; the lock is a file lock, so a finished process always releases it.

**`Scheduler cannot read the configuration, retrying every minute: ...`** and **`Scheduled run skipped, the configuration is invalid: ...`**
`config.yaml` was edited into an invalid state while the container runs. The rest of the line
names the problem, as in `Configuration problems:` above; fix the file and the log says
`Scheduler can read the configuration again`. A manual run meanwhile answers 400 with
`configuration is invalid: ...`.

**`Scheduled run skipped: the schedule is paused from the UI`**
Expected while the schedule is paused. **Resume schedule**, or restart the container, which
forgets every pause.

**`unknown or disabled folder(s)`** (400)
A `folders` name in `POST /api/runs` or `run-once --folder` does not match an enabled,
unpaused folder. Names are the `name:` in `config.yaml`, which defaults to the last part of
`source`.

**The run says *aborted*, `Run aborted: the application stopped while this run was in progress`**
The container was killed without the chance to cancel the run (SIGKILL, a host crash). The
record is closed at the next start. `--partial` kept what was transferred; the next run
resumes.

**`Log cap reached; further rsync output is counted but not stored`**
A single run produced more than `history.max_log_bytes` of rsync output, typically `-v`
listing millions of files. Raise the cap, or drop `-v` from `rsync.options` and read the
`--stats` summary instead.

## The UI

**`cross-site request refused`** or **`state changes need Content-Type: application/json`**
The page's own buttons never hit this. Behind a reverse proxy that rewrites `Host`, the
proxy must add `X-Forwarded-Host`; see [[Networking]]. From a script, send
`Content-Type: application/json`.

**`cannot pause or resume folders while a run is active`** (409)
Pauses apply to the next run only, so the UI refuses them during one.

**`folder ... is disabled in config.yaml; edit the file`** (400)
A folder with `enabled: false` has no buttons; the file is the switch.

**`there is no schedule to pause; schedule.cron is not set`** (400)
`PUT /api/schedule` with scheduled runs disabled. Set `CRON_SCHEDULE` or `schedule.cron`; the
UI hides **Pause schedule** while there is no schedule.

**`cannot clear history while a run is active`** (409)
**Clear history** waits for the run to end. Stop it first or try again afterwards.

**`no run is active`** (404)
`POST /api/runs/current/cancel` when nothing is running, usually a Stop that arrived just after
the run finished. Nothing to do.

**The log shows nothing until the run ends**
A reverse proxy is buffering the event stream. The application asks it not to
(`X-Accel-Buffering: no`); if the proxy ignores that, turn buffering off for the location.

**Dates look wrong**
The page follows `ui.locale` and `ui.clock`; timestamps inside the log follow the
container's `TZ`.

## The TrueNAS card

**`Failed to connect to TrueNAS: ...`**
The NAS is off, asleep, or `TRUENAS_HOST` is not where its web UI answers. The card comes
back on its own; a certificate error means `TRUENAS_VERIFY_TLS=true` with a self-signed
certificate.

**`API key rejected`**
The key is wrong or was revoked. If it came from `FILE__TRUENAS_API_KEY`, check the file
has only the key.

**`DDP handshake rejected: ...`**
Something answered at `wss://<TRUENAS_HOST>/websocket` but not as the TrueNAS API. Point
`TRUENAS_HOST` at the NAS's web UI address, with `:port` when it is not on 443.

**`DDP method ... failed: ...`**
The NAS refused one of the calls the card makes (`alert.list`, `pool.query`,
`pool.dataset.query`); the rest of the line is TrueNAS's own error. Check the API key's user
may read pools, datasets and alerts.

## The Tailscale sidecar

**remotebackups never starts, the sidecar is unhealthy**
The sidecar did not join the tailnet: an expired or single-use `TS_AUTHKEY` on a fresh state
volume, or no `/dev/net/tun`. `docker logs remotebackups-tailscale` says which. Once the
state volume holds an identity the key is no longer needed.

## rsync exit codes

As the run's folder table explains them. Negative codes mean rsync was killed by that
signal; `-15` is SIGTERM from Stop.

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | syntax or usage error |
| 2 | protocol incompatibility |
| 3 | errors selecting input/output files or dirs |
| 4 | requested action not supported |
| 5 | error starting client-server protocol |
| 6 | daemon unable to append to log-file |
| 10 | error in socket I/O |
| 11 | error in file I/O |
| 12 | error in rsync protocol data stream |
| 13 | errors with program diagnostics |
| 14 | error in IPC code |
| 20 | received SIGUSR1 or SIGINT |
| 21 | some error returned by waitpid() |
| 22 | error allocating core memory buffers |
| 23 | partial transfer due to error |
| 24 | partial transfer due to vanished source files |
| 25 | the --max-delete limit stopped deletions |
| 30 | timeout in data send/receive |
| 35 | timeout waiting for daemon connection |
| 255 | ssh could not connect or authenticate |

23 and 24 mean data moved and some files did not; the run is *completed with errors* (or
*failed* when every folder failed) and the log lists them.
