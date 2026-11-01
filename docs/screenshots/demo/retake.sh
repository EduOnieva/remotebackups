#!/usr/bin/env bash
# Retake the three README screenshots from the demo stack. Run from this directory after
# `make build` at the repository root. Needs docker compose, uv, node 22+ and google-chrome.
set -euo pipefail
cd "$(dirname "$0")"
URL=http://localhost:8098
status() { curl -s $URL/api/status | python3 -I -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
post() { curl -s -X "$1" -H 'Content-Type: application/json' "$URL$2" -d "${3:-{\}}"; }
wait_idle() { for _ in $(seq 1 600); do [ "$(status 'd["state"]')" = idle ] && return; sleep 2; done; echo "run never finished" >&2; exit 1; }

# The container writes config/ssh and config/runs as root; a container removes them again
clean_config() { [ -d config ] && docker run --rm -v "$PWD/config:/c" alpine:3.24.3 rm -rf /c/ssh /c/runs; rm -rf data config tls truenas.log; }
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
clean_config; mkdir -p data config tls
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -keyout tls/key.pem -out tls/cert.pem -days 30 -subj "/CN=truenas" 2>/dev/null
uv run --with websockets python fake_truenas.py tls/cert.pem tls/key.pem > truenas.log 2>&1 &
TRUENAS_PID=$!
trap 'kill $TRUENAS_PID 2>/dev/null; docker compose down -v --remove-orphans >/dev/null 2>&1; clean_config' EXIT
uv run python seed.py .
# Big files must be incompressible, or rsync's zstd makes the videos transfer instant
rm -f data/videos/Family/*
head -c 120000000 /dev/urandom > data/videos/Family/2026-07-holiday-day1.mp4
head -c 95000000 /dev/urandom > data/videos/Family/2026-07-holiday-day2.mp4
head -c 80000000 /dev/urandom > data/videos/Family/2026-03-birthday.mp4

docker compose up -d --build >/dev/null 2>&1
for _ in $(seq 1 60); do curl -sf $URL/healthz >/dev/null && break; sleep 1; done
docker exec demo-sshd-1 sh -c 'mkdir -p /mnt/tank/backup /mnt/vault/backup && chown -R backup:backup /mnt/tank /mnt/vault'
post POST /api/remote/check >/dev/null
echo "warm-up run"
warm=$(post POST /api/runs | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
wait_idle
docker exec demo-app-1 sh -c "rm -f /config/runs/$warm.*"

OPEN='document.querySelectorAll("details.card").forEach(d => d.open = true); ""'
PHONE="$OPEN; document.getElementById(\"log\").style.maxHeight = \"480px\""
head -c 250000000 /dev/urandom > data/videos/Family/2026-10-weekend-hike.mp4
post PUT /api/schedule '{"paused": true}' >/dev/null
post POST /api/runs >/dev/null
for _ in $(seq 1 60); do [ "$(status '(d["current_run"] or {}).get("current_folder")')" = videos ] && break; sleep 1; done
sleep 14
node shot.mjs $URL/ 1920 1000 2 0 ../running.png "$OPEN"
wait_idle
post PUT /api/schedule '{"paused": false}' >/dev/null
sleep 7
node shot.mjs $URL/ 1920 1000 2 0 ../overview.png "$OPEN"
node shot.mjs $URL/ 400 800 2 1 ../phone.png "$PHONE"
echo "done: $(docker run --rm remotebackups:dev --version)"
