import json
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from remotebackups.truenas.client import TrueNASClient

TIB = 1024**4

Scripted = str | Callable[["FakeWebSocket"], str]


class FakeWebSocket:
    """Scripted DDP server side: records what was sent and replays queued messages on recv()."""

    def __init__(self, scripted: list[Scripted]) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming = list(scripted)
        self.closed = False

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def recv(self) -> str:
        item = self.incoming.pop(0)
        return item(self) if callable(item) else item

    def close(self) -> None:
        self.closed = True


def result_for_last_request(result: Any) -> Callable[[FakeWebSocket], str]:
    return lambda ws: json.dumps({"msg": "result", "id": ws.sent[-1]["id"], "result": result})


CONNECTED = json.dumps({"msg": "connected", "session": "abc123"})
POOLS = [
    {"id": 1, "name": "backup-disk-0", "status": "ONLINE", "free": int(3.42 * TIB)},
    {"id": 3, "name": "backup-disk-1", "status": "DEGRADED", "free": int(0.5 * TIB)},
]
ALERTS = [{"id": "f1e2", "level": "WARNING", "formatted": "Pool backup-disk-1 state is DEGRADED"}]


def client(**kwargs: Any) -> TrueNASClient:
    return TrueNASClient("100.64.0.9", "1-testtoken", **kwargs)


def test_pool_status_keys_pools_by_name() -> None:
    ws = FakeWebSocket(
        [
            CONNECTED,
            result_for_last_request(True),
            result_for_last_request(ALERTS),
            result_for_last_request(POOLS),
        ]
    )
    with patch("remotebackups.truenas.client.create_connection", return_value=ws):
        full = client().query()
    status = full.result

    assert status == {"alerts": 1, "backup-disk-0": "3.42 TB", "backup-disk-1": "0.5 TB"}
    assert full.alert_texts == ("Pool backup-disk-1 state is DEGRADED",)
    methods = [m["method"] for m in ws.sent if m["msg"] == "method"]
    assert methods == ["auth.login_with_api_key", "alert.list", "pool.query"]
    assert ws.sent[1]["params"] == ["1-testtoken"]
    assert len({m["id"] for m in ws.sent if m["msg"] == "method"}) == 3, "one id per call"
    assert ws.closed


def test_aliases_rename_pools_and_unlisted_pools_keep_their_name() -> None:
    ws = FakeWebSocket(
        [
            CONNECTED,
            result_for_last_request(True),
            result_for_last_request([]),
            result_for_last_request(POOLS),
        ]
    )
    with patch("remotebackups.truenas.client.create_connection", return_value=ws):
        status = client(pool_aliases={"backup-disk-0": "disk-0"}).get_pool_status()
    assert status == {"alerts": 0, "disk-0": "3.42 TB", "backup-disk-1": "0.5 TB"}


def test_unrelated_messages_are_skipped() -> None:
    ws = FakeWebSocket(
        [
            CONNECTED,
            "",
            json.dumps({"msg": "pong"}),
            result_for_last_request(True),
            json.dumps({"msg": "changed", "collection": "alert.list"}),
            json.dumps({"msg": "result", "id": "someone-elses-call", "result": "ignored"}),
            result_for_last_request([]),
            result_for_last_request(POOLS[:1]),
        ]
    )
    with patch("remotebackups.truenas.client.create_connection", return_value=ws):
        status = client().get_pool_status()
    assert status == {"alerts": 0, "backup-disk-0": "3.42 TB"}


def test_ddp_error_is_reported() -> None:
    ws = FakeWebSocket(
        [
            CONNECTED,
            lambda s: json.dumps(
                {"msg": "result", "id": s.sent[-1]["id"], "error": {"reason": "Invalid API key"}}
            ),
        ]
    )
    with patch("remotebackups.truenas.client.create_connection", return_value=ws):
        status = client().get_pool_status()
    assert set(status) == {"error"}
    assert "Invalid API key" in str(status["error"])
    assert ws.closed


def test_connection_failure_is_reported() -> None:
    with patch(
        "remotebackups.truenas.client.create_connection",
        side_effect=OSError(113, "Host is unreachable"),
    ):
        status = client().get_pool_status()
    assert set(status) == {"error"}
    assert "Host is unreachable" in str(status["error"])


def test_tls_verification_is_passed_through() -> None:
    ws = FakeWebSocket([CONNECTED, result_for_last_request(False)])
    with patch("remotebackups.truenas.client.create_connection", return_value=ws) as connect:
        status = client(verify_tls=True).get_pool_status()
    assert "API key rejected" in str(status["error"])
    assert connect.call_args.kwargs["sslopt"]["cert_reqs"] == 2, "ssl.CERT_REQUIRED"
