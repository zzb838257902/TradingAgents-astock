# TradingAgents-Astock 阶段 6A：单机盘后自动运行与模拟组合设计

> 日期：2026-06-22
> 状态：独立审核修订后的最终设计
> 定位：阶段 6 的第一子阶段；不属于现有功能缺陷修复专项

## 1. 目标

在现有真实数据、股票池解析、事件增强选股、组合建议和 Scheduler 基础上，建立个人单机可持续运行的模拟组合闭环：盘后生成目标组合，下一交易日开盘模拟成交，更新账户与持仓，收盘估值并输出可审计报告。

本阶段的成功标准是运行正确、可恢复、可复验，不以模拟收益率高低作为通过条件。

## 2. 开工门禁

阶段 6A 编码前必须关闭现有功能缺陷修复专项的剩余门禁：

1. Tier B mootdx 探测必须具有真正可终止的整体超时；网络不可达返回 `BLOCKED` 和退出码 `2`，不能挂起，也不能误报 `PASS`。
2. `git diff --check 08af6011..HEAD` 必须通过。
3. 缺陷修复专项全量测试、Tier A、专项测试重新通过，并形成最终签字结论。

上述工作是专项收尾，不计入阶段 6A 产品能力。

## 3. 范围

### 3.1 包含

- 单个或多个本地模拟账户；
- 模拟订单、成交、持仓、现金流水和每日净值账本；
- 选股目标权重到调仓订单的确定性转换；
- T 日盘后信号、T+1 开盘快照驱动的模拟成交；
- 停牌、涨跌停、跳空、整手、流动性、佣金和印花税约束；
- 现金分红、送转、拆并股和退市的模拟账户处理；
- 开盘任务与盘后任务的单机调度；
- 步骤级检查点、幂等重跑和崩溃恢复；
- CSV、Markdown 和 JSON 运行报告；
- 离线五日重放、单日真实数据 smoke 和连续五个真实交易日运营观察。

### 3.2 不包含

- 券商接口、真实订单和实盘资金；
- Web 管理页面；
- Redis、Celery、消息队列和分布式运行；
- Top 候选多 Agent 或 LLM 风险复核；
- 短线、波段、中长线等新增多周期策略；
- ML 排序、盘中交易决策和分钟级行情；
- 配股、可转债、融资融券等复杂公司行为的自动处理。

## 4. 总体架构

采用独立 `paper.duckdb` 事务账本，并以只读方式访问现有 `market.duckdb`。复用 `MarketDataRepository`、`run_screen()`、`RunReport`、`JobStateStore` 和 Scheduler 入口，但不直接复用 `BacktestEngine` 的有状态组合循环。

```text
市场数据同步与质量门禁
        ↓
ScreeningService 兼容适配器
        ↓
UniverseResolver + run_screen（现有签名保持不变）
        ↓
FrozenScreenRun（不可变报告快照）
        ↓
RebalancePlanner（只生成计划）
        ↓
paper_orders（T 日盘后）
        ↓
MarketOpenSnapshot（T+1 09:35 前后可见数据）
        ↓
PaperExecutionEngine（T+1 开盘任务）
        ↓
fills + positions + cash_ledger
        ↓
CorporateActionProcessor
        ↓
MarkToMarketService（收盘估值）
        ↓
nav_snapshots + CSV/Markdown/JSON 报告
```

`ScreeningService.run(repo, config, request, signal_time) -> FrozenScreenRun` 负责复用现有 Repository fixture 构建和 `run_screen()` 调用。现有 CLI、Scheduler 和 `run_screen(fixture, config, db_path, ...)` 契约保持兼容。

模拟盘模块不得修改选股因子、排名和目标权重。它只消费已冻结且状态合格的 `FrozenScreenRun`，并记录从建议到实际模拟成交的差异。

### 4.1 数据库与并发边界

- `market.duckdb`：市场数据事实与版本，只读访问；
- `paper.duckdb`：模拟账户、不可变筛选快照、订单、成交、账本、运行状态和报告索引；
- 每次运行开始时，把实际读取的市场行复制为内容寻址、不可变的 `paper_run_inputs`；dataset version 只作为来源审计，历史重放只读该快照；
- 使用账户级跨进程文件锁协调 DuckDB 打开，并由 `paper_account_locks` 保存权威 `owner_id/pid/acquired_at/lease_until/fencing_token`；
- 锁等待超时返回 `BLOCKED`；过期锁只能在确认 owner 不存活或 lease 到期后接管；
- DuckDB 写事务内校验 fencing token，防止旧进程恢复后继续写入。

## 5. 时间语义

- `signal_date`：T 日，盘后形成选股信号。
- `signal_time`：T 日数据实际完整且可用于决策的时间，不强制伪装成 15:30。
- `execution_date`：T 后第一个开市日。
- `execution_time`：T+1 开盘快照的 `observed_at`，首版默认在 09:35 前后采集，不伪装成 09:30 精确成交。
- `valuation_date`：持仓估值所属交易日。
- `available_at`：每条行情、公司行为和财务记录的实际可用时间。
- `run_time`：任务真实执行时间。

禁止使用 T 日收盘信号并按 T 日收盘价成交。模拟成交只能使用 T+1 开盘快照中当时可见的未复权开盘价、昨收、实时状态和截至 `observed_at` 的累计成交量。不得读取 T+1 最终日线的 high、low、close 或全天 volume。

若开盘快照源不可用，开盘执行返回 `BLOCKED`，不得改用盘后完整日线补做“开盘成交”。离线历史重放使用冻结的开盘快照 fixture，而不是从最终日线反推。

历史重放必须固定上述时间，不能因任务在未来重新运行而读取未来数据。

## 6. 数据模型

阶段 6A 新增独立模拟盘表，不把账户状态混入市场数据事实表。

金额、价格和数量使用精确类型：

- 人民币金额：`DECIMAL(20, 4)`；
- 证券价格：`DECIMAL(20, 6)`；
- 权重和收益率：`DECIMAL(18, 10)`；
- 股数：`BIGINT`；
- 金额入账统一按人民币分四舍五入，模式为 `ROUND_HALF_UP`；
- 费用先按成交逐笔计算，再分别舍入，最后汇总，禁止使用二进制浮点写账。

### 6.1 `frozen_screen_runs`

- `screen_run_id`：主键；
- `screen_content_hash`：完整规范化 `RunReport` JSON 的 SHA256；
- `status`、`signal_time`；
- `target_portfolio_mode`：`WEIGHTS/ALL_CASH`；
- `target_weights_json`、`cash_weight`；
- `dataset_versions_json`、`event_dataset_versions_json`；
- `run_report_json`；
- `created_at`；
- `screen_content_hash` 唯一，快照写入后不可更新。

Planner 只能读取 `status=OK` 的快照。`DATA_ERROR` 和 `EMPTY_UNIVERSE` 均保持原仓并返回 `DATA_ERROR`，不得根据默认空权重清仓。只有显式 `target_portfolio_mode=ALL_CASH` 才允许生成清仓计划。`WEIGHTS` 模式必须满足：

```text
abs(sum(target_weights) + cash_weight - 1) <= 1e-8
```

### 6.2 `market_open_snapshots`

该表属于 `market.duckdb` 的版本化市场数据，不属于模拟账户账本：

- `symbol`、`trade_date`、`observed_at`；
- `open_cny`、`prev_close_cny`、`last_cny`；
- `cumulative_volume_shares`；
- `quote_status`：`TRADING/SUSPENDED/HALTED/UNKNOWN`；
- `upper_limit_cny`、`lower_limit_cny`；
- `source`、`available_at`、`dataset_version_id`。

Provider 必须使用当时可见的开盘快照字段。完整日线不得写入本表。执行批次冻结每个标的使用的 snapshot 主键、版本和内容哈希。

### 6.3 `paper_accounts`

- `account_id`：主键；
- `name`；
- `base_currency`：首版固定 `CNY`；
- `initial_cash_cny`；
- `status`：`ACTIVE/FROZEN/CLOSED`；
- `created_at`、`updated_at`。

### 6.4 `paper_account_locks`

- `account_id`：主键；
- `current_fencing_token`：单调递增；
- `owner_id`、`owner_pid`；
- `acquired_at`、`lease_until`、`updated_at`。

取得新 lease 时在事务中递增 fencing token。所有会产生资金、持仓或订单影响的事务必须带期望 token，并先执行条件校验；token 不一致立即回滚并返回 `BLOCKED`。文件锁只用于协调进程进入 DuckDB，数据库表才是 token 的权威来源。

### 6.5 `paper_positions`

- `account_id`、`symbol`：联合主键；
- `quantity`；
- `available_quantity`；
- `average_cost_cny`；
- `last_price_cny`；
- `market_value_cny`；
- `realized_pnl_cny`、`unrealized_pnl_cny`；
- `updated_at`、`version`。

数量不得为负。除公司行为或清仓尾数外，买入数量必须为 100 股整数倍。

`paper_positions` 只是可重建投影，不是账本事实来源。

### 6.6 `paper_lots`

- `lot_id`：主键；
- `account_id`、`symbol`；
- `acquired_date`、`source_type`、`source_id`；
- `original_quantity`、`remaining_quantity`；
- `original_cost_cny`、`remaining_cost_cny`；
- `created_at`、`closed_at`。

卖出按 FIFO 消耗 lot。`available_quantity` 等于 `acquired_date < execution_date` 的未关闭 lot 数量之和；T+1 当日买入不得当日卖出。

### 6.7 `paper_position_ledger`

Append-only 持仓变动账：

- `position_entry_id`：主键；
- `account_id`、`symbol`；
- `quantity_delta`、`cost_delta_cny`；
- `effective_date`；
- `source_type`：`FILL/CORPORATE_ACTION/DELISTING/ADJUSTMENT`；
- `source_id`、`component`；
- `business_key`、`created_at`；
- 唯一键：`account_id + source_type + source_id + component`。

### 6.8 `paper_run_inputs`

保存运行实际消费的不可变输入行：

- `run_id`、`input_type`、`scope_key`：联合主键；
- `row_content_hash`；
- `row_json`：规范化 JSON；
- `source_dataset_version_id`、`source_available_at`；
- `captured_at`。

`input_type` 至少包括 `SECURITY/DAILY_BAR/OPEN_SNAPSHOT/FINANCIAL/EVENT/CORPORATE_ACTION/VALUATION_PRICE`。同一内容可通过 hash 去重，但运行与输入的关联不可更新。现有市场事实表即使被后续 `INSERT OR REPLACE` 覆盖，也不能改变已经冻结的运行输入。

### 6.9 `rebalance_runs`

- `rebalance_run_id`：主键；
- `account_id`；
- `screen_run_id`；
- `screen_content_hash`、`target_hash`；
- `signal_date`、`signal_time`、`execution_date`；
- `universe_hash`、`config_hash`、`strategy_version`；
- `target_weights_json`；
- `logical_run_key`、`revision`、`is_active_revision`；
- `status`：使用 §11 的 `RunStatus`；
- `created_at`、`completed_at`；
- 唯一约束：`logical_run_key + revision`；
- 同一 `logical_run_key` 只能有一个 active revision。

`logical_run_key` 由 `account_id + signal_date + execution_date + universe_hash + config_hash + strategy_version` 计算。普通重跑复用 active revision；只有输入内容哈希变化且显式 `--force-revision` 才能创建下一 revision。已产生资金影响的 revision 不得被替换或再次执行。

### 6.10 `paper_orders`

- `order_id`：主键；
- `rebalance_run_id`、`account_id`、`symbol`；
- `side`：`BUY/SELL`；
- `planned_quantity`、`filled_quantity`、`remaining_quantity`；
- `reference_price_cny`、`limit_price_cny`；
- `status`：`PENDING/FILLED/PARTIALLY_FILLED/REJECTED/EXPIRED/PARTIALLY_FILLED_EXPIRED/CANCELLED`；
- `rejection_code`、`rejection_detail`；
- `created_at`、`updated_at`；
- 唯一约束：`rebalance_run_id + symbol + side`。

### 6.11 `paper_fills`

- `fill_id`：主键；
- `fill_sequence`：首版固定为 `1`，保留未来多次成交扩展；
- `order_id`、`account_id`、`symbol`；
- `execution_date`、`execution_time`；
- `quantity`、`price_cny`；
- `commission_cny`、`stamp_tax_cny`、`other_fee_cny`；
- `source_snapshot_key`、`source_snapshot_version_id`；
- 唯一成交幂等键：`order_id + execution_date + fill_sequence`。

### 6.12 `paper_cash_ledger`

- `cash_entry_id`：主键；
- `account_id`；
- `entry_type`：`DEPOSIT/BUY/SELL/COMMISSION/STAMP_TAX/DIVIDEND/CORPORATE_ACTION/ADJUSTMENT`；
- `amount_cny`：收入为正，支出为负；
- `source_type`、`source_id`、`component`；
- `occurred_at`、`created_at`；
- `balance_after_cny`；
- 唯一键：`account_id + source_type + source_id + component`，防止重复记账并允许同一成交分别记录本金、佣金和印花税。

### 6.13 `paper_nav_snapshots`

- `account_id`、`valuation_date`：联合主键；
- `cash_cny`、`positions_value_cny`、`total_equity_cny`；
- `daily_return`、`cumulative_return`、`drawdown`；
- `valuation_manifest_hash`、`created_at`。

资产恒等式必须成立：

```text
total_equity_cny = cash_cny + positions_value_cny
```

### 6.14 `paper_valuation_sources`

- `account_id`、`valuation_date`、`symbol`：联合主键；
- `quantity`、`price_cny`、`price_status`；
- `source_row_key`、`dataset_version_id`、`row_content_hash`；
- `available_at`。

逐标的估值来源用于重建 NAV，不能只在 NAV 上保存一个全局价格版本。

### 6.15 `paper_corporate_action_applications`

- `account_id`、`corporate_action_id`、`revision`：联合主键；
- `entitlement_quantity`、`entitlement_source_hash`；
- `status`：`PENDING/APPLIED/NEEDS_MANUAL_ACTION/ADJUSTED`；
- `position_entry_id`、`cash_entry_id`；
- `applied_at`、`is_active_revision`；
- 同一 `account_id + corporate_action_id` 只能有一个 active revision。

### 6.16 `paper_run_steps`

- `run_id`、`step_name`：联合主键；
- `status`：使用 §11 的 `StepStatus`；
- `input_hash`、`output_json`、`error_json`；
- `started_at`、`finished_at`。

## 7. 调仓计划

`RebalancePlanner` 接收账户快照、`FrozenScreenRun`、T 日已发布收盘价和组合约束，输出确定性订单计划。它必须先校验 screen 状态、目标模式、权重恒等式、数据版本冻结清单和内容哈希。T 日收盘价只用于估算订单，不能作为成交价；T+1 实际开盘快照由执行引擎重新计算可成交数量。

规则：

1. 目标市值为 `可投资权益 × target_weight`；
2. 先计算全部卖单，再计算买单；
3. 卖出不得超过 `available_quantity`；
4. 清仓卖出允许不足 100 股的尾数；
5. 买入向下取整为 100 股整数倍；
6. 预估资金必须包含佣金，并明确标记为 `ESTIMATED`；
7. `target_portfolio_mode=ALL_CASH` 时生成全现金清仓计划；`target_weights={}` 本身不具有清仓语义；
8. 相同输入必须生成相同订单和稳定哈希；
9. 计划阶段不得修改现金或持仓。

## 8. 模拟成交

`PaperExecutionEngine` 在 T+1 开盘处理 `PENDING` 订单：

1. 卖单优先，买单随后；
2. 使用 `market_open_snapshots.open_cny`，不得查询当天最终日线；
3. `SUSPENDED/HALTED/UNKNOWN` 拒单；买入开盘价达到涨停价或卖出开盘价达到跌停价时采取保守拒单；
4. 成交数量受快照 `cumulative_volume_shares` 和 `max_participation_rate` 限制；
5. 跳空导致资金不足时，按实际开盘价重新缩减买入数量；
6. 使用现有佣金与印花税口径；
7. 零成交订单记录 `REJECTED`；
8. 部分成交先记录 `PARTIALLY_FILLED`，剩余数量当日结束时转为 `PARTIALLY_FILLED_EXPIRED`；
9. 当日未成交订单不跨日自动追单，下一次调仓重新计算。

成交批次在一个 DuckDB 事务中完成：

```text
校验 rebalance_run 和订单状态
→ 写 paper_fills
→ 写/消耗 paper_lots
→ 写 paper_position_ledger
→ 写 paper_cash_ledger
→ 重建 paper_positions 投影
→ 更新订单状态
→ 更新 rebalance_run
→ COMMIT
```

事务失败必须整体回滚。重复执行时由订单状态和业务幂等键阻止重复成交、重复扣款和重复加仓。

账本必须满足并在每次事务提交前验证：

```text
cash = initial_cash + sum(paper_cash_ledger.amount_cny)
position_quantity(symbol) = sum(paper_position_ledger.quantity_delta)
buy_cash_delta = -(fill_notional + commission + other_buy_fees)
sell_cash_delta = +(fill_notional - commission - stamp_tax - other_sell_fees)
available_quantity = sum(open lots where acquired_date < execution_date)
realized_pnl = sell_proceeds - disposed_fifo_cost - sell_fees
```

`paper_positions`、lot 余额和账户现金均可从 append-only 账本重建。故障注入必须证明：fill、持仓账和现金账中的任一步失败都会整体回滚。

### 8.1 费用口径

- 佣金率和印花税率复用现有配置；
- 默认最低佣金为每笔 5 CNY，并允许配置覆盖；
- 买入费用：佣金及配置的其他费用；
- 卖出费用：佣金、印花税及配置的其他费用；
- 每个费用 component 独立按分 `ROUND_HALF_UP`，再计算现金变化；
- 费用规则必须以固定金额示例建立 golden tests；
- `BacktestEngine` 仅作为规则对照，Paper 引擎复用无状态价格限制、费用和数量计算组件，并通过 parity tests 证明相同 fixture 下规则一致。

## 9. 公司行为与估值

公司行为自动入账的前提是市场数据契约包含稳定 `corporate_action_id`、`announcement_at/available_at`、`record_date`、`ex_date`、`pay_date`、比例或每股金额、来源版本和修订关系。Task 1 必须先完成能力审计与契约扩展；不能等到执行模块完成后才判断数据是否可用。

### 9.1 自动处理

- 现金分红：资格按 `record_date` 收盘持仓快照确定，现金在 `pay_date` 入账；
- 送转、拆股、并股：按 `record_date` 资格，在 `ex_date` 开盘前通过持仓账调整数量和单位成本，总成本保持一致；
- 退市：复用现有退市恢复率规则，形成明确现金回收流水；
- 证券更名：只更新展示名称，不改变持仓主键。

每个动作先写入 `paper_corporate_action_applications`，其唯一键阻止重复应用。分数股向下取整到整数股；尾差按数据源明确的现金替代金额入账。若数据源没有尾差金额，状态为 `NEEDS_MANUAL_ACTION`，不得自行估值。

### 9.2 阻断处理

以下情况返回 `DATA_ERROR` 或 `NEEDS_MANUAL_ACTION`，不得静默忽略：

- 配股、可转债、换股吸收合并等未支持行为影响持仓；
- 现金分红缺少 record date 或 pay date；
- 送转拆并股缺少 record date、ex-date 或比例；
- 公司行为在应生效时间之后才首次可用；
- 修订事件会改变已经入账的权益。

迟到或修订动作不得回写历史账本，只能创建显式 `ADJUSTMENT` 持仓或现金流水，并保留原 application revision。

### 9.3 收盘估值

使用当日已发布未复权收盘价。持仓标的缺少当日价格时：

- 有明确停牌记录：允许使用最近有效收盘价，并在报告中标记 `STALE_SUSPENDED_PRICE`；
- 无停牌证明：返回 `DATA_ERROR`，不得使用旧价假绿。

## 10. Scheduler 与恢复

### 10.1 开盘任务

```text
calendar_gate
→ apply_effective_corporate_actions
→ sync_market_open_snapshots
→ load_pending_orders
→ market_open_quality_gate
→ execute_pending_orders
→ persist_execution_report
```

### 10.2 盘后任务

```text
calendar_gate
→ sync_market_data
→ quality_gate
→ reconcile_corporate_actions
→ mark_to_market
→ freeze_dataset_versions
→ ScreeningService.run
→ persist_frozen_screen_run
→ create_rebalance_plan
→ generate_reports
→ finalize_run
```

任务由以下字段唯一标识：

```text
job_type + account_id + trade_date + universe_hash + config_hash + strategy_version
```

每一步写入 `paper_run_steps`。重跑从第一个可重试且未成功步骤继续。`--force-revision` 可以创建新的修订运行，但不能重放已经产生资金影响的成交；成交修订必须使用显式 `ADJUSTMENT` 流水，而不是修改历史记录。

市场数据冻结发生在 screening 开始前。后续步骤只允许读取 `paper_run_inputs` 与 `frozen_screen_runs`；dataset version 和逐标的来源用于审计，不作为能够重建旧行的假设。新发布的修订不能改变正在运行或已经完成的历史结果。

若进程在数据库 `COMMIT` 成功后、步骤状态写为成功前崩溃，恢复逻辑先按业务幂等键检查账本事实，再将步骤修复为成功，不能重新成交。

首版由现有 CLI 配合 cron、systemd timer 或 Windows Task Scheduler 运行，不新增常驻服务。

## 11. 状态与错误语义

### 11.1 `RunStatus`

```text
PENDING → RUNNING
RUNNING → BLOCKED | DATA_ERROR | FAILED | COMPLETED | COMPLETED_WITH_REJECTIONS
BLOCKED → RUNNING
FAILED → RUNNING（仅无资金影响且输入 hash 未变）
```

`COMPLETED`、`COMPLETED_WITH_REJECTIONS` 和已经产生资金影响的运行是终态，不允许原地重开。`DATA_ERROR` 在输入 hash 未变化时也是终态；数据或配置变化后必须创建新的 revision，不能原地恢复。

### 11.2 `StepStatus`

```text
PENDING → RUNNING
RUNNING → SUCCESS | BLOCKED | DATA_ERROR | FAILED
BLOCKED → RUNNING
FAILED → RUNNING（仅该步骤无资金影响且输入 hash 未变）
```

`RUNNING` 超过 lease 且 owner 已失效时才能由更高 fencing token 接管。恢复前必须检查该步骤是否已经写入不可变业务事实。

### 11.3 `OrderStatus`

```text
PENDING → FILLED | PARTIALLY_FILLED | REJECTED | CANCELLED
PARTIALLY_FILLED → FILLED | PARTIALLY_FILLED_EXPIRED
PENDING → EXPIRED
```

- `BLOCKED`：网络、锁或供应商暂不可用，未产生资金影响，可以安全重试；
- `DATA_ERROR`：行情、交易日历、复权、公司行为、screen 状态或版本覆盖不完整，不得成交、清仓或估值；
- `REJECTED`：单只订单违反停牌、涨跌停、资金或流动性规则；
- `FAILED`：程序或数据库事务失败，当前事务整体回滚；
- `COMPLETED`：全部必要步骤完成；
- `COMPLETED_WITH_REJECTIONS`：运行完成，但存在明确拒单或部分成交。

错误报告必须包含步骤、账户、交易日、数据版本、可重试性和稳定错误代码，不只保存异常字符串。

## 12. CLI

新增 `tradingagents-paper`，提供：

```text
paper init       初始化模拟账户
paper plan       从指定 screen run 生成调仓计划
paper execute    执行指定交易日待成交订单
paper close      应用公司行为并完成收盘估值
paper status     只读展示账户、持仓、订单和最近运行
paper report     重新生成报告，不改变账户状态
```

Scheduler CLI 增加：

```text
scheduler run-open
scheduler run-after-close
scheduler recover
```

所有状态变更命令输出 JSON，成功退出码为 `0`，业务阻断为 `2`，程序或数据错误为 `1`。只读命令不得触发同步、成交或修订。

## 13. 报告

每个账户、交易日、运行修订生成独立目录：

```text
reports/paper/<account_id>/<trade_date>/<logical_run_key>/rev-<revision>/
  daily_summary.md
  orders.csv
  fills.csv
  positions.csv
  nav.csv
  run_manifest.json
```

同级 `latest.json` 只保存当前 active revision 的相对路径和 manifest hash，并通过原子替换更新；历史报告不可覆盖。

`daily_summary.md` 至少包含：数据状态、选股排名、目标权重、计划与实际成交差异、拒单、现金、持仓、市值、净值、收益率、回撤、公司行为和降级原因。

`run_manifest.json` 至少包含：代码 commit、配置哈希、策略版本、Universe 哈希、市场数据版本、事件数据版本、screen run、rebalance run、signal time、execution date 和报告内容哈希。

报告生成使用临时文件和原子替换，避免崩溃留下半份报告。

## 14. 验收

### 14.1 中期验收 A：账本

- Schema 原子升级、失败回滚；
- 开盘快照与公司行为数据能力审计；
- 不可变 screen run、账户、lot、订单、成交、持仓账、现金账和净值 Repository；
- 现金、持仓、可用数量、成本和净值恒等式；
- 重复写入幂等；
- 事务失败无部分资金影响。

### 14.2 中期验收 B：交易闭环

- 目标权重转订单；
- T+1 开盘成交；
- 停牌、涨跌停、跳空、流动性和费用；
- 公司行为；
- 缺失价格不假绿；
- 空 target weights 或 screen `DATA_ERROR` 不清仓；
- 重复执行不重复成交。

### 14.3 Tier A：离线五日重放

固定 fixture 连续重放五个交易日，验证：

- 输出完全确定；
- 每日现金、持仓和净值恒等；
- 崩溃恢复与正常运行结果一致；
- 重跑后订单、成交和现金流水数量不增加；
- 不读取未来行情、财务、事件和公司行为；
- 无负现金、负持仓或无法解释的净值跳变。

故障注入至少覆盖：

- fill 写入后、现金或持仓账写入前异常；
- COMMIT 成功但步骤状态尚未写为成功；
- 报告临时文件写入后、原子替换前中断；
- 两个 execute 进程同时处理同一账户；
- T+1 买入后当日尝试卖出；
- 部分成交后重跑；
- screen `DATA_ERROR` 且空权重时保持原仓；
- 连续停牌跨多日估值；
- 公司行为迟到或修订；
- `--force-revision` 不重复资金影响；
- 后续发布的数据修订不改变已冻结历史运行。

### 14.4 Tier B：真实数据单日 smoke

分别验证免费市场数据、选股、计划、模拟成交和报告。网络不可达返回 `BLOCKED/2`，所有必要步骤必须出现在报告中，不能因首个网络失败跳过本地步骤。

### 14.5 Tier C：连续五个真实交易日

每天保存运行 manifest，五日结束后汇总：

- 必要步骤成功率；
- 每步耗时；
- 数据覆盖与降级；
- 订单、成交和拒单数量；
- 资产恒等式；
- 恢复次数和人工干预；
- 未关闭缺陷。

Tier C 可以通过操作系统自动任务逐日积累，不要求单次 AI 会话持续等待五天。

## 15. 任务拆分与审核点

1. Task 0：现有缺陷修复专项收尾门禁；
2. Task 1：开盘快照、公司行为能力审计、模拟盘契约与 Schema；
3. Task 2：Paper Repository、append-only 账本、lot 与账户锁；
4. Task 3：ScreeningService、FrozenScreenRun 与调仓计划生成器；
5. Task 4：T+1 模拟执行引擎；
6. Task 5：公司行为与每日估值；
7. Task 6：双时点 Scheduler；
8. Task 7：CLI 与报告；
9. Task 8：分层验收和连续五日观察工具。

审核点：

- Task 2 后进行中期验收 A；
- Task 5 后进行中期验收 B；
- Task 8 后进行阶段 6A 最终独立审核。

每个 Task 必须先写失败测试，再做最小实现，并独立提交。实现过程中不得顺便修改策略权重、选股逻辑或阶段编号。

内部里程碑分为：

- 6A1：Task 1–4，完成数据契约、账本、计划和 T+1 模拟成交；
- 6A2：Task 5–8，完成公司行为、估值、自动运行和连续运营验收。

该拆分只用于降低交付风险，不改变“阶段 6A”的产品阶段编号。

## 16. 独立审核问题处置

本设计已根据独立子智能体审核修订：

- 关闭开盘任务读取全天日线造成的前视风险，新增版本化开盘快照；
- 禁止用空权重推断清仓，新增显式目标模式和 screen 状态门禁；
- 增加 append-only 持仓账与 lot，冻结 T+1 可卖和成本规则；
- 补齐公司行为 record/ex/pay date、应用记录、迟到和修订语义；
- 新增不可变 screen run、revision 幂等、数据版本冻结和逐标的估值来源；
- 明确独立 `paper.duckdb`、账户锁、fencing token 和状态转移；
- 明确 BacktestEngine 仅提供无状态规则对照；
- 补齐并发、崩溃、部分成交、停牌和修订故障注入。

## 17. 后续边界

阶段 6A 稳定后再分别设计：

- 阶段 6B：Web 运行管理与通知；
- 阶段 7：冻结上下文后的 Top 候选 LLM 风险复核；
- 多周期策略扩展；
- 券商适配和实盘安全体系。

这些能力不在本设计中预留未经使用的复杂抽象，只通过账户、订单、成交和报告契约保持可扩展边界。
