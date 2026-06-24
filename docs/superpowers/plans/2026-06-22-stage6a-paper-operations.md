# TradingAgents-Astock Stage 6A Paper Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PIT-safe, idempotent, single-machine paper portfolio that turns T-day after-close screening into T+1 market-open snapshot execution, persistent accounting, daily valuation, recovery, and auditable reports.

**Architecture:** Keep `market_live.duckdb` as versioned market input and create a separate `paper.duckdb` for immutable screen/input snapshots and the transactional paper ledger. Preserve `run_screen()` and existing CLI behavior through a `ScreeningService` adapter; persist all money-impacting facts as append-only cash/position entries and lots, while projections remain rebuildable.

**Tech Stack:** Python 3.10+, DuckDB, Pydantic, Typer, Decimal, pytest, Ruff, existing MarketDataRepository/Scheduler/backtest rule components.

---

## 0. Execution rules

1. Work from an isolated worktree created with `superpowers:using-git-worktrees`.
2. Read the approved design first: `docs/superpowers/specs/2026-06-22-stage6a-paper-operations-design.md`.
3. Do not change stage numbering, ranking factors, strategy weights, `run_screen()` inputs/outputs, Web, real trading, multi-Agent review, or multi-period strategy behavior.
4. For every Task: write a failing test, run it and capture the failure, implement the smallest change, run focused tests, run Ruff on touched files, then commit.
5. Do not add Tushare as a default or fallback. No tokens, DuckDB files, raw snapshots, caches, or generated reports may be committed.
6. Stop after Task 2 and Task 5 for intermediate review. Do not continue until the user confirms.

## Task 0: Close the existing-defects remediation gate

**Files:**

- Modify: `scripts/accept_existing_defect_remediation.py`
- Modify: `tests/remediation/test_remediation_acceptance.py`
- Modify: `docs/defect-remediation-quickstart.md`
- Modify: `docs/superpowers/reports/2026-06-21-defect-remediation-acceptance-a.md`
- Modify: `docs/superpowers/reports/2026-06-21-defect-remediation-acceptance-b.md`
- Modify: `docs/superpowers/reports/2026-06-21-defect-remediation-final.md`

- [ ] **Step 1: Add a failing hard-timeout test**

```python
def test_mootdx_probe_timeout_returns_without_waiting_for_worker(monkeypatch):
    import time
    from scripts.accept_existing_defect_remediation import run_mootdx_probe_subprocess

    started = time.perf_counter()
    result = run_mootdx_probe_subprocess(
        timeout_sec=0.1,
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
    )
    assert time.perf_counter() - started < 1.0
    assert result["status"] == "BLOCKED"
```

- [ ] **Step 2: Verify the test fails because ThreadPoolExecutor waits for shutdown**

Run:

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest \
  tests/remediation/test_remediation_acceptance.py::test_mootdx_probe_timeout_returns_without_waiting_for_worker -q
```

Expected: FAIL; elapsed time exceeds the asserted bound or the function is missing.

- [ ] **Step 3: Replace the thread timeout with a killable subprocess**

Implement this contract in `scripts/accept_existing_defect_remediation.py`:

```python
def run_mootdx_probe_subprocess(
    *, timeout_sec: float, command: list[str] | None = None
) -> dict[str, object]:
    probe_command = command or [
        sys.executable,
        "-c",
        (
            "from tradingagents.dataflows.mootdx_connection import get_mootdx_manager;"
            "f=get_mootdx_manager().call(lambda c:c.stocks(market=0));"
            "print(len(f) if f is not None else 0)"
        ),
    ]
    try:
        completed = subprocess.run(
            probe_command,
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return {"status": "BLOCKED", "reason": f"timed out after {timeout_sec}s"}
    if completed.returncode != 0:
        return {"status": "BLOCKED", "reason": completed.stderr.strip()}
    return {"status": "OK", "row_count": int(completed.stdout.strip())}
```

Map `BLOCKED` to `AssertionError("network blocked: ...")` inside the live step. Remove `_run_with_timeout()`.

- [ ] **Step 4: Remove all diff-check whitespace and update the final report**

Run:

```bash
git diff --check 08af6011..HEAD
```

Expected: no output, exit `0`.

- [ ] **Step 5: Run the remediation gates**

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 scripts/accept_existing_defect_remediation.py --offline
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 scripts/accept_existing_defect_remediation.py --live-smoke
```

Expected: full tests pass; Tier A `PASS/0`; Tier B `PASS/0` or external network `BLOCKED/2` within its configured deadline.

- [ ] **Step 6: Commit**

```bash
git add scripts/accept_existing_defect_remediation.py tests/remediation/test_remediation_acceptance.py \
  docs/defect-remediation-quickstart.md docs/superpowers/reports/2026-06-21-defect-remediation-*.md
git commit -m "fix(remediation): enforce killable live smoke timeout"
```

## Task 1: Open-snapshot, corporate-action, and paper schema contracts

**Files:**

- Create: `tradingagents/paper/__init__.py`
- Create: `tradingagents/paper/contracts.py`
- Create: `tradingagents/paper/config.py`
- Create: `tradingagents/paper/migrations.py`
- Modify: `tradingagents/market_data/contracts.py`
- Modify: `tradingagents/market_data/migrations.py`
- Modify: `tradingagents/market_data/providers/base.py`
- Modify: `tradingagents/market_data/providers/free_astock.py`
- Modify: `tradingagents/market_data/providers/free_astock_sources.py`
- Modify: `tradingagents/market_data/repository.py`
- Modify: `tradingagents/market_data/quality.py`
- Modify: `tradingagents/market_data/sync.py`
- Modify: `tradingagents/market_data/cli.py`
- Test: `tests/paper/test_contracts.py`
- Test: `tests/paper/test_migrations.py`
- Test: `tests/market_data/test_open_snapshots.py`
- Test: `tests/market_data/test_corporate_action_contract.py`

- [ ] **Step 1: Write failing contract tests**

```python
def test_open_snapshot_contains_only_observed_fields():
    row = MarketOpenSnapshot(
        symbol="600000",
        trade_date=date(2026, 6, 23),
        observed_at=datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI),
        open_cny=10.12,
        prev_close_cny=10.00,
        last_cny=10.15,
        cumulative_volume_shares=1_200_000,
        quote_status=QuoteStatus.TRADING,
        upper_limit_cny=11.00,
        lower_limit_cny=9.00,
        source="fixture",
        available_at=datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI),
    )
    assert not hasattr(row, "close")
    assert row.cumulative_volume_shares == 1_200_000


def test_paper_money_uses_decimal():
    account = PaperAccount(
        account_id="demo",
        name="Demo",
        initial_cash_cny=Decimal("1000000.00"),
    )
    assert isinstance(account.initial_cash_cny, Decimal)
```

- [ ] **Step 2: Define exact contracts and enums**

`tradingagents/paper/contracts.py` must define:

```python
class TargetPortfolioMode(StrEnum):
    WEIGHTS = "weights"
    ALL_CASH = "all_cash"

class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    DATA_ERROR = "data_error"
    FAILED = "failed"
    COMPLETED = "completed"
    COMPLETED_WITH_REJECTIONS = "completed_with_rejections"

class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    BLOCKED = "blocked"
    DATA_ERROR = "data_error"
    FAILED = "failed"

MONEY_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.000001")

def money(value: Decimal | str | int) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
```

Also define immutable Pydantic models for `PaperAccount`, `FrozenScreenRun`, `PaperOrder`, `PaperFill`, `CashEntry`, `PositionEntry`, `PaperLot`, `NavSnapshot`, and `RunStep` using names and fields from the approved design.

- [ ] **Step 3: Add market contracts**

`tradingagents/market_data/contracts.py`:

```python
class QuoteStatus(StrEnum):
    TRADING = "trading"
    SUSPENDED = "suspended"
    HALTED = "halted"
    UNKNOWN = "unknown"

class MarketOpenSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    trade_date: date
    observed_at: datetime
    open_cny: float
    prev_close_cny: float
    last_cny: float
    cumulative_volume_shares: int
    quote_status: QuoteStatus
    upper_limit_cny: float
    lower_limit_cny: float
    source: str
    available_at: datetime
    dataset_version_id: str | None = None
```

Extend the corporate-action normalized record with `corporate_action_id`, `announcement_at`, `record_date`, `pay_date`, `source_version`, and `supersedes_action_id`. Missing record/pay dates must remain `None`; do not infer them.

- [ ] **Step 4: Add atomic market migration and a separate paper migration runner**

Increment market schema from 11 to 12 for `market_open_snapshots` plus expanded corporate-action columns. Create `tradingagents/paper/migrations.py` with its own `paper_schema_migrations`. Version 1 must create these exact tables and constraints from design §6:

```text
frozen_screen_runs
paper_accounts
paper_account_locks
paper_positions
paper_lots
paper_position_ledger
paper_run_inputs
rebalance_runs
paper_orders
paper_fills
paper_cash_ledger
paper_nav_snapshots
paper_valuation_sources
paper_corporate_action_applications
paper_run_steps
```

`paper_orders.status` must allow `PENDING`, `FILLED`, `PARTIALLY_FILLED`, `REJECTED`, `EXPIRED`, `PARTIALLY_FILLED_EXPIRED`, and `CANCELLED`. `paper_corporate_action_applications` uses `(account_id, corporate_action_id, revision)` as its primary key and permits only one active revision per account/action pair.

Paper migrations must execute each version as:

```python
connection.execute("BEGIN")
try:
    connection.execute(sql)
    connection.execute(
        "INSERT INTO paper_schema_migrations VALUES (?, ?)",
        [version, datetime.now(tz=SHANGHAI)],
    )
    connection.execute("COMMIT")
except Exception:
    connection.execute("ROLLBACK")
    raise
```

- [ ] **Step 5: Add the free opening-snapshot provider contract**

Add `get_market_open_snapshots(symbols, trade_date, observed_at)` to provider protocol and fixture/free providers. The free provider must reject historical live requests with `NOT_AVAILABLE_YET`; it may use Tencent current quote fields only and must not query daily K-line close/high/low.

- [ ] **Step 6: Add versioned publish and PIT reads**

Add staging upsert, quality validation, atomic publish, and Repository reads for `market_open_snapshots`. Quality must require requested symbols, one row per symbol/source/observed time, Shanghai-aware timestamps, non-negative cumulative volume, positive prices, and `available_at <= requested cutoff`. Network, parse, partial, or empty responses must not publish a false empty version.

Expose:

```python
MarketDataSync.sync_market_open_snapshots(symbols, trade_date, observed_at)
MarketDataRepository.get_market_open_snapshots(
    symbols, trade_date, available_before, version_id=None
)
```

Add CLI dataset name `market-open-snapshots`; historical free requests return structured `BLOCKED` and publish nothing.

- [ ] **Step 7: Run focused verification**

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest \
  tests/paper/test_contracts.py tests/paper/test_migrations.py \
  tests/market_data/test_open_snapshots.py \
  tests/market_data/test_corporate_action_contract.py -q
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents/paper \
  tradingagents/market_data/contracts.py tradingagents/market_data/migrations.py \
  tradingagents/market_data/repository.py tradingagents/market_data/quality.py \
  tradingagents/market_data/sync.py tests/paper tests/market_data/test_open_snapshots.py
```

Expected: all focused tests and Ruff pass.

- [ ] **Step 8: Commit**

```bash
git add tradingagents/paper tradingagents/market_data tests/paper \
  tests/market_data/test_open_snapshots.py tests/market_data/test_corporate_action_contract.py
git commit -m "feat(paper): add opening snapshot and ledger schema contracts"
```

## Task 2: Paper repository, immutable inputs, lots, and fencing lock

**Files:**

- Create: `tradingagents/paper/repository.py`
- Create: `tradingagents/paper/locking.py`
- Create: `tradingagents/paper/invariants.py`
- Test: `tests/paper/test_repository.py`
- Test: `tests/paper/test_locking.py`
- Test: `tests/paper/test_invariants.py`

- [ ] **Step 1: Write failing repository and invariant tests**

```python
def test_cash_and_position_projection_rebuild_from_ledgers(repo):
    repo.create_account("demo", Decimal("1000000.00"))
    repo.append_cash_entry(CashEntry(
        account_id="demo", entry_type=CashEntryType.DEPOSIT,
        amount_cny=Decimal("1000000.00"), source_type="ACCOUNT",
        source_id="demo", component="INITIAL_CASH", occurred_at=SIGNAL_TIME,
    ))
    repo.append_position_entry(PositionEntry(
        account_id="demo", symbol="600000", quantity_delta=1000,
        cost_delta_cny=Decimal("10000.00"), effective_date=TRADE_DATE,
        source_type="ADJUSTMENT", source_id="seed", component="QUANTITY",
    ))
    rebuilt = repo.rebuild_account_projection("demo")
    assert rebuilt.cash_cny == Decimal("1000000.00")
    assert rebuilt.positions["600000"].quantity == 1000


def test_stale_fencing_token_cannot_commit(repo):
    first = repo.acquire_account_lease("demo", owner_id="one")
    second = repo.take_over_expired_lease("demo", owner_id="two")
    with pytest.raises(StaleFencingToken):
        repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=first.token)
    repo.apply_execution_batch(EXECUTION_BATCH, fencing_token=second.token)
```

- [ ] **Step 2: Implement `PaperPaths` and repository lifecycle**

`PaperPaths.paper_db_path` must be `<home_dir>/data/paper.duckdb`; market input remains `<home_dir>/data/market_live.duckdb`. Repository initialization applies paper migrations only.

Provide these methods with explicit transactions:

```python
create_account(...)
freeze_screen_run(...)
capture_run_inputs(...)
create_rebalance_revision(...)
insert_orders(...)
apply_execution_batch(...)
apply_corporate_action(...)
write_valuation(...)
load_account_snapshot(...)
rebuild_account_projection(...)
```

All append-only inserts use the design business keys and treat exact duplicate payloads as idempotent; a duplicate key with different content raises `IdempotencyConflict`.

- [ ] **Step 3: Implement authoritative leases and fencing**

Use a short OS file lock to serialize DuckDB lease acquisition. Inside DuckDB, atomically increment `current_fencing_token`. Every money-impacting transaction starts with:

```sql
SELECT current_fencing_token, owner_id, lease_until
FROM paper_account_locks
WHERE account_id = ?
```

Abort unless token and owner match and lease is unexpired. Add tests for timeout, expired takeover, stale process, and two concurrent execute processes.

- [ ] **Step 4: Implement invariant checks**

`assert_account_invariants()` must verify cash sum, position ledger sum, lot quantity/cost, non-negative balances, T+1 available quantity, projection equality, and NAV equality. Use `Decimal` exclusively.

- [ ] **Step 5: Add transaction fault injection tests**

Inject exceptions after fill insert, after cash insert, and before projection update. After each failure assert all table counts and balances equal the pre-transaction snapshot.

- [ ] **Step 6: Verify and commit**

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/paper/test_repository.py \
  tests/paper/test_locking.py tests/paper/test_invariants.py -q
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents/paper tests/paper
git add tradingagents/paper tests/paper
git commit -m "feat(paper): add transactional repository and account fencing"
```

### Intermediate acceptance A — mandatory pause

Submit schema tables, transaction rollback evidence, fencing race results, invariant output, tests, Ruff, and commits. Wait for user confirmation.

## Task 3: Frozen screening service and deterministic rebalance planner

**Files:**

- Create: `tradingagents/paper/screening.py`
- Create: `tradingagents/paper/planner.py`
- Modify: `tradingagents/scheduler/jobs.py`
- Test: `tests/paper/test_screening.py`
- Test: `tests/paper/test_planner.py`

- [ ] **Step 1: Write failure tests for screen status and explicit cash mode**

```python
def test_data_error_with_empty_weights_never_creates_liquidation_orders(repo):
    frozen = repo.freeze_screen_run(
        RunReport(
            run_id="bad", status=ScreeningStatus.DATA_ERROR,
            signal_time=SIGNAL_TIME, data_as_of=SIGNAL_TIME,
            target_weights={}, cash_weight=1.0,
        ),
        target_mode=TargetPortfolioMode.WEIGHTS,
        captured_inputs=[],
    )
    with pytest.raises(InvalidScreenRun):
        RebalancePlanner(repo).plan("demo", frozen.screen_run_id)
    assert repo.list_orders("demo") == []


def test_all_cash_requires_explicit_mode(repo):
    frozen = repo.freeze_screen_run(
        RunReport(
            run_id="cash", status=ScreeningStatus.OK,
            signal_time=SIGNAL_TIME, data_as_of=SIGNAL_TIME,
            target_weights={}, cash_weight=1.0,
        ),
        target_mode=TargetPortfolioMode.ALL_CASH,
        captured_inputs=[],
    )
    plan = RebalancePlanner(repo).plan("demo", frozen.screen_run_id)
    assert all(order.side == OrderSide.SELL for order in plan.orders)
```

- [ ] **Step 2: Implement `ScreeningService` without changing `run_screen()`**

```python
class ScreeningService:
    def run(
        self,
        repo: MarketDataRepository,
        config: ScreenerConfig,
        request: UniverseRequest,
        signal_time: datetime,
    ) -> FrozenScreenRun:
        trade_date, _, errors = resolve_signal_trade_date(...)
        if errors:
            raise ScreeningInputError(errors)
        report = run_repository_screen(...)
        return self.paper_repo.freeze_screen_run(report, captured_inputs=...)
```

Capture every consumed market row into `paper_run_inputs` before returning the frozen run. Preserve existing Scheduler output and fixture hashes.

- [ ] **Step 3: Implement deterministic planner**

Require `status=OK`, validate target-mode and weight sum, use T close only as `ESTIMATED` reference, sell before buy, cap sells by sellable lots, round buys down to 100 shares, and produce `target_hash` plus stable order IDs.

- [ ] **Step 4: Test revision semantics**

Same input reuses active revision. Changed `screen_content_hash` is rejected unless `--force-revision`; a revision with fills cannot be re-executed.

- [ ] **Step 5: Verify and commit**

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/paper/test_screening.py tests/paper/test_planner.py \
  tests/scheduler/test_jobs.py tests/remediation/test_baseline.py -q
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents/paper \
  tradingagents/scheduler/jobs.py tests/paper
git add tradingagents/paper tradingagents/scheduler/jobs.py tests/paper
git commit -m "feat(paper): freeze screening inputs and create rebalance plans"
```

## Task 4: T+1 opening-snapshot execution engine

**Files:**

- Create: `tradingagents/paper/execution.py`
- Create: `tradingagents/paper/fees.py`
- Modify: `tradingagents/backtest/execution.py`
- Test: `tests/paper/test_execution.py`
- Test: `tests/paper/test_execution_parity.py`

- [ ] **Step 1: Write execution failure tests**

```python
@pytest.mark.parametrize("status", ["suspended", "halted", "unknown"])
def test_non_trading_snapshot_rejects_order(status):
    result = engine.execute(order, snapshot(quote_status=status), account)
    assert result.order_status == OrderStatus.REJECTED


def test_buy_is_resized_after_gap_up_and_never_makes_cash_negative():
    result = engine.execute(buy_order(10_000), snapshot(open_cny="25.00"), account_cash="100000")
    assert result.fill.quantity % 100 == 0
    assert result.cash_after >= Decimal("0.00")
```

- [ ] **Step 2: Extract stateless shared rules**

Extract lot rounding, conservative open-limit rejection, participation cap, and fee arithmetic into stateless functions. Do not make Paper call `BacktestEngine.run()`.

- [ ] **Step 3: Implement exact fees**

```python
def calculate_fees(notional: Decimal, side: OrderSide, config: FeeConfig) -> FeeBreakdown:
    commission = max(
        money(notional * config.commission_rate),
        config.minimum_commission_cny,
    )
    stamp_tax = money(notional * config.stamp_tax_rate) if side == OrderSide.SELL else Decimal("0.00")
    return FeeBreakdown(commission=commission, stamp_tax=stamp_tax)
```

- [ ] **Step 4: Execute one atomic batch**

Read only frozen `OPEN_SNAPSHOT` inputs. Process sells before buys, write one fill sequence per order, consume FIFO lots, append cash/position entries, rebuild projections, mark unfilled remainder, verify invariants, and commit with fencing token.

- [ ] **Step 5: Add parity and replay tests**

For a fixture compatible with both engines, assert same side, rounded shares, open price, limit rejection, and participation cap. Re-running the same execution adds no rows.

- [ ] **Step 6: Verify and commit**

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/paper/test_execution.py \
  tests/paper/test_execution_parity.py tests/screener/test_execution.py \
  tests/screener/test_backtest_engine.py tests/screener/test_backtest_sizing.py -q
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents/paper \
  tradingagents/backtest/execution.py tests/paper
git add tradingagents/paper tradingagents/backtest/execution.py tests/paper
git commit -m "feat(paper): execute T+1 orders from opening snapshots"
```

## Task 5: Corporate actions and close valuation

**Files:**

- Create: `tradingagents/paper/corporate_actions.py`
- Create: `tradingagents/paper/valuation.py`
- Test: `tests/paper/test_corporate_actions.py`
- Test: `tests/paper/test_valuation.py`

- [ ] **Step 1: Write entitlement and missing-contract tests**

```python
def test_dividend_uses_record_date_and_posts_on_pay_date(repo):
    application = processor.apply(dividend(record_date="2026-06-22", pay_date="2026-06-25"))
    assert application.entitlement_quantity == 1000
    assert repo.cash_on(date(2026, 6, 24)) == before
    assert repo.cash_on(date(2026, 6, 25)) == before + Decimal("120.00")


def test_dividend_without_pay_date_needs_manual_action(repo):
    result = processor.apply(dividend(pay_date=None))
    assert result.status == CorporateActionStatus.NEEDS_MANUAL_ACTION
    assert repo.cash_entries_for(result.action_id) == []
```

- [ ] **Step 2: Implement idempotent application revisions**

Determine entitlement from frozen record-date lots, apply split/bonus before ex-date opening execution, post dividends on pay date, round fractional shares down, require provider cash-in-lieu for tails, and write adjustments for late/revised actions without altering old entries.

- [ ] **Step 3: Implement valuation**

Freeze every valuation row in `paper_run_inputs` and `paper_valuation_sources`. Missing price without suspension proof is `DATA_ERROR`; an explicitly suspended holding may use last published close with `STALE_SUSPENDED_PRICE`.

- [ ] **Step 4: Verify NAV and drawdown**

Use Decimal and verify `equity = cash + positions`. Add five-day golden examples for daily return, cumulative return, and drawdown.

- [ ] **Step 5: Verify and commit**

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/paper/test_corporate_actions.py \
  tests/paper/test_valuation.py -q
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents/paper tests/paper
git add tradingagents/paper tests/paper
git commit -m "feat(paper): apply corporate actions and daily valuation"
```

### Intermediate acceptance B — mandatory pause

Submit planner output, execution/fee golden examples, corporate-action applications, NAV invariants, missing-price rejection, replay evidence, tests, Ruff, and commits. Wait for user confirmation.

## Task 6: Dual-time scheduler, recovery, and run state

**Files:**

- Create: `tradingagents/paper/jobs.py`
- Create: `tradingagents/paper/recovery.py`
- Modify: `tradingagents/scheduler/cli.py`
- Modify: `tradingagents/scheduler/jobs.py`
- Test: `tests/paper/test_jobs.py`
- Test: `tests/paper/test_recovery.py`

- [ ] **Step 1: Write step-order and crash-recovery tests**

```python
def test_open_job_steps_are_complete_and_ordered():
    result = run_open_job(...)
    assert result.step_names == [
        "calendar_gate", "apply_effective_corporate_actions",
        "sync_market_open_snapshots", "load_pending_orders",
        "market_open_quality_gate", "execute_pending_orders",
        "persist_execution_report",
    ]


def test_commit_success_before_step_success_does_not_refill(repo):
    inject_crash_after_commit(repo)
    recover(account_id="demo", trade_date=TRADE_DATE)
    assert repo.count_fills() == 1
```

- [ ] **Step 2: Implement opening and after-close orchestrators**

Each step writes `paper_run_steps` with input hash and fencing token. Run all local steps even if an independent network step is blocked, then compute aggregate status without hiding failures.

- [ ] **Step 3: Implement recovery transitions**

Allow `BLOCKED -> RUNNING` and no-money-impact `FAILED -> RUNNING` with unchanged input. `DATA_ERROR` requires a new revision after changed inputs. Reconcile committed business keys before rerunning a stale `RUNNING` step.

- [ ] **Step 4: Extend scheduler CLI**

Add `run-open`, `run-after-close`, and `recover` commands. JSON output exit codes: success `0`, blocked `2`, data/program error `1`.

- [ ] **Step 5: Verify and commit**

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/paper/test_jobs.py \
  tests/paper/test_recovery.py tests/scheduler -q
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents/paper \
  tradingagents/scheduler tests/paper
git add tradingagents/paper tradingagents/scheduler tests/paper
git commit -m "feat(scheduler): orchestrate and recover paper operations"
```

## Task 7: Paper CLI and atomic reports

**Files:**

- Create: `tradingagents/paper/cli.py`
- Create: `tradingagents/paper/reporting.py`
- Modify: `pyproject.toml`
- Test: `tests/paper/test_cli.py`
- Test: `tests/paper/test_reporting.py`

- [ ] **Step 1: Write CLI no-side-effect and report tests**

```python
def test_status_is_read_only(runner, seeded_home):
    before = database_hash(seeded_home)
    result = runner.invoke(app, ["status", "--account-id", "demo", "--home-dir", str(seeded_home)])
    assert result.exit_code == 0
    assert database_hash(seeded_home) == before


def test_revision_reports_never_overwrite(tmp_path):
    first = reporter.write(run(revision=1))
    second = reporter.write(run(revision=2))
    assert first != second
    assert json.loads((second.parents[1] / "latest.json").read_text())["revision"] == 2
```

- [ ] **Step 2: Add `tradingagents-paper` entry point**

```toml
tradingagents-paper = "tradingagents.paper.cli:app"
```

Commands: `init`, `plan`, `execute`, `close`, `status`, `report`. Mutating commands require account ID and explicit date/run; `status` and `report` never sync or trade.

- [ ] **Step 3: Implement atomic reports**

Write `daily_summary.md`, `orders.csv`, `fills.csv`, `positions.csv`, `nav.csv`, and `run_manifest.json` to a temporary revision directory; fsync where supported and rename atomically. Update `latest.json` last via atomic replacement.

- [ ] **Step 4: Verify and commit**

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/paper/test_cli.py tests/paper/test_reporting.py -q
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents/paper tests/paper
git add tradingagents/paper tests/paper pyproject.toml
git commit -m "feat(paper): add CLI and auditable daily reports"
```

## Task 8: Layered acceptance and five-day operations evidence

**Files:**

- Create: `scripts/accept_stage6a_paper.py`
- Create: `scripts/summarize_stage6a_observation.py`
- Create: `tests/fixtures/paper/five_day_market.json`
- Create: `tests/paper/test_five_day_replay.py`
- Create: `tests/paper/test_acceptance.py`
- Create: `docs/stage6a-paper-quickstart.md`
- Create: `docs/superpowers/reports/2026-06-22-stage6a-final-acceptance.md`

- [ ] **Step 1: Build the deterministic five-day fixture**

Include opening snapshots, close prices, one suspension, one limit rejection, one partial fill, one dividend with complete dates, one missing-price negative case, and no future-visible records.

- [ ] **Step 2: Write five-day replay tests**

Run five days twice into fresh databases and assert identical manifests, orders, fills, cash ledgers, position ledgers, NAV, and hashes. Repeat after injected crashes and assert the same result.

- [ ] **Step 3: Implement Tier A acceptance**

`scripts/accept_stage6a_paper.py --offline` must not invoke pytest. It emits JSON and verifies schema, immutable inputs, plan, T+1 execution, corporate action, valuation, recovery, report hashes, and five-day replay. Exit `0` only on complete PASS.

- [ ] **Step 4: Implement Tier B live smoke**

Run independent steps for free opening snapshot, repository screen, plan, execution, valuation, and report. Network failure is `BLOCKED/2`; local ledger or invariant failure is `FAIL/1`. Every required step must appear even if an earlier network step is blocked. External probes use killable subprocess deadlines.

- [ ] **Step 5: Implement Tier C evidence collector**

Each real trading day writes one signed manifest. The summarizer requires five distinct open dates and reports step success, durations, coverage, orders/fills/rejections, invariant checks, recovery count, manual intervention, and open defects. It does not claim PASS before five dates exist.

- [ ] **Step 6: Run final verification**

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts
PYTHONPATH='.pip_packages:.' python3 scripts/accept_stage6a_paper.py --offline
PYTHONPATH='.pip_packages:.' python3 scripts/accept_stage6a_paper.py --live-smoke
git diff --check
git status --short
```

Expected: full tests pass; touched files Ruff-clean; Tier A PASS; Tier B PASS or external BLOCKED; no generated artifacts staged.

- [ ] **Step 7: Independent review and commit**

Review the complete Stage 6A diff for new P0/P1, PIT leakage, stale-price false success, duplicate money impact, revision overwrite, and scope drift. Fix all confirmed P0/P1 before commit.

```bash
git add scripts/accept_stage6a_paper.py scripts/summarize_stage6a_observation.py \
  tests/fixtures/paper tests/paper docs/stage6a-paper-quickstart.md \
  docs/superpowers/reports/2026-06-22-stage6a-final-acceptance.md
git commit -m "test(paper): add layered Stage 6A acceptance"
```

## Final completion criteria

Stage 6A code completion may be declared only when Tasks 0–8 are committed, both intermediate reviews are confirmed, all tests pass, Tier A passes, Tier B passes or is externally blocked with all steps reported, and independent review finds no unresolved P0/P1.

Operational completion is a separate claim and additionally requires Tier C evidence for five distinct real trading days with no unexplained accounting imbalance, duplicate fill, negative cash/position, future-data use, or silent stale-price fallback.
