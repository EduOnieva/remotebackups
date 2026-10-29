"""Optional TrueNAS SCALE status: alert count and free space per pool."""

from remotebackups.truenas.client import TrueNASClient
from remotebackups.truenas.service import TrueNASService

__all__ = ["TrueNASClient", "TrueNASService"]
