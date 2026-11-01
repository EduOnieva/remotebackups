# The screenshot stack

The README screenshots come from this stack, not from a real deployment, so they show no
real host, user or folder names. The dev image, a real sshd reachable as `nas.lan`, a fake
TrueNAS endpoint on the host, eight folders with generated files and a week of generated
run history in the exact shape the application writes.

`make build` at the repository root, then `docs/screenshots/demo/retake.sh` does all of the
below and replaces the three files in `docs/screenshots/`. Step by step:

```bash
make build                                                   # remotebackups:dev
cd docs/screenshots/demo
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout tls/key.pem -out tls/cert.pem -days 30 -subj "/CN=truenas"      # once; tls/ is gitignored
uv run --with websockets python fake_truenas.py tls/cert.pem tls/key.pem &  # wss on 8443
uv run python seed.py .                                      # data/, config/config.yaml, config/runs/
docker compose up -d --build                                 # UI on http://localhost:8098
docker exec demo-sshd-1 sh -c 'mkdir -p /mnt/tank/backup /mnt/vault/backup && chown -R backup:backup /mnt/tank /mnt/vault'
```

Then one warm-up run from the UI so the destination holds everything, and delete its
record from `config/runs/`. For the *running* screenshot, drop a new large file into
`data/videos/Family/` (the folder has `--bwlimit=4000`, so 250 MB takes about a minute),
pause the schedule, press Run, and capture while the videos folder is in progress. For the
*overview*, capture after it completes with the schedule resumed.

The captures are headless Chrome through its debugging protocol, full page, with the Runs
and Folders cards opened first:

```bash
OPEN='document.querySelectorAll("details.card").forEach(d => d.open = true); ""'
node shot.mjs http://localhost:8098/ 1920 1000 2 0 ../running.png "$OPEN"
node shot.mjs http://localhost:8098/ 1920 1000 2 0 ../overview.png "$OPEN"
node shot.mjs http://localhost:8098/ 400 800 2 1 ../phone.png "$OPEN"'; document.getElementById("log").style.maxHeight = "480px"'
```

The phone capture pins the log height because its `60vh` would otherwise follow the
full-page viewport. `docker compose down -v` and `rm -rf data config tls` clean up.
