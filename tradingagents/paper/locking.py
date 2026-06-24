"""Account lease coordination with OS file locks and DuckDB fencing tokens."""

from __future__ import annotations

import fcntl
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from tradingagents.paper.exceptions import (
    LeaseConflict,
    LeaseExpired,
    LeaseNotHeld,
    LeaseTimeout,
    StaleFencingToken,
)
from tradingagents.paper.migrations import SHANGHAI

DEFAULT_LEASE_SECONDS = 300
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class AccountLease:
    account_id: str
    token: int
    owner_id: str
    owner_pid: int
    acquired_at: datetime
    lease_until: datetime


def _lock_path(home_dir: Path, account_id: str) -> Path:
    lock_dir = home_dir / "data" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{account_id}.lock"


def _ensure_lock_row(connection: duckdb.DuckDBPyConnection, account_id: str) -> None:
    connection.execute(
        """
        INSERT INTO paper_account_locks (account_id, current_fencing_token, updated_at)
        SELECT ?, 0, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM paper_account_locks WHERE account_id = ?
        )
        """,
        [account_id, datetime.now(tz=SHANGHAI), account_id],
    )


def _read_lock_row(
    connection: duckdb.DuckDBPyConnection, account_id: str
) -> tuple[int, str | None, int | None, datetime | None, datetime | None]:
    row = connection.execute(
        """
        SELECT current_fencing_token, owner_id, owner_pid, acquired_at, lease_until
        FROM paper_account_locks
        WHERE account_id = ?
        """,
        [account_id],
    ).fetchone()
    if row is None:
        raise LeaseNotHeld(f"no lock row for account {account_id}")
    token, owner_id, owner_pid, acquired_at, lease_until = row
    return int(token), owner_id, owner_pid, acquired_at, lease_until


class _AccountFileLock:
    def __init__(self, path: Path, timeout_seconds: float):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def __enter__(self) -> _AccountFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise LeaseTimeout(
                        f"timed out acquiring file lock for {self.path.name}"
                    ) from None
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def _grant_account_lease(
    connection: duckdb.DuckDBPyConnection,
    *,
    account_id: str,
    owner_id: str,
    lease_seconds: int,
) -> AccountLease:
    _ensure_lock_row(connection, account_id)
    token, _, _, _, _ = _read_lock_row(connection, account_id)
    now = datetime.now(tz=SHANGHAI)
    new_token = token + 1
    acquired_at = now
    lease_until = now + timedelta(seconds=lease_seconds)
    owner_pid = os.getpid()
    connection.execute(
        """
        UPDATE paper_account_locks
        SET current_fencing_token = ?,
            owner_id = ?,
            owner_pid = ?,
            acquired_at = ?,
            lease_until = ?,
            updated_at = ?
        WHERE account_id = ?
        """,
        [
            new_token,
            owner_id,
            owner_pid,
            acquired_at,
            lease_until,
            now,
            account_id,
        ],
    )
    return AccountLease(
        account_id=account_id,
        token=new_token,
        owner_id=owner_id,
        owner_pid=owner_pid,
        acquired_at=acquired_at,
        lease_until=lease_until,
    )


def acquire_account_lease(
    connection: duckdb.DuckDBPyConnection,
    *,
    home_dir: Path,
    account_id: str,
    owner_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> AccountLease:
    lock_file = _lock_path(home_dir, account_id)
    with _AccountFileLock(lock_file, lock_timeout_seconds):
        _ensure_lock_row(connection, account_id)
        _, owner_id_existing, _, _, lease_until = _read_lock_row(connection, account_id)
        now = datetime.now(tz=SHANGHAI)
        if (
            owner_id_existing is not None
            and lease_until is not None
            and lease_until > now
            and owner_id_existing != owner_id
        ):
            raise LeaseConflict(
                f"account {account_id} leased by {owner_id_existing} until {lease_until}"
            )
        return _grant_account_lease(
            connection,
            account_id=account_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
        )


def take_over_expired_lease(
    connection: duckdb.DuckDBPyConnection,
    *,
    home_dir: Path,
    account_id: str,
    owner_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> AccountLease:
    lock_file = _lock_path(home_dir, account_id)
    with _AccountFileLock(lock_file, lock_timeout_seconds):
        _ensure_lock_row(connection, account_id)
        token, owner_id_existing, owner_pid, acquired_at, lease_until = _read_lock_row(
            connection, account_id
        )
        now = datetime.now(tz=SHANGHAI)
        if owner_id_existing is None or lease_until is None:
            return _grant_account_lease(
                connection,
                account_id=account_id,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )
        if lease_until > now and owner_id_existing == owner_id:
            return AccountLease(
                account_id=account_id,
                token=token,
                owner_id=owner_id,
                owner_pid=owner_pid or os.getpid(),
                acquired_at=acquired_at or now,
                lease_until=lease_until,
            )
        if lease_until > now:
            raise LeaseConflict(
                f"account {account_id} still leased by {owner_id_existing} until {lease_until}"
            )
        return _grant_account_lease(
            connection,
            account_id=account_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
        )


def validate_fencing(
    connection: duckdb.DuckDBPyConnection,
    *,
    account_id: str,
    fencing_token: int,
    owner_id: str,
) -> None:
    token, owner_id_existing, _, _, lease_until = _read_lock_row(connection, account_id)
    now = datetime.now(tz=SHANGHAI)
    if token != fencing_token:
        raise StaleFencingToken(
            f"expected fencing token {fencing_token}, authoritative token is {token}"
        )
    if owner_id_existing != owner_id:
        raise StaleFencingToken(
            f"expected owner {owner_id}, authoritative owner is {owner_id_existing}"
        )
    if lease_until is None or lease_until <= now:
        raise LeaseExpired(f"lease for account {account_id} expired at {lease_until}")
    if owner_id_existing is None:
        raise LeaseNotHeld(f"account {account_id} has no active lease owner")
