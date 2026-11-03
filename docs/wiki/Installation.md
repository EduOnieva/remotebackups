# Installation

You need Docker with Compose, a destination you can SSH into (see [[Destinations]]) and an
SSH key without a passphrase whose public half is in the destination user's
`authorized_keys`.

## The key

Generate one for these backups alone, so it can be revoked without touching anything else:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/remotebackups
```

The container looks for the private key at `/run/secrets/ssh_key`. Mount the file there
read-only, or declare it under Compose's `secrets:`, which lands in the same place:

```yaml
services:
  remotebackups:
    secrets: [ssh_key]
secrets:
  ssh_key:
    file: /home/you/.ssh/remotebackups
```

Write host paths in full. Compose expands `~` to the home of whoever runs the deploy, which
is root under `sudo` or Portainer, and Docker turns a bind mount whose source does not exist
into an empty directory. The symptom is `No SSH key at /run/secrets/ssh_key` in the UI.

The key is copied before every run and every SSH check into the container's runtime
directory with mode 0600, so
it does not matter how the file is owned or protected on the host, and nothing in `/config`
ever holds it.

## Direct destination

A destination on your LAN, or a host your server already reaches through a VPN. This is the
tracked [`docker-compose.yaml`](https://github.com/EduOnieva/remotebackups/blob/main/docker-compose.yaml):

```yaml
# remotebackups: back up folders of this host to another host over SSH.
# This file is for a destination reachable directly (LAN, a host already on your VPN).
# For a destination reached through Tailscale, use docker-compose.tailscale.yaml instead.
#
# 1. Point the key volume below at your SSH private key.
# 2. Mount each folder to back up read-only under /data and set REMOTE_HOST / REMOTE_USER.
# 3. docker compose up -d, then open http://<this-host>:8080 and edit ./config/config.yaml
#    (a commented example is written on first start; folders are re-read before every run).
services:
  remotebackups:
    image: eduonieva/remotebackups:latest
    container_name: remotebackups
    restart: unless-stopped
    stop_grace_period: 30s # a running rsync gets SIGTERM and ten seconds before SIGKILL
    security_opt:
      - no-new-privileges:true # nothing in the container needs to gain privileges
    # Root inside, with only what root needs here: reading your folders whatever their owner
    # and writing /config. The rest is dropped and the image itself is mounted read-only
    cap_drop: [ALL]
    cap_add: [DAC_READ_SEARCH, DAC_OVERRIDE]
    read_only: true
    tmpfs:
      - /run/remotebackups # the ssh config and key copy; forgotten at every start
    ports:
      - "8080:8080"
    volumes:
      - ./config:/config
      # Absolute path: `~` would be root's home under sudo or Portainer, and a missing source
      # silently becomes an empty directory in the container
      - /home/you/.ssh/id_ed25519:/run/secrets/ssh_key:ro
      - /path/to/documents:/data/documents:ro
      - /path/to/photos:/data/photos:ro
    environment:
      - REMOTE_HOST=192.168.1.20
      - REMOTE_USER=backup
      - TZ=Etc/UTC
      # - CRON_SCHEDULE=30 2 * * 1        # default: weekly, Monday 02:30
      # - SCHEDULED_RSYNC_TIMEOUT=3600    # rsync inactivity timeout for scheduled runs only
      # - TRUENAS_API_KEY=                # enables the TrueNAS card when the destination is a TrueNAS
      # - TRUENAS_POOL_ALIASES=tank:disk-0
      # - SSH_PASSWORD=                   # password auth instead of a key (not recommended)
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

Mount every folder to back up read-only under `/data`; the container never needs to write
to them. `stop_grace_period` gives a running rsync time to stop cleanly: it gets SIGTERM and
ten seconds before SIGKILL, and `--partial` keeps what was transferred.

## Through Tailscale

When the destination is on your tailnet and the server is not, the tracked
[`docker-compose.tailscale.yaml`](https://github.com/EduOnieva/remotebackups/blob/main/docker-compose.tailscale.yaml)
runs the official `tailscale/tailscale` sidecar and puts remotebackups in its network
namespace. The UI is then reachable only from the tailnet. See [[Networking]] for the
details and the trade-offs.

## Portainer

Paste the compose file as a stack. Two things bite there:

- Portainer runs the deploy as root, so `~` in a path is `/root`. Write absolute paths.
- A relative `./config` is created under Portainer's own data directory. Prefer an absolute
  host path such as `/srv/remotebackups/config` so you know where the state is.

## Secrets

A value in `environment:` is readable by anyone who can run `docker inspect`. For the
TrueNAS API key and an SSH password, point a `FILE__` variable at a file instead:

```yaml
    environment:
      - FILE__TRUENAS_API_KEY=/run/secrets/truenas_key
    secrets: [ssh_key, truenas_key]
secrets:
  ssh_key:
    file: /home/you/.ssh/remotebackups
  truenas_key:
    file: /home/you/.config/remotebackups/truenas_key
```

`FILE__<VAR>` works for every variable and wins over a plain `<VAR>`. The file's trailing
newline is stripped. Two exceptions: the healthcheck reads `WEB_PORT` from the environment
itself, and the timestamps in the logs follow the process's own `TZ`, so set those two plainly.

## The container hardening

The compose files run the container read-only, with every Linux capability dropped except
two, and the runtime directory on a tmpfs:

| Setting | Why |
|---|---|
| `cap_drop: [ALL]` | Nothing in the container needs to change ownership, bind low ports, load modules or trace processes |
| `cap_add: [DAC_READ_SEARCH]` | Root inside the container reads your folders whatever their owner and mode |
| `cap_add: [DAC_OVERRIDE]` | Root writes `/config` even when the host directory belongs to your user |
| `read_only: true` | The image is immutable at runtime; the only writable places are `/config` and the tmpfs |
| `tmpfs: /run/remotebackups` | The generated ssh config, the key copy and the pause file; gone at every stop |
| `no-new-privileges` | No setuid escalation, which nothing here uses anyway |

The process runs as root inside the container because the folders you back up belong to
whoever owns them on the host, and reading them all is the job. Running as a fixed user
would require that user to be able to read every mounted folder, which on a typical server
is not the case. The two capabilities are what root keeps.

## Image tags

| Tag | Follows |
|---|---|
| `latest` | the `main` branch, which moves at releases |
| `2.6` | that release; `2` is the newest `2.x` |
| `develop` | the `develop` branch, where changes land first |

Published on Docker Hub (`eduonieva/remotebackups`) and GHCR (`ghcr.io/eduonieva/remotebackups`)
for amd64 and arm64. The release tags are rebuilt every Monday from the same code, so fixes
in the Python base image and Alpine packages arrive with a `docker compose pull`. The runtime
image carries no pip; the only Python packages in it are the application's own.

Updating is `docker compose pull && docker compose up -d`. Nothing in `/config` is rewritten
on upgrade: `remotebackups print-example-config` prints the current template so you can see
new keys, and every new key has a default.

## First start

The first start writes a commented `config/config.yaml` and keeps running, with the UI up and
no folders. Add `folders:` to the file, and they are picked up before the next run, no
restart needed. `docker exec remotebackups remotebackups check-config` validates the file and
summarises it.
