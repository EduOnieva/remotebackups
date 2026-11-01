"""Seed the demo: source folders with files, config.yaml, and a week of run history."""
import os, random, sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from remotebackups.sync.models import FolderResult, FolderStatus, RsyncStats, RunOutcome, RunRecord, RunTrigger

demo = Path(sys.argv[1])
data, config, runs = demo / "data", demo / "config", demo / "config" / "runs"
random.seed(7)
TZ = ZoneInfo("Europe/Madrid")

FOLDERS = {  # name: (dest, files, avg size, names)
    "documents": ("/mnt/tank/backup/documents", 180, 90_000, ["Invoice-2026-{:02d}.pdf", "notes/{}.md", "taxes/2025/form-{}.pdf", "contracts/lease-{}.docx"]),
    "photos": ("/mnt/tank/backup/photos", 60, 2_400_000, ["2026/10/IMG_{:04d}.jpg", "2026/09/IMG_{:04d}.jpg", "2024/trip/DSC{:04d}.jpg"]),
    "music": ("/mnt/tank/backup/music", 90, 6_000_000, ["Artist {0}/Album/{0:02d} - Track.flac", "Artist {0}/Album/cover.jpg"]),
    "projects": ("/mnt/tank/backup/projects", 240, 20_000, ["remotebackups/src/{}.py", "homepage/config/{}.yaml", "notes/{}.txt"]),
    "nextcloud": ("/mnt/tank/backup/nextcloud", 150, 400_000, ["data/anna/files/{}.odt", "data/anna/files/Scans/{}.pdf"]),
    "paperless": ("/mnt/tank/backup/paperless", 120, 300_000, ["media/documents/originals/{:07d}.pdf", "media/documents/thumbnails/{:07d}.webp"]),
    "home-assistant": ("/mnt/tank/backup/home-assistant", 40, 15_000, ["{}.yaml", "custom_components/{}/manifest.json", "www/{}.png"]),
    "videos": ("/mnt/vault/backup/videos", 3, 90_000_000, ["Family/2026-{:02d}-birthday.mp4", "Family/holiday-{}.mp4"]),
}

def make_files() -> None:
    for name, (_, count, avg, patterns) in FOLDERS.items():
        root = data / name
        for i in range(count):
            rel = random.choice(patterns).format(i + 1)
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            size = max(1_000, int(random.gauss(avg, avg / 3)))
            path.write_bytes(os.urandom(min(size, 2_000_000)) if size <= 2_000_000 else os.urandom(1_000_000) * (size // 1_000_000))

def write_config() -> None:
    lines = ["# remotebackups configuration for the demo", "", "schedule:", '  cron: "30 2 * * *"', "", "folders:"]
    for name, (dest, *_rest) in FOLDERS.items():
        lines += [f"  - source: /data/{name}", f"    destination: {dest}"]
        if name == "videos":
            lines += ['    extra_options: ["--bwlimit=4000"]']
        if name == "paperless":
            lines += ["    allow_delete: false"]
    lines += ["", "ui:", "  locale: en-GB", ""]
    (config / "config.yaml").write_text("\n".join(lines))

def run_id(started: datetime) -> str:
    return f"{started.astimezone(ZoneInfo('UTC')):%Y%m%dT%H%M%SZ}-{random.randrange(16**4):04x}"

def rsync_log(name: str, cmd_flags: str, files: int, sent: int, created: int, deleted: int, size: int, exit_code: int = 0) -> list[str]:
    dest = FOLDERS[name][0]
    lines = [f"rsync -a -v -z --partial --stats --no-owner --no-group{cmd_flags} --timeout=3600 '--exclude=*.Trash*' --exclude=lost+found '--exclude=System Volume Information' --exclude=.DS_Store --exclude=Thumbs.db --exclude=desktop.ini --exclude=.venv -e 'ssh -F /run/remotebackups/ssh/config' /data/{name}/ backup@nas.lan:{dest}/",
             "sending incremental file list"]
    patterns = FOLDERS[name][3]
    for i in range(min(files, 6)):
        lines.append(random.choice(patterns).format(random.randrange(1, 400)))
    if exit_code == 23:
        lines.append(f'rsync: [sender] send_files failed to open "/data/{name}/2024/trip/DSC0412.jpg": Permission denied (13)')
    lines += ["", f"Number of files: {size // 100_000 + files:,} (reg: {size // 120_000 + files:,}, dir: {size // 900_000 + 3:,})",
              f"Number of created files: {created:,}", f"Number of deleted files: {deleted:,}", f"Number of regular files transferred: {files:,}",
              f"Total file size: {size:,} bytes", f"Total transferred file size: {sent:,} bytes", f"Literal data: {sent:,} bytes", "Matched data: 0 bytes",
              f"File list size: {files * 48:,}", "File list generation time: 0.004 seconds", "File list transfer time: 0.000 seconds",
              f"Total bytes sent: {sent + files * 60:,}", f"Total bytes received: {files * 20 + 400:,}", "",
              f"sent {sent + files * 60:,} bytes  received {files * 20 + 400:,} bytes  {(sent + files * 60) // 3:,}.00 bytes/sec",
              f"total size is {size:,}  speedup is {size / max(1, sent + files * 60):,.2f}"]
    if exit_code == 23:
        lines.append("rsync error: some files/attrs were not transferred (see previous errors) (code 23) at main.c(1338) [sender=3.4.1]")
    return lines

SIZES = {"documents": 16_400_000_000, "photos": 148_000_000_000, "music": 91_000_000_000, "projects": 4_800_000_000,
         "nextcloud": 61_000_000_000, "paperless": 35_000_000_000, "home-assistant": 600_000_000, "videos": 410_000_000_000}

def make_run(started: datetime, trigger: RunTrigger, *, delete=False, dry_run=False, outcome=RunOutcome.COMPLETED,
             failing: str | None = None, cancel_at: str | None = None, only: list[str] | None = None, activity: float = 1.0) -> None:
    names = only or list(FOLDERS)
    t = started
    log = [f"{t:%Y-%m-%d %H:%M:%S} [INFO] Run {{id}} started: trigger={trigger.value} delete={delete} dry_run={dry_run} folders={len(names)}"]
    results, cancelled = [], False
    for name in names:
        res = FolderResult(name=name, source=f"/data/{name}", destination=FOLDERS[name][0])
        if cancelled:
            res.status, res.message = FolderStatus.SKIPPED, "run cancelled (user)"
            results.append(res); continue
        res.started_at = t
        log.append(f"{t:%Y-%m-%d %H:%M:%S} [INFO] Folder {name}: /data/{name} -> {FOLDERS[name][0]}")
        files = int(random.randint(0, 40) * activity) if name != "videos" else int(random.choice([0, 0, 1, 2]) * activity)
        sent = files * random.randint(50_000, 3_000_000) if name != "videos" else files * 1_900_000_000
        deleted = random.randint(2, 30) if delete and name in ("documents", "projects", "nextcloud") else 0
        if dry_run: sent = sent  # rsync still reports what it would send
        flags = (" --delete" if delete and name != "paperless" else "") + (" --dry-run" if dry_run else "")
        exit_code = 23 if name == failing else 0
        body = rsync_log(name, flags, files, sent if not dry_run else sent, max(0, files - 2), deleted, SIZES[name], exit_code)
        log += body
        duration = timedelta(seconds=random.uniform(3, 20) + (sent / 40_000_000 if not dry_run else sent / 400_000_000))
        if name == cancel_at:
            duration = timedelta(seconds=random.uniform(40, 90))
            t2 = t + duration
            log.append(f"{t2:%Y-%m-%d %H:%M:%S} [WARN] Folder {name}: interrupted by stop (user); rsync exit -15")
            res.status, res.exit_code, res.message = FolderStatus.CANCELLED, -15, "interrupted by stop (user); rsync exit -15"
            res.finished_at = t2; cancelled = True; t = t2; results.append(res); continue
        t2 = t + duration
        res.exit_code = exit_code
        res.stats = RsyncStats(files_total=SIZES[name] // 100_000 + files, files_transferred=files, files_created=max(0, files - 2),
                               files_deleted=deleted, total_size=SIZES[name], bytes_sent=sent + files * 60, bytes_received=files * 20 + 400,
                               speedup=round(SIZES[name] / max(1, sent + files * 60), 2))
        res.finished_at = t2
        if exit_code:
            res.status, res.message = FolderStatus.FAILED, "rsync exit 23: partial transfer due to error (see the rsync messages above)"
            log.append(f"{t2:%Y-%m-%d %H:%M:%S} [ERROR] Folder {name}: {res.message}")
        else:
            res.status = FolderStatus.OK
            log.append(f"{t2:%Y-%m-%d %H:%M:%S} [INFO] Folder {name}: done")
        t = t2
        results.append(res)
    record = RunRecord(id=run_id(started), trigger=trigger, delete=delete, dry_run=dry_run, started_at=started, finished_at=t,
                       outcome=outcome, folders=results, reason="user" if cancel_at else None)
    log[0] = log[0].replace("{id}", record.id)
    level = "INFO" if outcome is RunOutcome.COMPLETED else "WARN"
    log.append(f"{t:%Y-%m-%d %H:%M:%S} [{level}] Run {record.id} finished: outcome={outcome.value} ok={record.folders_ok} failed={record.folders_failed}")
    (runs / f"{record.id}.json").write_text(record.model_dump_json(indent=2))
    (runs / f"{record.id}.log").write_text("\n".join(log) + "\n")

def make_history() -> None:
    runs.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ).replace(hour=2, minute=30, second=random.randint(0, 20), microsecond=0)
    for days_ago in range(7, 0, -1):
        d = today - timedelta(days=days_ago)
        if days_ago == 3:
            make_run(d, RunTrigger.SCHEDULED, outcome=RunOutcome.COMPLETED_WITH_ERRORS, failing="photos")
        else:
            make_run(d, RunTrigger.SCHEDULED, activity=random.choice([0.3, 0.6, 1.0]))
    make_run(today - timedelta(days=5, hours=6, minutes=50), RunTrigger.MANUAL, outcome=RunOutcome.CANCELLED, cancel_at="videos")
    make_run(today - timedelta(days=2) + timedelta(hours=17, minutes=44), RunTrigger.MANUAL, delete=True, dry_run=True)
    make_run(today - timedelta(days=2) + timedelta(hours=17, minutes=51), RunTrigger.MANUAL, delete=True)
    make_run(today - timedelta(days=1) + timedelta(hours=15, minutes=35), RunTrigger.MANUAL, only=["projects"])
    make_run(today, RunTrigger.SCHEDULED, activity=0.4)

make_files(); write_config(); make_history()
print("files:", sum(1 for _ in data.rglob("*") if _.is_file()), "runs:", len(list(runs.glob("*.json"))))
