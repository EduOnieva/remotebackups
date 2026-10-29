"""Command-line entry point.

Subcommands arrive with the modules they drive; for now the CLI only knows its version.
"""

import argparse
from collections.abc import Sequence

from remotebackups import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remotebackups",
        description="Scheduled rsync backups of home-server folders to another host.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
