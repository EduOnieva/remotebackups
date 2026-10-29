"""Application logging: stderr only, so stdout stays clean for CLI output; Docker owns rotation."""

import logging
import sys

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format=FORMAT, stream=sys.stderr, force=True)
    # uvicorn's own access log duplicates what the app logs; keep only its warnings
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
