#!/usr/bin/env bash
# `docker stop` while a run is in progress must end within the grace period, exit 0 and leave
# the run recorded as cancelled. Runs from the repo root after the pytest stage of the stack.
set -euo pipefail

compose=(docker compose -f compose/docker-compose.test.yml)
app_url() { "${compose[@]}" port app 8080 2>/dev/null || true; }

"${compose[@]}" up -d app
"${compose[@]}" exec -T app sh -c 'until wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1; do sleep 0.5; done'

# A slow copy into a destination nobody has filled yet, so the run is still going when we stop
dest="/mnt/backup/key/big-stop-$(date +%s)"
"${compose[@]}" exec -T app sh -c "cat > /config/config.yaml <<EOF
folders:
  - name: big
    source: /data/big
    destination: ${dest}
    extra_options: [\"--bwlimit=500\"]
EOF"
run_id=$("${compose[@]}" exec -T app python -c '
import json, urllib.request
req = urllib.request.Request("http://127.0.0.1:8080/api/runs", data=b"{}", headers={"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req))["run_id"])')
sleep 3
state=$("${compose[@]}" exec -T app python -c '
import json, urllib.request
print(json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/status"))["state"])')
[[ "$state" == "running" ]] || { echo "expected a run in progress before stopping, got '$state'"; exit 1; }

start=$(date +%s)
"${compose[@]}" stop -t 30 app
elapsed=$(( $(date +%s) - start ))
exit_code=$(docker inspect -f '{{.State.ExitCode}}' "$("${compose[@]}" ps -aq app)")

outcome=$("${compose[@]}" run --rm -T --no-deps --entrypoint python app -c "
import json; print(json.load(open('/config/runs/${run_id}.json'))['outcome'])")

echo "docker stop took ${elapsed}s, container exit code ${exit_code}, run outcome ${outcome}"
[[ "$exit_code" == "0" ]] || { echo "expected exit code 0"; exit 1; }
[[ "$outcome" == "cancelled" ]] || { echo "expected the run to be cancelled"; exit 1; }
[[ "$elapsed" -lt 25 ]] || { echo "shutdown took too long"; exit 1; }
