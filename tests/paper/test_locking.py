"""Account lease and fencing tests (Stage 6A Task 2)."""

from __future__ import annotations

import fcntl
import threading
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from tradingagents.paper.exceptions import LeaseConflict, LeaseTimeout, StaleFencingToken
from tradingagents.paper.locking import _lock_path
from tradingagents.paper.repository import PaperRepository
from tests.paper.conftest import EXECUTION_BATCH, seed_execution_orders


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
