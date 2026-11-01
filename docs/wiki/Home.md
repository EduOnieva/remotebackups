# remotebackups

Scheduled rsync backups of your home-server folders to another host, with a web UI. One
container, an optional Tailscale sidecar, TrueNAS aware.

The [README](https://github.com/EduOnieva/remotebackups#readme) is the front page: what it
does, the quick start, the configuration tables. This wiki holds the longer guides.

## Setting up

- [[Installation]]: the compose shapes (direct, Tailscale sidecar, Portainer), secrets, the
  container hardening and why it runs as root, image tags and updating.
- [[Configuration]]: every environment variable and every `config.yaml` key, with examples.
- [[Destinations]]: TrueNAS SCALE step by step, Synology, a plain Linux host.
- [[Networking]]: Tailscale, LAN, reverse proxies (Caddy, Traefik, nginx), HTTPS.

## Running it

- [[Operations]]: what a run does, pauses and stop, logs and history, the CLI, backing up
  `/config`, and restoring files from the backup.
- [[Troubleshooting]]: message by message, plus the rsync exit codes.
- [[API]]: every endpoint with examples, and widgets for Homepage and Home Assistant.
- [[Customising the UI]]: `custom.css` and `custom.js`.

## The project

- [[Development]]: code layout, tests, CI, the release flow.
- [[Migrating from v1]]: from rsync-tailscale-docker.
- [Changelog](https://github.com/EduOnieva/remotebackups/blob/main/CHANGELOG.md) and
  [security policy](https://github.com/EduOnieva/remotebackups/blob/main/SECURITY.md).
