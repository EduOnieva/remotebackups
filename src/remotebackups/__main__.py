"""Allow `python -m remotebackups`."""

import sys

from remotebackups.cli import main

sys.exit(main())
