#!/usr/bin/env python3
"""Unit tests for the TrueNAS DDP client in web_server.py."""

import io
import json
from typing import Any, Callable, Dict, List, Union
from unittest.mock import Mock, patch

import pytest

import web_server

TB = 1024 ** 4

# One message recv() hands back; a callable gets the socket so it can echo the request id
Scripted = Union[str, Callable[['FakeWebSocket'], str]]


class FakeWebSocket:
    """Scripted DDP server side: records what was sent and replays queued messages on recv()."""

    def __init__(self, scripted: List[Scripted]) -> None:
        self.sent: List[Dict[str, Any]] = []
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
    return lambda ws: json.dumps({'msg': 'result', 'id': ws.sent[-1]['id'], 'result': result})


CONNECTED = json.dumps({'msg': 'connected', 'session': 'abc123'})

POOLS = [
    {'id': 1, 'name': 'backup-disk-0', 'status': 'ONLINE', 'healthy': True,
     'size': 7 * TB, 'allocated': int(3.58 * TB), 'free': int(3.42 * TB)},
    {'id': 3, 'name': 'backup-disk-1', 'status': 'DEGRADED', 'healthy': False,
     'size': 4 * TB, 'allocated': int(3.5 * TB), 'free': int(0.5 * TB)},
]

ALERTS = [
    {'id': 'f1e2', 'level': 'WARNING', 'klass': 'VolumeStatus', 'dismissed': False,
     'formatted': 'Pool backup-disk-1 state is DEGRADED: One or more devices has experienced an error.'},
]


@pytest.fixture
def truenas_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('REMOTE_HOST', '100.64.0.9')
    monkeypatch.setenv('REMOTE_API_TOKEN', '1-testtoken')


def test_pool_status_keys_pools_by_name(truenas_env: None) -> None:
    ws = FakeWebSocket([
        CONNECTED,
        result_for_last_request(True),
        result_for_last_request(ALERTS),
        result_for_last_request(POOLS),
    ])
    with patch('web_server.create_connection', return_value=ws):
        status = web_server.TrueNASHandler().get_pool_status()

    assert status == {'alerts': 1, 'backup-disk-0': '3.42 TB', 'backup-disk-1': '0.5 TB'}
    methods = [m['method'] for m in ws.sent if m['msg'] == 'method']
    assert methods == ['auth.login_with_api_key', 'alert.list', 'pool.query']
    assert ws.sent[1]['params'] == ['1-testtoken']
    assert len({m['id'] for m in ws.sent if m['msg'] == 'method'}) == 3, 'each call needs its own id'
    assert ws.closed


def test_pool_status_skips_unrelated_messages(truenas_env: None) -> None:
    ws = FakeWebSocket([
        CONNECTED,
        '',
        json.dumps({'msg': 'pong'}),
        result_for_last_request(True),
        json.dumps({'msg': 'changed', 'collection': 'alert.list', 'fields': {}}),
        json.dumps({'msg': 'result', 'id': 'someone-elses-call', 'result': 'ignored'}),
        result_for_last_request([]),
        result_for_last_request(POOLS[:1]),
    ])
    with patch('web_server.create_connection', return_value=ws):
        status = web_server.TrueNASHandler().get_pool_status()

    assert status == {'alerts': 0, 'backup-disk-0': '3.42 TB'}


def test_pool_status_applies_pool_aliases(truenas_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('TRUENAS_POOL_ALIASES', 'backup-disk-0:disk-0,backup-disk-1:disk-1')
    ws = FakeWebSocket([
        CONNECTED,
        result_for_last_request(True),
        result_for_last_request(ALERTS),
        result_for_last_request(POOLS),
    ])
    with patch('web_server.create_connection', return_value=ws):
        status = web_server.TrueNASHandler().get_pool_status()

    assert status == {'alerts': 1, 'disk-0': '3.42 TB', 'disk-1': '0.5 TB'}


def test_pool_aliases_tolerate_spaces_and_skip_malformed_entries(truenas_env: None,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('TRUENAS_POOL_ALIASES', ' backup-disk-0 : disk-0 , no-colon-here, :empty-name,')
    ws = FakeWebSocket([
        CONNECTED,
        result_for_last_request(True),
        result_for_last_request([]),
        result_for_last_request(POOLS),
    ])
    with patch('web_server.create_connection', return_value=ws):
        status = web_server.TrueNASHandler().get_pool_status()

    assert status == {'alerts': 0, 'disk-0': '3.42 TB', 'backup-disk-1': '0.5 TB'}


def test_pool_status_reports_ddp_error(truenas_env: None) -> None:
    ws = FakeWebSocket([
        CONNECTED,
        lambda s: json.dumps({'msg': 'result', 'id': s.sent[-1]['id'],
                              'error': {'error': 22, 'reason': 'Invalid API key'}}),
    ])
    with patch('web_server.create_connection', return_value=ws):
        status = web_server.TrueNASHandler().get_pool_status()

    assert set(status) == {'error'}
    assert 'Invalid API key' in status['error']
    assert ws.closed


def test_pool_status_reports_connection_failure(truenas_env: None) -> None:
    with patch('web_server.create_connection', side_effect=OSError(113, 'Host is unreachable')):
        status = web_server.TrueNASHandler().get_pool_status()

    assert set(status) == {'error'}
    assert 'Host is unreachable' in status['error']


def test_pool_status_without_token_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('REMOTE_HOST', '100.64.0.9')
    monkeypatch.delenv('REMOTE_API_TOKEN', raising=False)
    with patch('web_server.create_connection') as connect:
        status = web_server.TrueNASHandler().get_pool_status()

    assert set(status) == {'error'}
    connect.assert_not_called()


def pool_status_handler() -> web_server.EnhancedLogHandler:
    """A handler wired to buffers instead of a socket, pointed at the pool status endpoint."""
    with patch.object(web_server.EnhancedLogHandler, '__init__', return_value=None):
        handler = web_server.EnhancedLogHandler()
    handler.path = '/api/pool_status'
    handler.wfile = io.BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    return handler


def test_pool_status_endpoint_is_rsync_only_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('TRUENAS_DISABLED', 'true')
    handler = pool_status_handler()
    with patch('web_server.LOG_FILE', '/nonexistent/sync.log'), \
         patch('web_server.create_connection') as connect:
        handler.do_GET()

    assert json.loads(handler.wfile.getvalue()) == {'sync_errors': 0, 'truenas': 'disabled'}
    connect.assert_not_called()


def test_pool_status_endpoint_merges_truenas_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('TRUENAS_DISABLED', raising=False)
    handler = pool_status_handler()
    with patch('web_server.LOG_FILE', '/nonexistent/sync.log'), \
         patch.object(web_server.TrueNASHandler, 'get_pool_status',
                      return_value={'alerts': 0, 'backup-disk-0': '3.42 TB'}):
        handler.do_GET()

    assert json.loads(handler.wfile.getvalue()) == {'sync_errors': 0, 'alerts': 0, 'backup-disk-0': '3.42 TB'}
