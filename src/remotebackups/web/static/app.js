"use strict";

const $ = (id) => document.getElementById(id);
const STATUS_INTERVAL_MS = 5000;
const RUNS_INTERVAL_MS = 15000;
const TRUENAS_INTERVAL_MS = 60000;
// The log panel keeps this many lines in the DOM; a big rsync run prints far more
const MAX_LOG_LINES = 2000;
const LOG_TAIL_BYTES = 262144;

let selectedRun = null;
let liveRun = null;
let stream = null;
let runs = [];
let statusTimer = null;
let truenasLoaded = false;
let pendingLines = [];
let flushTimer = null;
let hiddenLines = 0;
let tailOnly = false;
// Following is inferred from the scroll position, like most log viewers: at the bottom new
// lines keep the view pinned there, scrolled up they count into the "jump to bottom" pill
let following = true;
let unseenLines = 0;
// A run you clicked stays on screen when another run starts; the page follows a new run only
// while it shows the latest one or a run started from here
let pinned = false;
let knownCurrent = null;
let folderList = [];
let isRunning = false;

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

// Date formatting follows ui.locale and ui.clock from the configuration (status response)
let dateLocale;
let hour12 = false;
function fmtTime(iso, withSeconds = true) {
  if (!iso) return "–";
  // hourCycle rather than hour12: Chrome renders midnight as 24:00 with hour12:false
  const options = { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: hour12 ? "h12" : "h23" };
  if (withSeconds) options.second = "2-digit";
  try {
    return new Intl.DateTimeFormat(dateLocale, options).format(new Date(iso));
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(new Date(iso));
  }
}
function fmtDate(iso) {
  if (!iso) return "–";
  const options = { year: "numeric", month: "2-digit", day: "2-digit" };
  try {
    return new Intl.DateTimeFormat(dateLocale, options).format(new Date(iso));
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(new Date(iso));
  }
}
// "in 2 days" / "5 min ago" in the configured locale; the exact time goes in a tooltip
function fmtRelative(iso) {
  const seconds = Math.round((new Date(iso) - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  const [value, unit] = abs < 60 ? [seconds, "second"]
    : abs < 3600 ? [Math.round(seconds / 60), "minute"]
    : abs < 86400 ? [Math.round(seconds / 3600), "hour"]
    : [Math.round(seconds / 86400), "day"];
  try {
    return new Intl.RelativeTimeFormat(dateLocale, { numeric: "auto" }).format(value, unit);
  } catch {
    return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(value, unit);
  }
}
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
const label = (outcome) => {
  const words = outcome.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};
const runKind = (r, sep) => `${r.trigger}${r.delete ? `${sep}with delete` : ""}${r.dry_run ? `${sep}dry run` : ""}`;
// The file name in the title is the download link: a hint that the full log is one click away
function setLogTitle(runId, startedAt, live = false) {
  const title = $("log-title");
  title.textContent = runId ? `Log · ${fmtTime(startedAt)}${live ? " (live)" : ""} · ` : "Log";
  if (!runId) return;
  const link = document.createElement("a");
  link.href = `/api/runs/${runId}/log/download`;
  link.download = "";
  link.title = "Download the full log";
  link.textContent = `${runId}.log`;
  title.appendChild(link);
}

function renderStatus(s) {
  dateLocale = s.locale || undefined;
  hour12 = s.clock === "12h";
  const running = s.state !== "idle";
  const current = s.current_run;
  // The pause switches lock during a run, since a toggle could only apply to the next one
  if (running !== isRunning) { isRunning = running; renderFolders(); }
  const stateEl = $("state");
  stateEl.textContent = running ? (s.state === "cancelling" ? "Stopping…" : "Running") : (s.last_run ? label(s.last_run.outcome) : "No run yet");
  stateEl.className = `big state-${running ? s.state : (s.last_run?.outcome || "idle")}`;
  renderProgress(current);
  renderLastRun(s.last_run, running);
  const next = $("next-run");
  next.textContent = s.next_run_at ? `${fmtRelative(s.next_run_at)} · ${fmtTime(s.next_run_at, false)}`
    : (s.schedule ? "computing…" : "schedule disabled");
  next.title = s.schedule ? `cron: ${s.schedule}` : "";
  $("run").disabled = running;
  $("run-delete").disabled = running;
  $("dry-run").disabled = running;
  $("stop").hidden = !running;
  $("stop").disabled = s.state === "cancelling";

  renderRemote(s.remote);
  $("truenas-card").hidden = !s.truenas_enabled;
  // The page-load fetch runs before status reveals TrueNAS is on; fetch now instead of in 60 s
  if (s.truenas_enabled && !truenasLoaded) loadTrueNAS();

  if (current && liveRun !== current.id) {
    if (!pinned) follow(current.id, current.started_at);
    else if (knownCurrent !== current.id) toast(`Run ${current.id} started · select it in Runs to follow`);
  }
  if (current && liveRun === current.id) loadRunDetail(current.id);
  if (current && knownCurrent !== current.id) { knownCurrent = current.id; loadRuns(); }
  if (!current && knownCurrent) { knownCurrent = null; liveRun = null; loadRuns(); }
}

function renderProgress(current) {
  // The rows only exist while a run is active; dashes in an idle card say nothing
  for (const id of ["row-trigger", "row-progress", "row-elapsed", "progress-bar"]) $(id).hidden = !current;
  if (!current) return;
  const done = current.folders_ok + current.folders_failed;
  $("trigger").textContent = runKind(current, ", ");
  $("progress").textContent = `${done} of ${current.folders_total} folders${current.current_folder ? ` · ${current.current_folder}` : ""}`;
  $("elapsed").textContent = fmtDuration((Date.now() - new Date(current.started_at)) / 1000);
  const bar = $("progress-bar");
  bar.className = "bar progress-fill";
  bar.firstElementChild.style.width = `${current.folders_total ? (done / current.folders_total) * 100 : 0}%`;
}

function renderLastRun(last, running) {
  const dd = $("last-run");
  dd.textContent = "";
  if (!last) { dd.textContent = "–"; return; }
  // When and how long; the full numbers are one click away in Runs. Idle, the header
  // already carries the outcome; during a run it belongs here
  const parts = [`${fmtTime(last.finished_at, false)} (${fmtDuration(last.duration_s)})`];
  if (running) parts.push(label(last.outcome));
  dd.append(parts.join(" · "));
  if (last.folders_failed) {
    const failed = document.createElement("span");
    failed.className = "bad";
    failed.textContent = ` · ${last.folders_failed} failed`;
    dd.appendChild(failed);
  }
}

const HOST_KEY_LABEL = { tofu: "trust on first use", strict: "strict host key", insecure: "host key unverified" };

function renderRemote(remote) {
  const ok = remote.ok;
  $("remote-ok").textContent = ok == null ? "checking…" : ok ? "yes" : "no";
  $("remote-ok").className = ok == null ? "" : ok ? "ok" : "bad";
  const checked = $("remote-checked");
  checked.textContent = remote.checked_at ? fmtRelative(remote.checked_at) : "–";
  checked.title = remote.checked_at ? fmtTime(remote.checked_at) : "";
  $("remote-message").textContent = ok === false ? remote.message : "";
  const details = $("remote-details");
  details.textContent = "";
  const parts = [];
  if (remote.port !== 22) parts.push(`port ${remote.port}`);
  parts.push(remote.auth === "password" ? "password auth" : "key auth");
  details.append(parts.join(" · ") + " · ");
  const policy = document.createElement("span");
  if (remote.host_key_policy === "insecure") policy.className = "warn-text";
  policy.textContent = HOST_KEY_LABEL[remote.host_key_policy] || remote.host_key_policy;
  details.appendChild(policy);
}

async function checkRemote() {
  const button = $("check-remote");
  button.disabled = true;
  button.textContent = "Checking…";
  try { renderRemote(await api("/api/remote/check", { method: "POST" })); }
  catch (e) { toast(e.message, true); }
  finally { button.disabled = false; button.textContent = "Check now"; }
}

async function loadStatus() {
  try { renderStatus(await api("/api/status")); } catch (e) { toast(`Status unavailable: ${e.message}`, true); }
}

// Table cells are built from text nodes, never markup, so a value from the config or the
// API can only ever be displayed
function cell(text, cls = "") {
  const td = document.createElement("td");
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

function renderRuns() {
  const body = $("runs").querySelector("tbody");
  body.innerHTML = "";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    if (r.id === selectedRun) tr.classList.add("selected");
    const folders = cell(`${r.folders_ok}/${r.folders_total}`);
    if (r.folders_failed) {
      const failed = document.createElement("span");
      failed.className = "bad";
      failed.textContent = ` (${r.folders_failed} failed)`;
      folders.appendChild(failed);
    }
    tr.append(
      cell(fmtTime(r.started_at)),
      cell(runKind(r, " + ")),
      cell(label(r.outcome), `state-${r.outcome}`),
      cell(fmtDuration(r.duration_s)),
      folders,
      cell(r.files_transferred, "wide-only"),
      cell(fmtBytes(r.bytes_sent), "wide-only"),
    );
    tr.onclick = () => { pinned = true; showRun(r.id); };
    body.appendChild(tr);
  }
}

const FOLDER_STATUS_CLASS = { ok: "ok", failed: "bad", running: "state-running", cancelled: "warn-text" };

// What happened to each folder of the selected run: the numbers the log buries in rsync output
function renderRunFolders(detail) {
  const wrap = $("run-folders-wrap");
  const body = $("run-folders").querySelector("tbody");
  body.innerHTML = "";
  for (const f of detail.folders) {
    const tr = document.createElement("tr");
    tr.append(
      cell(f.name),
      cell(f.status, FOLDER_STATUS_CLASS[f.status] || "muted"),
      cell(fmtDuration(f.duration_s)),
      cell(f.stats ? f.stats.files_transferred : "–", "wide-only"),
      cell(f.stats ? fmtBytes(f.stats.bytes_sent) : "–", "wide-only"),
      cell(f.message || "", "note"),
    );
    body.appendChild(tr);
  }
  wrap.hidden = !detail.folders.length;
  const failed = detail.folders.filter((f) => f.status === "failed").length;
  $("run-folders-summary").textContent = `Folders in this run · ${detail.folders_ok} of ${detail.folders.length} ok${failed ? `, ${failed} failed` : ""}`;
}

async function loadRunDetail(runId) {
  try {
    const detail = await api(`/api/runs/${runId}`);
    // The selection may have moved while the request was in flight
    if (runId === selectedRun) renderRunFolders(detail);
  } catch (e) { toast(e.message, true); }
}

async function loadRuns() {
  try {
    runs = await api("/api/runs?limit=50");
    renderRuns();
    // On load, the most recent run's log is what people came to see
    if (!selectedRun && !liveRun && runs.length) showRun(runs[0].id);
  } catch (e) { toast(e.message, true); }
}

async function loadFolders() {
  try { folderList = await api("/api/folders"); renderFolders(); }
  catch (e) { toast(e.message, true); }
}

function renderFolders() {
  const body = $("folders").querySelector("tbody");
  body.innerHTML = "";
  for (const f of folderList) {
    const tr = document.createElement("tr");
    // A tick is the exception, like the chips: a quiet row is a normal row. A folder the
    // file disables has no pause state, so it gets no box
    const pause = cell("", "pause");
    if (f.enabled) {
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = f.paused;
      toggle.disabled = isRunning;
      toggle.title = isRunning ? "Wait for the run to finish"
        : f.paused ? "Untick to run this folder again" : "Tick to skip this folder until the container restarts";
      toggle.onchange = () => setPaused(f.name, toggle.checked);
      pause.appendChild(toggle);
    }
    const chips = cell("", "chips");
    // On a phone the paths give way to the switch; they stay one long-press away
    const name = cell(f.name);
    name.title = `${f.source} \u2192 ${f.destination}`;
    tr.append(name, cell(f.source, "path wide-only"), cell(f.destination, "path wide-only"), chips, pause);
    tr.style.cursor = "default";
    if (!f.enabled) chips.appendChild(badge("disabled", "muted"));
    if (f.paused) chips.appendChild(badge("paused", "warn-text"));
    if (!f.source_exists) chips.appendChild(badge("source missing", "bad"));
    if (!f.allow_delete) chips.appendChild(badge("no delete", "muted"));
    body.appendChild(tr);
  }
  // A pause is forgotten at the next restart; say so where the switches are, and point at
  // the permanent switch, so a redeploy never surprises anyone
  const paused = folderList.filter((f) => f.paused).length;
  const notice = $("folders-notice");
  notice.hidden = !paused;
  notice.textContent = paused
    ? `${paused} folder${paused > 1 ? "s" : ""} paused from here until the container restarts. To disable a folder for good, set enabled: false in config.yaml.`
    : "";
}

async function setPaused(name, paused) {
  try {
    await api(`/api/folders/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ paused }) });
    toast(paused ? `${name} paused until the container restarts` : `${name} runs again`);
  } catch (e) { toast(e.message, true); }
  await loadFolders();
}

const MAX_ALERTS_SHOWN = 5;

function badge(text, cls) {
  const span = document.createElement("span");
  span.className = `badge ${cls}`;
  span.textContent = text;
  return span;
}

function renderPool(pool) {
  const div = document.createElement("div");
  div.className = "pool";
  const head = document.createElement("div");
  head.className = "pool-head";
  const name = document.createElement("strong");
  name.textContent = pool.alias;
  const health = badge(pool.status, pool.healthy ? "ok" : "bad");
  head.append(name, health);
  const bar = document.createElement("div");
  bar.className = `bar ${pool.used_pct >= 95 ? "bad-fill" : pool.used_pct >= 85 ? "warn-fill" : ""}`;
  const fill = document.createElement("span");
  fill.style.width = `${Math.min(100, pool.used_pct)}%`;
  bar.appendChild(fill);
  const detail = document.createElement("div");
  detail.className = "muted small";
  let text = `${pool.used_pct}% used · ${fmtBytes(pool.free)} free of ${fmtBytes(pool.size)}`;
  if (pool.scrub_ended_at) {
    const errors = pool.scrub_errors ? `${pool.scrub_errors} errors` : "no errors";
    text += ` · last scrub ${fmtDate(pool.scrub_ended_at)}, ${errors}`;
  }
  detail.textContent = text;
  div.append(head, bar, detail);
  return div;
}

function renderAlert(alert) {
  const div = document.createElement("div");
  div.className = "alert";
  const cls = alert.rank >= 4 ? "bad" : alert.rank >= 3 ? "warn-text" : "muted";
  const text = document.createElement("span");
  text.textContent = alert.text;
  // When it was raised matters less than what it says; keep it as a tooltip
  if (alert.since) text.title = `Since ${fmtTime(alert.since, false)}`;
  div.append(badge(alert.level, cls), text);
  return div;
}

async function loadTrueNAS() {
  if ($("truenas-card").hidden) return;
  truenasLoaded = true;
  try {
    const data = await api("/api/truenas");
    const pools = $("truenas-pools"), alerts = $("truenas-alerts"), error = $("truenas-error");
    pools.innerHTML = ""; alerts.innerHTML = "";
    error.textContent = data.error || "";
    error.hidden = !data.error;
    if (data.error) return;
    data.pools.forEach((pool) => pools.appendChild(renderPool(pool)));
    const active = data.alerts.filter((a) => !a.dismissed);
    if (!active.length) {
      const none = document.createElement("div");
      none.className = "muted small";
      none.textContent = "No alerts";
      alerts.appendChild(none);
    } else {
      active.slice(0, MAX_ALERTS_SHOWN).forEach((alert) => alerts.appendChild(renderAlert(alert)));
      if (active.length > MAX_ALERTS_SHOWN) {
        const more = document.createElement("div");
        more.className = "muted small";
        more.textContent = `and ${active.length - MAX_ALERTS_SHOWN} more`;
        alerts.appendChild(more);
      }
    }
    $("truenas-asof").textContent = data.fetched_at ? `as of ${fmtTime(data.fetched_at)}` : "";
  } catch (e) { toast(e.message, true); }
}

function appendLine(text) {
  // Lines are buffered and flushed a few times a second: one DOM update and one scroll per
  // batch instead of one per line, which is what made a busy run freeze the page
  pendingLines.push(text);
  if (!flushTimer) flushTimer = setTimeout(flushLines, 150);
}

function flushLines() {
  flushTimer = null;
  if (!pendingLines.length) return;
  const log = $("log");
  const fragment = document.createDocumentFragment();
  for (const text of pendingLines) {
    const span = document.createElement("span");
    span.textContent = text + "\n";
    if (text.includes("[ERROR]")) span.className = "err";
    else if (text.includes("[WARN]")) span.className = "warn";
    fragment.appendChild(span);
  }
  const added = pendingLines.length;
  pendingLines = [];
  log.appendChild(fragment);
  while (log.childElementCount > MAX_LOG_LINES) {
    log.removeChild(log.firstElementChild);
    hiddenLines++;
  }
  updateLogNotice();
  if (following) {
    log.scrollTop = log.scrollHeight;
  } else {
    unseenLines += added;
    updateJump();
  }
}

function updateJump() {
  const jump = $("jump");
  jump.hidden = following;
  jump.textContent = unseenLines ? `${unseenLines} new lines · jump to bottom` : "Jump to bottom";
}

function onLogScroll() {
  const log = $("log");
  following = log.scrollHeight - log.scrollTop - log.clientHeight < 8;
  if (following) unseenLines = 0;
  updateJump();
}

function resetLog(runId, startedAt, live = false) {
  pendingLines = [];
  hiddenLines = 0;
  tailOnly = false;
  following = true;
  unseenLines = 0;
  $("log").textContent = "";
  $("run-folders-wrap").hidden = true;
  updateJump();
  setLogTitle(runId, startedAt, live);
  updateLogNotice();
}

function updateLogNotice() {
  // One sentence whether the cut came from fetching only the file's tail or from the
  // line cap of the panel; the title's file name is the link to the whole log
  const truncated = tailOnly || hiddenLines > 0;
  const visible = $("log").childElementCount;
  $("log-notice").textContent = truncated ? `Showing the last ${visible} lines · download the full log for more` : "";
  $("log-notice").hidden = !truncated;
}

function closeStream() {
  if (stream) { stream.close(); stream = null; }
}

function follow(runId, startedAt) {
  closeStream();
  selectedRun = runId;
  liveRun = runId;
  pinned = false;
  resetLog(runId, startedAt, true);
  renderRuns();
  loadRunDetail(runId);
  stream = new EventSource(`/api/runs/${runId}/log/stream`);
  stream.addEventListener("line", (e) => appendLine(e.data));
  stream.addEventListener("end", () => { closeStream(); flushLines(); setLogTitle(runId, startedAt); loadRunDetail(runId); loadRuns(); loadStatus(); });
  stream.onerror = () => { /* the browser reconnects with Last-Event-ID on its own */ };
}

async function showRun(runId) {
  const run = runs.find((r) => r.id === runId);
  // Selecting the run in progress means following it live, not reading a snapshot
  if (run && run.outcome === "running") { follow(runId, run.started_at); return; }
  closeStream();
  selectedRun = runId;
  renderRuns();
  resetLog(runId, run ? run.started_at : null);
  loadRunDetail(runId);
  try {
    // Only the end of a finished log is shown; the full file is one click away
    const response = await fetch(`/api/runs/${runId}/log?tail=${LOG_TAIL_BYTES}`);
    if (!response.ok) throw new Error(await response.text());
    const text = await response.text();
    tailOnly = Number(response.headers.get("X-Log-Start")) > 0;
    text.split("\n").filter((l, i, a) => l || i < a.length - 1).forEach(appendLine);
    flushLines();
  } catch (e) { toast(e.message, true); }
}

async function startRun(withDelete, dryRun = false) {
  if (withDelete && !confirm("Run with --delete? Files removed locally will be deleted from the backup.")) return;
  try {
    const result = await api("/api/runs", { method: "POST", body: JSON.stringify({ delete: withDelete, dry_run: dryRun }) });
    toast(`${dryRun ? "Dry run" : "Run"} ${result.run_id} started`);
    pinned = false;
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
  try { await api("/api/runs", { method: "DELETE" }); selectedRun = null; resetLog(null); await loadRuns(); await loadStatus(); }
  catch (e) { toast(e.message, true); }
}

$("run").onclick = () => startRun(false);
$("run-delete").onclick = () => startRun(true);
$("dry-run").onclick = () => startRun(false, true);
$("stop").onclick = stopRun;
$("check-remote").onclick = checkRemote;
$("log").addEventListener("scroll", onLogScroll);
$("jump").onclick = () => { $("log").scrollTop = $("log").scrollHeight; };
// The button sits inside a <summary>; without preventDefault the click would also fold the card
$("clear").onclick = (e) => { e.preventDefault(); clearHistory(); };

// Cards and folds start closed; which ones you open is a per-browser preference kept in localStorage
for (const card of document.querySelectorAll("details.card, details.fold")) {
  const key = `remotebackups.open.${card.id}`;
  try { card.open = localStorage.getItem(key) === "1"; } catch { /* storage unavailable */ }
  card.addEventListener("toggle", () => {
    try { localStorage.setItem(key, card.open ? "1" : "0"); } catch { /* storage unavailable */ }
  });
}

loadStatus(); loadRuns(); loadFolders(); loadTrueNAS();
statusTimer = setInterval(loadStatus, STATUS_INTERVAL_MS);
setInterval(loadRuns, RUNS_INTERVAL_MS);
setInterval(loadTrueNAS, TRUENAS_INTERVAL_MS);
