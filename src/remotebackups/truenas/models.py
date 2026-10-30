"""What the UI shows about the TrueNAS box, parsed from `pool.query` and `alert.list`."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, computed_field

# Higher is more urgent; unknown levels sort between NOTICE and WARNING
ALERT_RANK = {
    "INFO": 0,
    "NOTICE": 1,
    "WARNING": 3,
    "ERROR": 4,
    "CRITICAL": 5,
    "ALERT": 6,
    "EMERGENCY": 7,
}


class PoolInfo(BaseModel):
    name: str
    alias: str
    status: str
    healthy: bool
    size: int
    allocated: int
    free: int
    scrub_state: str | None = None
    scrub_ended_at: datetime | None = None
    scrub_errors: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def used_pct(self) -> float:
        return round(self.allocated / self.size * 100, 1) if self.size else 0.0

    @classmethod
    def from_api(cls, pool: dict[str, Any], aliases: dict[str, str]) -> "PoolInfo":
        name = str(pool.get("name", "unknown"))
        scan = pool.get("scan") or {}
        return cls(
            name=name,
            alias=aliases.get(name, name),
            status=str(pool.get("status", "UNKNOWN")),
            healthy=bool(pool.get("healthy", False)),
            size=int(pool.get("size") or 0),
            allocated=int(pool.get("allocated") or 0),
            free=int(pool.get("free") or 0),
            scrub_state=scan.get("state"),
            scrub_ended_at=parse_timestamp(scan.get("end_time")),
            scrub_errors=scan.get("errors"),
        )


class AlertInfo(BaseModel):
    level: str
    text: str
    since: datetime | None = None
    dismissed: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rank(self) -> int:
        return ALERT_RANK.get(self.level, 2)

    @classmethod
    def from_api(cls, alert: dict[str, Any]) -> "AlertInfo":
        return cls(
            level=str(alert.get("level", "INFO")).upper(),
            text=str(alert.get("formatted") or alert.get("text") or "unknown alert"),
            since=parse_timestamp(alert.get("datetime")),
            dismissed=bool(alert.get("dismissed", False)),
        )


def parse_timestamp(value: Any) -> datetime | None:
    """TrueNAS sends dates as `{"$date": <ms>}` over DDP; be lenient about the rest."""
    if isinstance(value, dict):
        value = value.get("$date")
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
