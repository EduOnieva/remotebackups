"""Read alerts and pool free space from TrueNAS over its DDP WebSocket API (blocking)."""

import json
import logging
import ssl
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from websocket import create_connection

logger = logging.getLogger(__name__)

TIB = 1024**4


@dataclass(frozen=True)
class PoolStatus:
    """What one query produced: the API-shaped result plus the alert texts for logging."""

    result: dict[str, int | str]
    alert_texts: tuple[str, ...] = field(default_factory=tuple)


class TrueNASClient:
    # Events and pings can arrive before our result; stop waiting after this many
    MAX_SKIPPED_MESSAGES = 50

    def __init__(
        self,
        host: str,
        api_key: str,
        *,
        verify_tls: bool = False,
        pool_aliases: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self._host = host
        self._api_key = api_key
        self._verify_tls = verify_tls
        self._aliases = dict(pool_aliases or {})
        self._timeout = timeout_seconds

    def get_pool_status(self) -> dict[str, int | str]:
        """`{"alerts": n, "<alias or pool>": "X TB", ...}`, or `{"error": "..."}`."""
        return self.query().result

    def query(self) -> PoolStatus:
        ws = None
        try:
            ws = create_connection(
                f"wss://{self._host}/websocket",
                sslopt={"cert_reqs": ssl.CERT_REQUIRED if self._verify_tls else ssl.CERT_NONE},
                timeout=self._timeout,
            )
            ws.send(json.dumps({"msg": "connect", "version": "1", "support": ["1"]}))
            handshake = json.loads(ws.recv())
            if handshake.get("msg") != "connected":
                raise RuntimeError(f"DDP handshake rejected: {handshake.get('msg')}")
            if not self._call(ws, "auth.login_with_api_key", [self._api_key]):
                raise RuntimeError("API key rejected")
            alerts = self._call(ws, "alert.list") or []
            pools = self._call(ws, "pool.query") or []
        except Exception as exc:
            # A NAS that is off or asleep is expected; the caller decides how loud to be
            return PoolStatus({"error": f"Failed to connect to TrueNAS: {exc}"})
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception as exc:
                    logger.warning("Error closing TrueNAS WebSocket: %s", exc)

        result: dict[str, int | str] = {"alerts": len(alerts)}
        texts = tuple(str(a.get("formatted", a.get("text", "unknown"))) for a in alerts)
        for pool in pools:
            # Keyed by name (or its alias): TrueNAS ids are database rows that change on recreate
            name = pool.get("name", "unknown")
            free_tb = round(pool.get("free", 0) / TIB, 2)
            result[self._aliases.get(name, name)] = f"{free_tb} TB"
        return PoolStatus(result, texts)

    def _call(self, ws: Any, method: str, params: list[Any] | None = None) -> Any:
        """One DDP method call; unrelated traffic (events, pongs, other ids) is skipped."""
        request_id = str(uuid.uuid4())
        ws.send(
            json.dumps(
                {"id": request_id, "msg": "method", "method": method, "params": params or []}
            )
        )
        for _ in range(self.MAX_SKIPPED_MESSAGES):
            raw = ws.recv()
            if not raw or not raw.strip():
                continue
            response = json.loads(raw)
            if response.get("msg") != "result" or response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"DDP method {method} failed: {response['error']}")
            return response.get("result")
        raise RuntimeError(
            f"No result for DDP method {method} after {self.MAX_SKIPPED_MESSAGES} messages"
        )
