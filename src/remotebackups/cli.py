"""Command-line entry point.

`run` is what the container executes; the other subcommands help configure and debug it.
"""

import argparse
import asyncio
import os
import signal
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence

from pydantic import ValidationError

from remotebackups import __version__
from remotebackups.app import Application, WebStartError
from remotebackups.clock import SystemClock
from remotebackups.config import Config, ConfigError, EnvSettings, dump_config, load_config
from remotebackups.config.loader import EXAMPLE_TEXT, ensure_config_files, format_validation_errors
from remotebackups.logging_setup import configure_logging
from remotebackups.paths import ConfigPaths
from remotebackups.sync import (
    RunHistory,
    RunOptions,
    RunOutcome,
    RunTrigger,
    SyncEngine,
    UnknownFolderError,
)

EXIT_RUN_NOT_CLEAN = 1
EXIT_CONFIG_ERROR = 2
EXIT_START_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remotebackups",
        description="Scheduled rsync backups of home-server folders to another host.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        "run", help="serve: scheduler, web UI and manual runs (the container default)"
    )
    commands.add_parser("check-config", help="validate the configuration and summarise it")
    show = commands.add_parser("print-config", help="print the effective configuration as YAML")
    show.add_argument("--redact", action="store_true", help="mask secrets")
    commands.add_parser("print-example-config", help="print the commented example config.yaml")
    commands.add_parser("healthcheck", help="exit 0 when the local web server reports healthy")
    once = commands.add_parser("run-once", help="run one backup now in the foreground and exit")
    once.add_argument("--delete", action="store_true", help="pass --delete to rsync")
    once.add_argument(
        "--dry-run", action="store_true", help="pass --dry-run to rsync: report, transfer nothing"
    )
    once.add_argument(
        "--folder", action="append", dest="folders", metavar="NAME", help="only this folder"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
        return 0
    if args.command == "print-example-config":
        print(EXAMPLE_TEXT, end="")
        return 0
    if args.command == "healthcheck":
        return healthcheck()

    try:
        env = EnvSettings()
    except ValueError as exc:
        # Field problems arrive wrapped; a FILE__ variable pointing nowhere is a bare ValueError
        problems = format_validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]
        return _report(ConfigError([f"environment: {p}" for p in problems]))
    configure_logging(env.log_level)
    paths = ConfigPaths(
        config_dir=env.config_dir, runtime_dir=env.runtime_dir, secrets_dir=env.secrets_dir
    )
    paths.ensure()
    ensure_config_files(paths)
    try:
        config = load_config(paths, env)
    except ConfigError as exc:
        return _report(exc)

    if args.command == "check-config":
        enabled = len(config.enabled_folders)
        print(
            f"OK: {enabled} enabled folder(s) of {len(config.folders)}, "
            f"schedule {config.schedule.cron or 'disabled'}, "
            f"TrueNAS {'on' if config.truenas.is_enabled else 'off'}"
        )
        return 0
    if args.command == "print-config":
        print(dump_config(config, redact=args.redact), end="")
        return 0
    if args.command == "run":
        try:
            return asyncio.run(Application(env, paths).run())
        except ConfigError as exc:
            return _report(exc)
        except WebStartError as exc:
            print(f"Cannot start: {exc}", file=sys.stderr)
            return EXIT_START_ERROR
    options = RunOptions(delete=args.delete, dry_run=args.dry_run, folders=args.folders)
    return asyncio.run(_run_once(paths, env, config, options))


async def _run_once(
    paths: ConfigPaths, env: EnvSettings, config: Config, options: RunOptions
) -> int:
    clock = SystemClock()
    history = RunHistory(paths.runs_dir, config.history.keep_days, clock)
    history.reconcile_unfinished()
    engine = SyncEngine(lambda: load_config(paths, env), paths, history, clock)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(engine.cancel("signal")))
    try:
        record = await engine.start(RunTrigger.MANUAL, options)
    except UnknownFolderError as exc:
        return _report(ConfigError([str(exc)]))
    print(f"Run {record.id} started; log: {history.log_path(record.id)}")
    finished = await engine.wait()
    assert finished is not None
    for folder in finished.folders:
        detail = f" ({folder.message})" if folder.message else ""
        print(f"  {folder.name}: {folder.status.value}{detail}")
    print(f"Outcome: {finished.outcome.value}")
    return 0 if finished.outcome is RunOutcome.COMPLETED else EXIT_RUN_NOT_CLEAN


def healthcheck() -> int:
    """The Docker HEALTHCHECK: no curl or wget needed, honours WEB_PORT."""
    port = os.environ.get("WEB_PORT", "8080")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=4) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1


def _report(error: ConfigError) -> int:
    print("Configuration problems:", file=sys.stderr)
    for problem in error.problems:
        print(f"  - {problem}", file=sys.stderr)
    return EXIT_CONFIG_ERROR
