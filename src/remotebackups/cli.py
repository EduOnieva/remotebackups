"""Command-line entry point.

Subcommands arrive with the modules they drive. Today: inspecting the configuration.
"""

import argparse
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from remotebackups import __version__
from remotebackups.config import ConfigError, EnvSettings, dump_config, load_config
from remotebackups.config.loader import EXAMPLE_TEXT, ensure_config_files, format_validation_errors
from remotebackups.logging_setup import configure_logging
from remotebackups.paths import ConfigPaths

EXIT_CONFIG_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remotebackups",
        description="Scheduled rsync backups of home-server folders to another host.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("check-config", help="validate the configuration and summarise it")
    show = commands.add_parser("print-config", help="print the effective configuration as YAML")
    show.add_argument("--redact", action="store_true", help="mask secrets")
    commands.add_parser("print-example-config", help="print the commented example config.yaml")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        build_parser().print_help()
        return 0
    if args.command == "print-example-config":
        print(EXAMPLE_TEXT, end="")
        return 0

    try:
        env = EnvSettings()
    except ValueError as exc:
        # Field problems arrive wrapped; a FILE__ variable pointing nowhere is a bare ValueError
        problems = format_validation_errors(exc) if isinstance(exc, ValidationError) else [str(exc)]
        return _report(ConfigError([f"environment: {p}" for p in problems]))
    configure_logging(env.log_level)
    paths = ConfigPaths(config_dir=env.config_dir, runtime_dir=env.runtime_dir)
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
    elif args.command == "print-config":
        print(dump_config(config, redact=args.redact), end="")
    return 0


def _report(error: ConfigError) -> int:
    print("Configuration problems:", file=sys.stderr)
    for problem in error.problems:
        print(f"  - {problem}", file=sys.stderr)
    return EXIT_CONFIG_ERROR
