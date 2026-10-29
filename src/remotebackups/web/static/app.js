"use strict";

const $ = (id) => document.getElementById(id);
const STATUS_INTERVAL_MS = 5000;
const RUNS_INTERVAL_MS = 15000;
const TRUENAS_INTERVAL_MS = 60000;

let selectedRun = null;
let liveRun = null;
let stream = null;
let runs = [];
let statusTimer = null;

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (response.status === 204) return null;
  const body = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body?.detail || body || response.statusText);
  return body;
}

function toast(message, bad = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = bad ? "bad" : "";
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 4000);
}

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString() : "–");
const fmtDuration = (s) => {
  if (s == null) return "–";
  const total = Math.round(s);
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), sec = total % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
};
const fmtBytes = (n) => {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
};
const label = (outcome) => outcome.replace(/_/g, " ");

function renderStatus(s) {
  const running = s.state !== "idle";
  const current = s.current_run;
  const stateEl = $("state");
  stateEl.textContent = running ? (s.state === "cancelling" ? "Stopping…" : "Running") : (s.last_run ? label(s.last_run.outcome) : "No run yet");
  stateEl.className = `big state-${running ? s.state : (s.last_run?.outcome || "idle")}`;
  $("current-folder").textContent = current?.current_folder || "–";
  $("elapsed").textContent = current ? fmtDuration((Date.now() - new Date(current.started_at)) / 1000) : "–";
  $("last-run").textContent = s.last_run ? `${label(s.last_run.outcome)} · ${fmtTime(s.last_run.finished_at)}` : "–";
  $("next-run").textContent = s.next_run_at ? fmtTime(s.next_run_at) : (s.schedule ? "computing…" : "schedule disabled");
  $("run").disabled = running;
  $("run-delete").disabled = running;
  $("stop").hidden = !running;
  $("stop").disabled = s.state === "cancelling";

  const ok = s.remote.ok;
  $("remote-ok").textContent = ok == null ? "checking…" : ok ? "yes" : "no";
  $("remote-ok").className = ok == null ? "" : ok ? "ok" : "bad";
  $("remote-checked").textContent = fmtTime(s.remote.checked_at);
  $("remote-message").textContent = ok === false ? s.remote.message : "";
  $("truenas-card").hidden = !s.truenas_enabled;

  if (current && liveRun !== current.id) follow(current.id);
  if (!current && liveRun) { liveRun = null; loadRuns(); }
}

async function loadStatus() {
  try { renderStatus(await api("/api/status")); } catch (e) { toast(`Status unavailable: ${e.message}`, true); }
}

function renderRuns() {
  const body = $("runs").querySelector("tbody");
  body.innerHTML = "";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    if (r.id === selectedRun) tr.classList.add("selected");
    tr.innerHTML = `<td>${fmtTime(r.started_at)}</td><td>${r.trigger}${r.delete ? " + delete" : ""}</td>` +
      `<td class="state-${r.outcome}">${label(r.outcome)}</td><td>${fmtDuration(r.duration_s)}</td>` +
      `<td>${r.folders_ok}/${r.folders_total}${r.folders_failed ? ` <span class="bad">(${r.folders_failed} failed)</span>` : ""}</td>` +
      `<td>${r.files_transferred}</td><td>${fmtBytes(r.bytes_sent)}</td>`;
    tr.onclick = () => showRun(r.id);
    body.appendChild(tr);
  }
}

async function loadRuns() {
  try { runs = await api("/api/runs?limit=50"); renderRuns(); } catch (e) { toast(e.message, true); }
}

async function loadFolders() {
  try {
    const list = await api("/api/folders");
    const body = $("folders").querySelector("tbody");
    body.innerHTML = "";
    for (const f of list) {
      const tr = document.createElement("tr");
      const note = !f.enabled ? '<span class="muted">disabled</span>' : !f.source_exists ? '<span class="bad">source missing</span>' : f.allow_delete ? "" : '<span class="muted">no delete</span>';
      tr.innerHTML = `<td>${f.name}</td><td class="path">${f.source}</td><td class="path">${f.destination}</td><td>${note}</td>`;
      tr.style.cursor = "default";
      body.appendChild(tr);
    }
  } catch (e) { toast(e.message, true); }
}

async function loadTrueNAS() {
  if ($("truenas-card").hidden) return;
  try {
    const data = await api("/api/pool_status");
    const dl = $("truenas-list");
    dl.innerHTML = "";
    for (const [key, value] of Object.entries(data)) {
      if (key === "sync_errors") continue;
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = key === "alerts" ? "Alerts" : key === "error" ? "Error" : `${key} free`;
      dd.textContent = value;
      if (key === "alerts" && value > 0) dd.className = "warn-text";
      if (key === "error") dd.className = "bad";
      dl.append(dt, dd);
    }
  } catch (e) { toast(e.message, true); }
}

function appendLine(text) {
  const log = $("log");
  const span = document.createElement("span");
  span.textContent = text + "\n";
  if (text.includes("[ERROR]")) span.className = "err";
  else if (text.includes("[WARN]")) span.className = "warn";
  log.appendChild(span);
  if ($("follow").checked) log.scrollTop = log.scrollHeight;
}

function closeStream() {
  if (stream) { stream.close(); stream = null; }
}

function follow(runId) {
  closeStream();
  selectedRun = runId;
  liveRun = runId;
  $("log").textContent = "";
  $("log-title").textContent = `Log · ${runId} (live)`;
  renderRuns();
  stream = new EventSource(`/api/runs/${runId}/log/stream`);
  stream.addEventListener("line", (e) => appendLine(e.data));
  stream.addEventListener("end", () => { closeStream(); $("log-title").textContent = `Log · ${runId}`; loadRuns(); loadStatus(); });
  stream.onerror = () => { /* the browser reconnects with Last-Event-ID on its own */ };
}

async function showRun(runId) {
  closeStream();
  selectedRun = runId;
  renderRuns();
  const log = $("log");
  log.textContent = "";
  $("log-title").textContent = `Log · ${runId}`;
  try {
    let offset = 0;
    for (;;) {
      const response = await fetch(`/api/runs/${runId}/log?offset=${offset}&max_bytes=262144`);
      if (!response.ok) throw new Error(await response.text());
      const text = await response.text();
      text.split("\n").filter((l, i, a) => l || i < a.length - 1).forEach(appendLine);
      const next = Number(response.headers.get("X-Log-Offset"));
      if (response.headers.get("X-Log-Complete") === "true" || next === offset) break;
      offset = next;
    }
  } catch (e) { toast(e.message, true); }
}

async function startRun(withDelete) {
  if (withDelete && !confirm("Run with --delete? Files removed locally will be deleted from the backup.")) return;
  try {
    const result = await api("/api/runs", { method: "POST", body: JSON.stringify({ delete: withDelete }) });
    toast(`Run ${result.run_id} started`);
    await loadStatus();
  } catch (e) { toast(e.message, true); }
}

async function stopRun() {
  if (!confirm("Stop the running backup? rsync will resume from where it left off next time.")) return;
  try { await api("/api/runs/current/cancel", { method: "POST" }); toast("Stopping…"); await loadStatus(); }
  catch (e) { toast(e.message, true); }
}

async function clearHistory() {
  if (!confirm("Delete all run records and logs?")) return;
  try { await api("/api/runs", { method: "DELETE" }); selectedRun = null; $("log").textContent = ""; $("log-title").textContent = "Log"; await loadRuns(); await loadStatus(); }
  catch (e) { toast(e.message, true); }
}

$("run").onclick = () => startRun(false);
$("run-delete").onclick = () => startRun(true);
$("stop").onclick = stopRun;
$("clear").onclick = clearHistory;

loadStatus(); loadRuns(); loadFolders(); loadTrueNAS();
statusTimer = setInterval(loadStatus, STATUS_INTERVAL_MS);
setInterval(loadRuns, RUNS_INTERVAL_MS);
setInterval(loadTrueNAS, TRUENAS_INTERVAL_MS);
