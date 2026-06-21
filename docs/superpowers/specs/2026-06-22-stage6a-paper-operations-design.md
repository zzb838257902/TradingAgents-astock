# TradingAgents-Astock 阶段 6A：单机盘后自动运行与模拟组合设计

> 日期：2026-06-22
> 状态：设计已确认，等待文档审阅
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
- T 日盘后信号、T+1 开盘模拟成交；
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

采用 DuckDB 事务账本，并复用现有 `MarketDataRepository`、`run_screen()`、`RunReport`、`JobStateStore` 和 Scheduler 入口。

```text
市场数据同步与质量门禁
        ↓
UniverseResolver + run_screen
        ↓
目标权重与选股审计报告
        ↓
RebalancePlanner（只生成计划）
        ↓
paper_orders（T 日盘后）
        ↓
PaperExecutionEngine（T+1 开盘）
        ↓
fills + positions + cash_ledger
        ↓
CorporateActionProcessor
        ↓
MarkToMarketService（收盘估值）
        ↓
nav_snapshots + CSV/Markdown/JSON 报告
```

模拟盘模块不得修改选股因子、排名和目标权重。它只消费已冻结的 `RunReport.target_weights`，并记录从建议到实际模拟成交的差异。

## 5. 时间语义

- `signal_date`：T 日，盘后形成选股信号。
- `signal_time`：T 日数据实际完整且可用于决策的时间，不强制伪装成 15:30。
- `execution_date`：T 后第一个开市日。
- `execution_time`：`execution_date` 开盘时点。
- `valuation_date`：持仓估值所属交易日。
- `available_at`：每条行情、公司行为和财务记录的实际可用时间。
- `run_time`：任务真实执行时间。

禁止使用 T 日收盘信号并按 T 日收盘价成交。模拟成交只能使用 T+1 未复权开盘价及当时可见的交易约束。

历史重放必须固定上述时间，不能因任务在未来重新运行而读取未来数据。

## 6. 数据模型

阶段 6A 新增独立模拟盘表，不把账户状态混入市场数据事实表。

### 6.1 `paper_accounts`

- `account_id`：主键；
- `name`；
- `base_currency`：首版固定 `CNY`；
- `initial_cash_cny`；
- `status`：`ACTIVE/FROZEN/CLOSED`；
- `created_at`、`updated_at`。

### 6.2 `paper_positions`

- `account_id`、`symbol`：联合主键；
- `quantity`；
- `available_quantity`；
- `average_cost_cny`；
- `last_price_cny`；
- `market_value_cny`；
- `realized_pnl_cny`、`unrealized_pnl_cny`；
- `updated_at`、`version`。

数量不得为负。除公司行为或清仓尾数外，买入数量必须为 100 股整数倍。

### 6.3 `rebalance_runs`

- `rebalance_run_id`：主键；
- `account_id`；
- `screen_run_id`；
- `signal_date`、`signal_time`、`execution_date`；
- `universe_hash`、`config_hash`、`strategy_version`；
- `target_weights_json`；
- `status`；
- `created_at`、`completed_at`；
- 唯一幂等约束：`account_id + signal_date + execution_date + universe_hash + config_hash + strategy_version`。

### 6.4 `paper_orders`

- `order_id`：主键；
- `rebalance_run_id`、`account_id`、`symbol`；
- `side`：`BUY/SELL`；
- `planned_quantity`、`remaining_quantity`；
- `reference_price_cny`、`limit_price_cny`；
- `status`：`PENDING/FILLED/PARTIALLY_FILLED/REJECTED/EXPIRED/CANCELLED`；
- `rejection_code`、`rejection_detail`；
- `created_at`、`updated_at`；
- 唯一约束：`rebalance_run_id + symbol + side`。

### 6.5 `paper_fills`

- `fill_id`：主键；
- `order_id`、`account_id`、`symbol`；
- `execution_date`、`execution_time`；
- `quantity`、`price_cny`；
- `commission_cny`、`stamp_tax_cny`、`other_fee_cny`；
- `source_bar_version_id`；
- 唯一成交幂等键：`order_id + execution_date + fill_sequence`。

### 6.6 `paper_cash_ledger`

- `cash_entry_id`：主键；
- `account_id`；
- `entry_type`：`DEPOSIT/BUY/SELL/COMMISSION/STAMP_TAX/DIVIDEND/CORPORATE_ACTION/ADJUSTMENT`；
- `amount_cny`：收入为正，支出为负；
- `business_key`：订单、成交或公司行为的稳定键；
- `occurred_at`、`created_at`；
- `balance_after_cny`；
- `business_key` 唯一，防止重复记账。

### 6.7 `paper_nav_snapshots`

- `account_id`、`valuation_date`：联合主键；
- `cash_cny`、`positions_value_cny`、`total_equity_cny`；
- `daily_return`、`cumulative_return`、`drawdown`；
- `price_version_id`、`created_at`。

资产恒等式必须成立：

```text
total_equity_cny = cash_cny + positions_value_cny
```

### 6.8 `paper_run_steps`

- `run_id`、`step_name`：联合主键；
- `status`：`PENDING/RUNNING/SUCCESS/BLOCKED/FAILED`；
- `input_hash`、`output_json`、`error_json`；
- `started_at`、`finished_at`。

## 7. 调仓计划

`RebalancePlanner` 接收账户快照、目标权重、T 日已发布收盘价和组合约束，输出确定性订单计划。T 日收盘价只用于估算订单，不能作为成交价；T+1 实际开盘价由执行引擎重新计算可成交数量。

规则：

1. 目标市值为 `可投资权益 × target_weight`；
2. 先计算全部卖单，再计算买单；
3. 卖出不得超过 `available_quantity`；
4. 清仓卖出允许不足 100 股的尾数；
5. 买入向下取整为 100 股整数倍；
6. 预估资金必须包含佣金，并明确标记为 `ESTIMATED`；
7. 目标权重为空时生成全现金清仓计划；
8. 相同输入必须生成相同订单和稳定哈希；
9. 计划阶段不得修改现金或持仓。

## 8. 模拟成交

`PaperExecutionEngine` 在 T+1 开盘处理 `PENDING` 订单：

1. 卖单优先，买单随后；
2. 使用未复权开盘价；
3. 停牌、涨停买入、跌停卖出必须拒单；
4. 成交数量受开盘可见成交量和 `max_participation_rate` 限制；
5. 跳空导致资金不足时，按实际开盘价重新缩减买入数量；
6. 使用现有佣金与印花税口径；
7. 零成交订单记录 `REJECTED`；
8. 部分成交记录 `PARTIALLY_FILLED`，剩余数量当日结束时 `EXPIRED`；
9. 当日未成交订单不跨日自动追单，下一次调仓重新计算。

成交批次在一个 DuckDB 事务中完成：

```text
校验 rebalance_run 和订单状态
→ 写 paper_fills
→ 更新 paper_positions
→ 写 paper_cash_ledger
→ 更新订单状态
→ 更新 rebalance_run
→ COMMIT
```

事务失败必须整体回滚。重复执行时由订单状态和业务幂等键阻止重复成交、重复扣款和重复加仓。

## 9. 公司行为与估值

### 9.1 自动处理

- 现金分红：在可用日写入现金流水；
- 送转、拆股、并股：调整数量和单位成本，总成本保持一致；
- 退市：复用现有退市恢复率规则，形成明确现金回收流水；
- 证券更名：只更新展示名称，不改变持仓主键。

### 9.2 阻断处理

配股、可转债、换股吸收合并等未支持行为若影响持仓，估值步骤返回 `DATA_ERROR`，不得静默忽略。

### 9.3 收盘估值

使用当日已发布未复权收盘价。持仓标的缺少当日价格时：

- 有明确停牌记录：允许使用最近有效收盘价，并在报告中标记 `STALE_SUSPENDED_PRICE`；
- 无停牌证明：返回 `DATA_ERROR`，不得使用旧价假绿。

## 10. Scheduler 与恢复

### 10.1 开盘任务

```text
calendar_gate
→ apply_effective_corporate_actions
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
→ run_screen
→ create_rebalance_plan
→ generate_reports
→ finalize_run
```

任务由以下字段唯一标识：

```text
job_type + account_id + trade_date + universe_hash + config_hash + strategy_version
```

每一步写入 `paper_run_steps`。重跑从第一个未成功步骤继续。`--force` 可以创建新的修订运行，但不能重放已经产生资金影响的成交；成交修订必须使用显式 `ADJUSTMENT` 流水，而不是修改历史记录。

首版由现有 CLI 配合 cron、systemd timer 或 Windows Task Scheduler 运行，不新增常驻服务。

## 11. 状态与错误语义

- `BLOCKED`：网络、供应商或所需数据暂不可用，未产生资金影响，可以安全重试；
- `DATA_ERROR`：行情、交易日历、复权、公司行为或版本覆盖不完整，不得成交或估值；
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

每个账户、交易日生成独立目录：

```text
reports/paper/<account_id>/<trade_date>/
  daily_summary.md
  orders.csv
  fills.csv
  positions.csv
  nav.csv
  run_manifest.json
```

`daily_summary.md` 至少包含：数据状态、选股排名、目标权重、计划与实际成交差异、拒单、现金、持仓、市值、净值、收益率、回撤、公司行为和降级原因。

`run_manifest.json` 至少包含：代码 commit、配置哈希、策略版本、Universe 哈希、市场数据版本、事件数据版本、screen run、rebalance run、signal time、execution date 和报告内容哈希。

报告生成使用临时文件和原子替换，避免崩溃留下半份报告。

## 14. 验收

### 14.1 中期验收 A：账本

- Schema 原子升级、失败回滚；
- 账户、订单、成交、持仓、现金和净值 Repository；
- 资产恒等式；
- 重复写入幂等；
- 事务失败无部分资金影响。

### 14.2 中期验收 B：交易闭环

- 目标权重转订单；
- T+1 开盘成交；
- 停牌、涨跌停、跳空、流动性和费用；
- 公司行为；
- 缺失价格不假绿；
- 重复执行不重复成交。

### 14.3 Tier A：离线五日重放

固定 fixture 连续重放五个交易日，验证：

- 输出完全确定；
- 每日现金、持仓和净值恒等；
- 崩溃恢复与正常运行结果一致；
- 重跑后订单、成交和现金流水数量不增加；
- 不读取未来行情、财务、事件和公司行为；
- 无负现金、负持仓或无法解释的净值跳变。

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
2. Task 1：模拟盘契约与 Schema；
3. Task 2：Paper Repository；
4. Task 3：调仓计划生成器；
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

## 16. 后续边界

阶段 6A 稳定后再分别设计：

- 阶段 6B：Web 运行管理与通知；
- 阶段 7：冻结上下文后的 Top 候选 LLM 风险复核；
- 多周期策略扩展；
- 券商适配和实盘安全体系。

这些能力不在本设计中预留未经使用的复杂抽象，只通过账户、订单、成交和报告契约保持可扩展边界。
