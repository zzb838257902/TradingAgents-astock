"""Account lease and fencing tests (Stage 6A Task 2)."""

from __future__ import annotations

import fcntl
import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tradingagents.paper.exceptions import LeaseConflict, LeaseTimeout, StaleFencingToken
from tradingagents.paper.locking import _lock_path
from tradingagents.paper.migrations import SHANGHAI
from tradingagents.paper.repository import PaperRepository
from tests.paper.conftest import EXECUTION_BATCH, make_execution_batch, seed_execution_orders


def test_acquire_account_lease_increments_fencing_token(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    first = repo.acquire_account_lease("demo", owner_id="one")
    second = repo.acquire_account_lease("demo", owner_id="one", lease_seconds=300)
    assert second.token == first.token + 1


def test_concurrent_lease_acquisition_serializes(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    results: list[int] = []
    errors: list[Exception] = []

    def worker() -> None:
        child = PaperRepository(repo.paths)
        try:
            lease = child.acquire_account_lease(
                "demo",
                owner_id="worker",
                lock_timeout_seconds=2.0,
            )
            results.append(lease.token)
            time.sleep(0.1)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            child.close()

    threads = [
        threading.Thread(target=worker),
        threading.Thread(target=worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert sorted(results) == results
    assert results[1] == results[0] + 1


def test_valid_lease_blocks_other_owner(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    repo.acquire_account_lease("demo", owner_id="one", lease_seconds=300)
    with pytest.raises(LeaseConflict):
        repo.acquire_account_lease("demo", owner_id="two", lease_seconds=300)


def test_expired_lease_can_be_taken_over(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    first = repo.acquire_account_lease("demo", owner_id="one", lease_seconds=300)
    repo.expire_lease_for_test("demo")
    second = repo.take_over_expired_lease("demo", owner_id="two", lease_seconds=300)
    assert second.token > first.token
    assert second.owner_id == "two"


def test_first_takeover_on_new_account_succeeds(repo):
    repo.create_account("acct", Decimal("100000.00"))
    lease = repo.take_over_expired_lease("acct", owner_id="owner-1")
    assert lease.token == 1
    assert lease.owner_id == "owner-1"


def test_stale_token_rejected_after_validate_before_commit(repo):
    seed_execution_orders(repo)
    first = repo.acquire_account_lease("demo", owner_id="one")

    def invalidate_after_validate() -> None:
        now = datetime.now(tz=SHANGHAI)
        repo.connection.execute(
            """
            UPDATE paper_account_locks
            SET current_fencing_token = ?,
                owner_id = ?,
                lease_until = ?,
                updated_at = ?
            WHERE account_id = ?
            """,
            [
                first.token + 1,
                "two",
                now + timedelta(seconds=300),
                now,
                "demo",
            ],
        )

    with pytest.raises(StaleFencingToken):
        repo.apply_execution_batch(
            EXECUTION_BATCH,
            fencing_token=first.token,
            fault_injection={"after_validate": invalidate_after_validate},
        )
    fills = repo.connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()
    assert int(fills[0]) == 0


def test_takeover_blocks_while_execution_holds_file_lock(repo):
    seed_execution_orders(repo)
    lease = repo.acquire_account_lease("demo", owner_id="one")
    other = PaperRepository(repo.paths)
    started = threading.Event()
    errors: list[Exception] = []

    def slow_after_validate() -> None:
        started.set()
        time.sleep(0.3)

    def try_takeover() -> None:
        assert started.wait(timeout=5)
        try:
            other.acquire_account_lease(
                "demo",
                owner_id="two",
                lock_timeout_seconds=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=try_takeover)
    thread.start()
    repo.apply_execution_batch(
        make_execution_batch(owner_id="one"),
        fencing_token=lease.token,
        fault_injection={"after_validate": slow_after_validate},
    )
    thread.join(timeout=5)
    other.close()
    assert any(isinstance(exc, LeaseTimeout) for exc in errors)
    assert repo.connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1

def test_stale_token_rejected_inside_transaction(repo):
    seed_execution_orders(repo)
    first = repo.acquire_account_lease("demo", owner_id="one")
    other = PaperRepository(repo.paths)

    def takeover_during_transaction() -> None:
        now = datetime.now(tz=SHANGHAI)
        other.connection.execute(
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
                first.token + 1,
                "two",
                os.getpid(),
                now,
                now + timedelta(seconds=300),
                now,
                "demo",
            ],
        )

    try:
        with pytest.raises(StaleFencingToken):
            repo.apply_execution_batch(
                EXECUTION_BATCH,
                fencing_token=first.token,
                fault_injection={"before_validate": takeover_during_transaction},
            )
        fills = other.connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()
        assert int(fills[0]) == 0
    finally:
        other.close()


def test_stale_token_rejected_on_money_impact(repo):
    seed_execution_orders(repo)
    first = repo.acquire_account_lease("demo", owner_id="one")
    repo.expire_lease_for_test("demo")
    second = repo.take_over_expired_lease("demo", owner_id="two")
    with pytest.raises(StaleFencingToken):
        repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=first.token)
    repo.apply_execution_batch(
        replace(EXECUTION_BATCH, owner_id="two"),
        fencing_token=second.token,
    )


def test_lock_timeout_raises(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    held = threading.Event()
    release = threading.Event()
    lock_file = _lock_path(repo.paths.home_dir, "demo")

    def hold_file_lock() -> None:
        handle = open(lock_file, "a+b")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        held.set()
        release.wait(timeout=5)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    thread = threading.Thread(target=hold_file_lock)
    thread.start()
    assert held.wait(timeout=5)

    waiter = PaperRepository(repo.paths)
    try:
        with pytest.raises(LeaseTimeout):
            waiter.acquire_account_lease(
                "demo",
                owner_id="waiter",
                lock_timeout_seconds=0.2,
            )
    finally:
        release.set()
        thread.join(timeout=5)
        waiter.close()
