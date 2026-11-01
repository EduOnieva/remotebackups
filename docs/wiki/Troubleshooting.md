# Troubleshooting

By message, as the UI, the log or the CLI shows it. The rsync exit codes are at the end.

## Starting

**`No SSH key at /run/secrets/ssh_key and no SSH_PASSWORD set`**
The key file is not where the container looks. Usually the bind mount's host path does not
exist, so Docker created an empty directory: `docker exec remotebackups ls -la /run/secrets`
shows a directory where a file should be. Write the host path in full (`~` is root's home
under `sudo` or Portainer) and recreate the container. `SSH_KEY` points at another path.

**`SSH key ... cannot be used non-interactively (passphrase-protected or invalid)`**
The key has a passphrase, or the file is not a private key (a public key, a certificate, an
empty file from a bad mount). Generate one without a passphrase for these backups:
`ssh-keygen -t ed25519 -N "" -f ~/.ssh/remotebackups`.

**`Configuration problems:` and the container exits with code 2**
`config.yaml` or the environment is invalid; each line names the key and the problem, for
example `folders[0].destination: must be an absolute path`. Unknown keys are refused too, so
check for typos. `remotebackups check-config` reproduces the check without starting anything.

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
*completed with errors*; everything else was copied.

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

23 and 24 mean data moved and some files did not; the run is *completed with errors* and
the log lists them.
