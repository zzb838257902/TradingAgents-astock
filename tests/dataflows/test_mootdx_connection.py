"""Tests for bounded mootdx transport reconnection."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tradingagents.dataflows.mootdx_connection import (
    MootdxConnectionManager,
    is_mootdx_transport_error,
    reset_mootdx_manager_for_tests,
)


class _FakeClient:
    def __init__(self, client_id: int) -> None:
        self.client_id = client_id
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_transport_error_classifier():
    assert is_mootdx_transport_error(ConnectionResetError())
    assert is_mootdx_transport_error(TimeoutError())
    assert not is_mootdx_transport_error(ValueError("bad symbol"))


def test_first_call_succeeds_without_reconnect():
    created: list[int] = []

    def connect_fn():
        created.append(1)
        return _FakeClient(1)

    manager = MootdxConnectionManager(connect_fn=connect_fn)
    result = manager.call(lambda client: client.client_id)
    assert result == 1
    assert created == [1]


def test_transport_error_retries_once_then_succeeds():
    created: list[int] = []
    attempts = {"count": 0}

    def connect_fn():
        created.append(len(created) + 1)
        return _FakeClient(len(created))

    def operation(client: _FakeClient) -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionResetError("reset")
        return f"ok-{client.client_id}"

    manager = MootdxConnectionManager(connect_fn=connect_fn)
    assert manager.call(operation) == "ok-2"
    assert created == [1, 2]


def test_consecutive_transport_errors_raise_without_loop():
    created: list[int] = []

    def connect_fn():
        created.append(1)
        return _FakeClient(1)

    manager = MootdxConnectionManager(connect_fn=connect_fn)
    with pytest.raises(ConnectionResetError):
        manager.call(lambda _client: (_raise_reset()))
    assert len(created) == 2


def _raise_reset() -> None:
    raise ConnectionResetError("again")


def test_non_transport_error_does_not_retry():
    created: list[int] = []

    def connect_fn():
        created.append(1)
        return _FakeClient(1)

    manager = MootdxConnectionManager(connect_fn=connect_fn)
    with pytest.raises(ValueError, match="parse"):
        manager.call(lambda _client: (_raise_parse()))
    assert created == [1]


def _raise_parse() -> None:
    raise ValueError("parse failure")


def test_concurrent_calls_share_one_client():
    created: list[int] = []
    barrier = threading.Barrier(2)

    def connect_fn():
        created.append(1)
        return _FakeClient(1)

    manager = MootdxConnectionManager(connect_fn=connect_fn)

    def worker() -> int:
        barrier.wait()
        return manager.call(lambda client: client.client_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results == [1, 1]
    assert created == [1]


def test_close_invalidates_client_before_retry():
    clients: list[_FakeClient] = []

    def connect_fn():
        client = _FakeClient(len(clients) + 1)
        clients.append(client)
        return client

    manager = MootdxConnectionManager(connect_fn=connect_fn)
    attempts = {"count": 0}

    def operation(client: _FakeClient) -> int:
        attempts["count"] += 1
        if attempts["count"] == 1:
            manager.close()
            raise ConnectionResetError("stale socket")
        return client.client_id

    assert manager.call(operation) == 2
    assert clients[0].closed is True


def test_get_mootdx_manager_is_process_singleton():
    reset_mootdx_manager_for_tests()
    from tradingagents.dataflows.mootdx_connection import get_mootdx_manager

    assert get_mootdx_manager() is get_mootdx_manager()
    reset_mootdx_manager_for_tests()
