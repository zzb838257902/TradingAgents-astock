"""Thread-safe mootdx Quotes client lifecycle with bounded transport reconnection."""

from __future__ import annotations

import errno
import os
import socket
import threading
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_FALLBACK_MOOTDX_SERVERS: tuple[tuple[str, int], ...] = (
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("110.41.147.114", 7709),
    ("124.70.176.52", 7709),
)

_TRANSPORT_ERRNOS = {
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ETIMEDOUT,
    errno.EPIPE,
    errno.ECONNABORTED,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
}


def is_mootdx_transport_error(exc: BaseException) -> bool:
    """Return True only for explicit network/transport failures."""
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, TimeoutError, EOFError)):
        return True
    if isinstance(exc, OSError) and exc.errno in _TRANSPORT_ERRNOS:
        return True
    if isinstance(exc, socket.timeout):
        return True
    return False


def create_mootdx_quotes_client():
    """Connect to mootdx HQ, falling back when bestip scan is blocked."""
    from mootdx.quotes import Quotes

    budget_sec = float(os.environ.get("MOOTDX_CONNECT_BUDGET_SEC", "30"))
    deadline = time.monotonic() + budget_sec
    per_server_timeout = min(10, max(1, int(budget_sec // 4)))

    skip_bestip = os.environ.get("MOOTDX_SKIP_BESTIP", "").lower() in {"1", "true", "yes"}
    if not skip_bestip and time.monotonic() < deadline:
        try:
            return Quotes.factory(market="std", bestip=True, timeout=per_server_timeout)
        except OSError:
            pass

    servers: list[tuple[str, int]] = list(_FALLBACK_MOOTDX_SERVERS)
    try:
        from mootdx.consts import HQ_HOSTS
        from tdxpy.constants import hq_hosts

        for host in hq_hosts[:12] + HQ_HOSTS[:8]:
            servers.append((host[1], int(host[2])))
    except Exception:
        pass

    seen: set[tuple[str, int]] = set()
    last_error: Exception | None = None
    for server in servers:
        if time.monotonic() >= deadline:
            break
        if server in seen:
            continue
        seen.add(server)
        try:
            return Quotes.factory(
                market="std",
                server=server,
                timeout=per_server_timeout,
            )
        except Exception as exc:
            last_error = exc
    raise OSError(f"unable to connect to mootdx HQ server: {last_error}")


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class MootdxConnectionManager:
    """Manage one shared mootdx client with at most one reconnect retry."""

    def __init__(
        self,
        connect_fn: Callable[[], Any] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._client: Any | None = None
        self._active_calls = 0
        self._clients_to_close: list[Any] = []
        self._connect_fn = connect_fn or create_mootdx_quotes_client

    def connect(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = self._connect_fn()
            return self._client

    def _retire_client_locked(self, client: Any) -> None:
        if self._active_calls > 0:
            self._clients_to_close.append(client)
        else:
            _close_client(client)

    def _finish_call_locked(self) -> None:
        self._active_calls -= 1
        if self._active_calls == 0 and self._clients_to_close:
            pending = self._clients_to_close
            self._clients_to_close = []
        else:
            pending = []
        for client in pending:
            _close_client(client)

    def invalidate(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            if client is not None:
                self._retire_client_locked(client)

    def close(self) -> None:
        self.invalidate()

    def call(self, operation: Callable[[Any], T]) -> T:
        last_error: BaseException | None = None
        for attempt in range(2):
            with self._lock:
                if self._client is None:
                    self._client = self._connect_fn()
                client = self._client
                self._active_calls += 1
            try:
                try:
                    return operation(client)
                except BaseException as exc:
                    if not is_mootdx_transport_error(exc):
                        raise
                    last_error = exc
                    with self._lock:
                        if attempt == 0 and self._client is client:
                            stale = self._client
                            self._client = None
                            self._retire_client_locked(stale)
                    if attempt == 0:
                        continue
                    raise
            finally:
                with self._lock:
                    self._finish_call_locked()
        assert last_error is not None
        raise last_error


_manager: MootdxConnectionManager | None = None
_manager_lock = threading.Lock()


def get_mootdx_manager() -> MootdxConnectionManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = MootdxConnectionManager()
    return _manager


def reset_mootdx_manager_for_tests() -> None:
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.close()
        _manager = None
