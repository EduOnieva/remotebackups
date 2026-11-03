# API

Everything the page shows comes from a JSON API under `/api`, documented live at `/docs`
(Swagger UI). No authentication: see [[Networking]] for where the UI belongs.

State-changing calls (`POST`, `PUT`, `DELETE`) must carry `Content-Type: application/json`,
and from a browser must come from the page's own origin. From a script:

```bash
curl -X POST -H 'Content-Type: application/json' http://remotebackups:8080/api/runs -d '{"dry_run": true}'
```

## Endpoints

| Endpoint | What it does |
|---|---|
| `GET /healthz` | `{"status": "ok", "version", "reasons": [], "next_run_at"}`; 503 with `"degraded"` when the scheduler loop is not running |
| `GET /api/status` | State, current and last run, next scheduled run, remote reachability, UI settings |
| `GET /api/folders` | The configured folders: enabled, paused, `allow_delete`, whether the source exists |
| `PUT /api/folders/{name}` | Body `{"paused": true}` or `false`. 409 while a run is active, 400 for a folder disabled in the file |
| `PUT /api/schedule` | Body `{"paused": true}` or `false`: skip scheduled runs until the container restarts. 400 when there is no schedule |
| `POST /api/runs` | Start a run; body `{"delete": false, "dry_run": false, "folders": ["documents"]}`, all optional. 202 with the run id, 409 while a run is active, 400 for an unknown folder or an invalid configuration |
| `POST /api/runs/current/cancel` | Stop the active run. 202, or 404 when none is active |
| `POST /api/remote/check` | Probe the SSH login now; answers like the `remote` object of the status |
| `GET /api/runs?limit=20` | Run summaries, newest first, up to 500 |
| `GET /api/runs/{id}` | One run with its per-folder results and rsync statistics |
| `GET /api/runs/{id}/log` | The log as text. `offset` and `max_bytes` (up to 1 MiB) read a chunk; `tail` reads the last N bytes from a line start |
| `GET /api/runs/{id}/log/download` | The whole log file as a download |
| `GET /api/runs/{id}/log/stream` | The log as Server-Sent Events from byte `start`, live while the run is going |
| `DELETE /api/runs` | Delete all history. 409 while a run is active |
| `GET /api/config` | The effective configuration, secrets masked |
| `GET /api/truenas` | Pools (usage, health, scrub) and alerts as the TrueNAS card shows them |
| `GET /api/pool_status` | The v1 shape for dashboards, below |

Run ids look like `20261101T114449Z-d781`: the UTC start time and four hex characters.

## The status

```json
{
  "state": "idle",
  "current_run": null,
  "last_run": {
    "id": "20261101T114449Z-d781", "trigger": "manual", "delete": false, "dry_run": false,
    "outcome": "completed", "started_at": "2026-11-01T12:44:49.312000+01:00",
    "finished_at": "2026-11-01T12:45:52.104000+01:00", "duration_s": 62.79,
    "folders_total": 8, "folder_names": ["documents", "photos", "..."],
    "folders_ok": 8, "folders_failed": 0, "files_transferred": 1, "bytes_sent": 250060676,
    "reason": null, "current_folder": null
  },
  "next_run_at": "2026-11-02T02:30:00+01:00",
  "schedule": "30 2 * * *",
  "schedule_paused": false,
  "remote": {
    "host": "nas.lan", "user": "backup", "port": 22, "auth": "key", "host_key_policy": "tofu",
    "ok": true, "message": "ok", "checked_at": "2026-11-01T12:44:30+01:00"
  },
  "truenas_enabled": true,
  "dark_theme": true, "locale": "en-GB", "clock": "24h",
  "version": "2.3", "started_at": "2026-11-01T12:38:02+01:00"
}
```

`state` is `idle`, `running` or `cancelling`. `current_run` has the same shape as
`last_run` while a run is active, with `current_folder` set. `outcome` is one of
`running`, `completed`, `completed_with_errors`, `cancelled`, `failed`, `aborted`.

## A run's detail

`GET /api/runs/{id}` adds `folders` and `log_lines_dropped` to the summary. Each folder:

```json
{
  "name": "videos", "source": "/data/videos", "destination": "/mnt/vault/backup/videos",
  "status": "ok", "exit_code": 0, "message": null,
  "started_at": "...", "finished_at": "...", "duration_s": 61.6,
  "stats": {
    "files_total": 7, "files_transferred": 1, "files_created": 1, "files_deleted": 0,
    "total_size": 795000000, "bytes_sent": 250060676, "bytes_received": 39, "speedup": 3.18
  }
}
```

`status` is `pending`, `running`, `ok`, `failed`, `cancelled` or `skipped`; `message`
explains a failure or a skip in words, including the rsync exit code's meaning. `stats` is
null when rsync printed no `--stats` block (it did not start, or was stopped early).

## The log

`GET /api/runs/{id}/log?tail=65536` returns text with three headers: `X-Log-Start` and
`X-Log-Offset` are the byte range returned, `X-Log-Complete` says whether the run is over
and the range reaches the end. To follow a running log, call again with `offset` set to the
last `X-Log-Offset`.

`GET /api/runs/{id}/log/stream?start=0` is an SSE stream from byte `start`: `line` events
carry one log line each, with the byte offset after it as the event id, so a client
reconnecting with `Last-Event-ID` continues where it stopped (the header beats `start`); a `state` event carries the outcome when it changes; `end`
closes the stream when the run is over and everything was sent. A comment line keeps the
connection alive every 15 seconds of silence.

## pool_status, the v1 shape

```json
{"sync_errors": 0, "alerts": 1, "disk-0": "6.08 TB", "disk-1": "3.21 TB"}
```

`sync_errors` is the number of folders that failed in the last finished run. `alerts` and
one key per pool come from TrueNAS. A pool's value is the space left to write, its root
dataset's `available` in decimal TB (`zpool list` shows `FREE` in TiB, before ZFS's slop
space), or `"unknown"` when the root dataset is missing; the pool keys follow
`TRUENAS_POOL_ALIASES`. Without TrueNAS the answer is `{"sync_errors": 0, "truenas": "disabled"}`,
and when the NAS cannot be reached `{"sync_errors": 0, "error": "Failed to connect to TrueNAS: ..."}`.

## Dashboards

**Homepage** (gethomepage.dev), a `customapi` widget on the status:

```yaml
- Backups:
    icon: mdi-backup-restore
    href: http://remotebackups:8080
    widget:
      type: customapi
      url: http://remotebackups:8080/api/status
      mappings:
        - field: state
          label: State
        - field:
            last_run: outcome
          label: Last run
        - field:
            last_run: finished_at
          label: Finished
          format: relativeDate
        - field: next_run_at
          label: Next
          format: relativeDate
```

**Home Assistant**, a REST sensor pair:

```yaml
rest:
  - resource: http://remotebackups:8080/api/status
    scan_interval: 300
    sensor:
      - name: Backup state
        value_template: "{{ value_json.state }}"
      - name: Backup last outcome
        value_template: "{{ value_json.last_run.outcome if value_json.last_run else 'none' }}"
      - name: Backup next run
        value_template: "{{ value_json.next_run_at }}"
        device_class: timestamp
```

**Uptime Kuma** or any HTTP monitor: watch `/healthz` for a 200, and a keyword monitor on
`/api/status` for `"outcome":"completed"` if you want to be told about a bad night.

**A script that starts a run and waits for it**:

```bash
id=$(curl -s -X POST -H 'Content-Type: application/json' http://remotebackups:8080/api/runs | jq -r .run_id)
until [ "$(curl -s http://remotebackups:8080/api/status | jq -r .state)" = idle ]; do sleep 10; done
curl -s http://remotebackups:8080/api/runs/$id | jq '{outcome, folders_ok, folders_failed, bytes_sent}'
```
