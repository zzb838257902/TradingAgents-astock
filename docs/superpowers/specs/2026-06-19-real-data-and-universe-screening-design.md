# TradingAgents-Astock 阶段 4：真实数据与多范围自动选股设计

> 日期：2026-06-19
>
> 状态：用户已确认，独立审核后修订
>
> 前置基线：阶段 0–3 自动选股 MVP 已通过独立验收
>
> 适用范围：个人研究、模拟选股和历史回测，不包含实盘下单与商业数据再分发

## 1. 文档目的

阶段 0–3 已完成离线 fixture 驱动的核心链路：

```text
CLI → Pipeline → Repository(PIT) → 股票池过滤 → 策略 → 组合 → 回测
```

阶段 4 在不破坏该链路的前提下接入真实 A 股数据，建立可增量更新、可追踪、可恢复的数据流程，并支持全 A 股、行业板块、概念板块、指数成分股和用户自定义列表选股。现有单股分析、CLI、Web 和 fixture 离线模式必须继续可用。

## 2. 目标与非目标

### 2.1 目标

1. 建立统一、可插拔的真实数据 Provider，首个正式实现为 Tushare Pro。
2. 建立证券主表、交易日历、日线行情、每日指标、财务数据和板块成员关系的数据仓库。
3. 支持历史初始化、增量更新、失败重试、断点续传、幂等写入和质量检查。
4. 所有策略数据保留 `available_at`，确保实时筛选与历史回测符合 point-in-time 约束。
5. 使用统一请求表达全市场、行业、概念、指数和自定义股票池。
6. 让真实数据与 fixture 复用相同的过滤、因子、策略、组合和回测代码。
7. 输出数据来源、质量状态、股票池规模、排除原因、排名、仓位和现金权重。
8. 提供本地盘后任务入口和可审计的运行报告。

### 2.2 非目标

- 券商实盘下单；
- 分钟、逐笔或高频行情；
- 分布式任务集群；
- 商业数据再分发；
- 全市场逐只运行多 Agent 分析；
- 机器学习排序；
- 规避供应商授权或限流；
- 对缺少历史快照的数据进行伪 PIT 回测。

## 3. 核心设计原则

### 3.1 保持已验收核心不变

阶段 4 只扩展数据入口，不重新实现已验收的股票池过滤、策略、组合和回测逻辑。

```text
FixtureProvider ─┐
                 ├→ MarketDataRepository → 现有 Pipeline
TushareProvider ─┘
```

### 3.2 职责隔离

- Provider：获取、分页、限流和字段转换；
- Repository：存储、版本管理和 PIT 查询；
- Universe Resolver：解析选股范围；
- Pipeline：编排标准化数据；
- Strategy、Portfolio、Backtest：不得访问网络或供应商 SDK。

### 3.3 错误不能伪装成空数据

统一结果状态为 `OK`、`EMPTY`、`STALE`、`PARTIAL`、`ERROR`。只有 `EMPTY` 可以生成合法空股票池；`ERROR`、`STALE` 和关键数据的 `PARTIAL` 必须阻止正式选股或要求显式降级。

### 3.4 PIT 优先

任何策略查询都必须满足：

```text
record.available_at <= signal_time
```

财务数据以公开披露时间生效；板块成员以历史有效区间或已保存快照生效；抓取时间、报告期和交易日期都不能替代 `available_at`。

## 4. 数据源方案

### 4.1 候选方案

#### 方案 A：Tushare Pro 统一主源（推荐）

证券、行情、财务、行业、指数和概念接口较统一；日线和每日指标适合横截面同步；申万行业成员带纳入、剔除日期；东方财富概念成员可按交易日期获取。限制是部分接口需要积分，部分概念数据存在个人研究和商业授权边界。

#### 方案 B：完全免费组合源

组合 mootdx、交易所公开资料和 AKShare 等来源。成本较低，但字段定义、历史覆盖、稳定性和许可不一致，概念板块历史 PIT 较弱。仅作为开发备用或当前快照补充。

#### 方案 C：商业授权源

使用 Wind、聚宽或其他有明确商业授权的数据服务。适合未来商业部署，但成本和接入复杂度较高。阶段 4 只保留扩展点。

### 4.2 最终选择

阶段 4 采用 `Tushare Pro 主源 + FixtureProvider 离线测试 + 可插拔备用 Provider`。不得将多个来源静默拼接；每条数据必须记录来源和冲突处理。

### 4.3 数据能力矩阵

| 数据集 | 首选接口 | 备用来源 | PIT 等级 | 说明 |
|---|---|---|---|---|
| 证券主表 | `stock_basic` | 交易所/mootdx | `PIT_REQUIRED` | 保存上市、退市和状态变化 |
| 交易日历 | `trade_cal` | 交易所 | `PIT_REQUIRED` | 作为交易日唯一依据 |
| 日线行情 | `daily` | mootdx/AKShare | `PIT_REQUIRED` | 盘后可用时间用上海时区 |
| 每日指标 | `daily_basic` | 不静默降级 | `PIT_REQUIRED` | 估值、市值、换手率 |
| 财务数据 | 财报接口、`fina_indicator` | 交易所披露 | `PIT_REQUIRED` | 按公告时间生效 |
| 申万行业成员 | `index_member_all` | 当前快照 | `PIT_REQUIRED` | 使用 `in_date/out_date` |
| 指数成员 | 指数成分/权重接口 | 指数公司 | `PIT_REQUIRED` | 保存生效日和快照日 |
| 东方财富概念 | `dc_member` | 无 | 经验证后为 `PIT_REQUIRED` | 按交易日保存历史快照 |
| 同花顺概念 | `ths_member` | AKShare 当前快照 | `CURRENT_ONLY` | 不推断历史生效日期 |

官方参考：

- Tushare 数据目录：https://tushare.pro/document/2
- 申万行业成分：https://tushare.pro/document/2?doc_id=335
- 财务指标：https://tushare.pro/document/2?doc_id=79
- 每日指标：https://tushare.pro/document/2?doc_id=32
- 同花顺概念成员：https://tushare.pro/document/2?doc_id=261
- 东方财富概念成员：https://tushare.pro/document/2?doc_id=363

接口权限、积分和许可可能变化。开发前必须重新核对官方说明，并将核对日期写入能力矩阵。

## 5. 总体架构

```text
供应商 API
  → Provider Adapter
  → Raw Snapshot（只追加）
  → Normalizer + Validation
  → MarketDataRepository（DuckDB）
  → Universe Resolver
  → 现有 filter_universe()
  → Factors → Strategy → Portfolio → Backtest
  → Run Report
```

推荐职责目录：

```text
tradingagents/market_data/
├── contracts.py
├── repository.py
├── migrations.py
├── quality.py
├── sync.py
├── cli.py
└── providers/
    ├── base.py
    ├── fixture.py
    └── tushare.py

tradingagents/screener/
├── universe.py
├── universe_resolver.py
├── pipeline.py
└── cli.py

tradingagents/scheduler/
├── jobs.py
└── state.py
```

这是职责边界而非强制复制目录。现有文件已承担相同职责时应扩展现有实现，禁止形成两套权威 Repository。

## 6. 数据契约

### 6.1 Provider 结果

```python
DataResult[T](
    data=T | None,
    status="ok|empty|stale|partial|error",
    source="tushare",
    as_of=datetime,
    available_at=datetime | None,
    pit_level="pit_required|current_only|best_effort",
    errors=(),
)
```

### 6.2 Provider 接口

```python
class MarketDataProvider(Protocol):
    def list_securities(self, as_of: date) -> DataResult[list[Security]]: ...
    def get_trade_calendar(self, start: date, end: date) -> DataResult[list[TradingDay]]: ...
    def get_daily_bars(self, symbols, start: date, end: date) -> DataResult[list[DailyBar]]: ...
    def get_daily_indicators(self, trade_date: date) -> DataResult[list[DailyIndicator]]: ...
    def get_financials(self, symbols, announced_before: datetime) -> DataResult[list[FinancialRecord]]: ...
    def get_industry_members(self, code: str, as_of: datetime) -> DataResult[list[Membership]]: ...
    def get_concept_members(self, code: str, as_of: datetime) -> DataResult[list[Membership]]: ...
    def get_index_members(self, code: str, as_of: datetime) -> DataResult[list[Membership]]: ...
```

所有策略数据必须保存：

```text
source, source_record_id, as_of, available_at, ingested_at,
dataset_version, quality_status, pit_level, raw_snapshot_id
```

全部时间字段使用带时区的 `datetime`，市场时间统一为 `Asia/Shanghai`。

## 7. 数据仓库

标准化数据使用 DuckDB；原始响应使用压缩 JSON 或 Parquet 只追加保存。调度状态先与 DuckDB 同库，只有出现明确并发需求时才引入 SQLite，避免双数据库协调。

核心数据集：

- `security_master`：代码、名称、市场、上市/退市日期、状态；
- `trade_calendar`：交易所、日期、开闭市状态；
- `daily_bars`：OHLC、昨收、成交量、成交额；
- `daily_indicators`：市值、估值和换手率；
- `financial_records`：报告期、公告日、可用时间和财务指标；
- `board_definitions`：行业、概念和指数定义；
- `board_memberships`：成员、生效区间、快照日和 PIT 等级；
- `raw_snapshots`：原始文件、哈希、来源和抓取时间；
- `ingestion_runs`：任务参数、游标、状态、计数和错误；
- `data_quality_events`：规则、严重级别和处理状态。

稳定业务键示例：

```text
daily_bars:        (symbol, trade_date, source)
daily_indicators:  (symbol, trade_date, source)
financial_records: (symbol, report_period, announcement_date, source, record_type)
board_memberships: (board_type, board_code, symbol, effective_from, source)
```

相同任务重复执行时，记录数和内容哈希不得无故变化。供应商修订数据时保留原始快照和修订时间。

## 8. 股票池查询

### 8.1 统一请求

```python
UniverseRequest(
    universe_type="all|industry|concept|index|custom",
    universe_code=None,
    symbols=(),
    as_of=datetime,
)
```

约束：

- `all` 不需要板块代码；
- `industry`、`concept`、`index` 必须提供板块代码；
- `custom` 必须提供非空代码列表；
- `as_of` 必须使用上海时区；
- 不允许同时传板块代码和自定义列表形成语义不明的并集。

### 8.2 查询流程

```text
UniverseRequest
→ 校验范围和日期
→ 按 as_of 查询有效成员
→ 与有效证券主表求交集
→ filter_universe()
→ included 进入因子/组合/回测
→ excluded 输出结构化原因
```

### 8.3 CLI 目标

```bash
python -m tradingagents.screener.cli screen --universe all --as-of 2026-06-19
python -m tradingagents.screener.cli screen --universe industry --universe-code 801080.SI --as-of 2026-06-19
python -m tradingagents.screener.cli screen --universe concept --universe-code BK1184.DC --as-of 2026-06-19
python -m tradingagents.screener.cli screen --universe index --universe-code 000300.SH --as-of 2026-06-19
python -m tradingagents.screener.cli screen --universe custom --symbols 600000.SH,000001.SZ --as-of 2026-06-19
```

实现时可按现有 CLI 风格调整参数名称，但五种模式和语义必须保持一致。

## 9. 同步流程

建议命令：

```bash
python -m tradingagents.market_data.cli init
python -m tradingagents.market_data.cli sync --dataset security-master
python -m tradingagents.market_data.cli sync --dataset trade-calendar
python -m tradingagents.market_data.cli sync --dataset daily --start 2020-01-01 --end 2026-06-19
python -m tradingagents.market_data.cli sync --dataset financials
python -m tradingagents.market_data.cli sync --dataset memberships
python -m tradingagents.market_data.cli status
```

日常流程：

```text
确认交易日
→ 同步证券状态
→ 同步日线和每日指标
→ 同步新披露财务数据
→ 同步板块定义和成员快照
→ 数据质量检查
→ 仅在关键数据通过后发布数据版本
```

可靠性要求：

- 保存分页游标和成功分片；
- 限流、超时和临时错误采用有上限的指数退避；
- 认证失败、无权限和契约变化不得无限重试；
- 中断后从最后成功分片继续；
- staging 校验通过后再原子发布；
- 失败不得破坏上一已发布版本；
- 日志不得包含 Token 或完整请求签名。

## 10. 数据质量

阻断级规则：

- 业务主键重复；
- OHLC 关系非法；
- 成交量或成交额为负；
- 证券代码无法标准化；
- `PIT_REQUIRED` 数据缺少 `available_at`；
- 财务数据在公告前可见；
- 板块成员生效区间倒置；
- 关键分页缺失；
- 数据源错误被转换为空结果。

告警级规则：

- 日线完整率低于预期；
- 单日价格或成交量异常跳变；
- 板块成员数量异常变化；
- 数据超过新鲜度阈值；
- 多来源字段冲突。

阻断级异常停止正式选股；告警级异常写入报告，由配置决定是否允许降级。

## 11. Pipeline 输出

Pipeline 增加 `UniverseRequest` 和数据版本输入，继续复用当前策略链路。每次运行至少输出：

```text
run_id, signal_time, data_as_of, dataset_version,
data_sources, data_quality, pit_level,
universe_type, universe_code, universe_size,
included_count, excluded_count, excluded_reasons,
ranking, factor_contributions, target_weights, cash_weight,
industry_by_symbol, industry_weights, orders, metrics
```

若概念成员仅为 `CURRENT_ONLY`，允许当日筛选并显著标记，但历史回测必须拒绝；不得用今天的概念成员替代过去日期成员。

## 12. 本地调度

阶段 4 只提供本地任务入口，不引入 Celery、消息队列或分布式锁。

```text
15:30 后触发
→ 检查交易日和数据可用性
→ 增量同步
→ 质量门禁
→ 自动选股
→ 保存运行报告
```

任务通过 `job_name + trade_date + config_hash` 保持幂等。失败任务允许人工重跑，并保留全部尝试记录。

## 13. 开发任务拆分

### 任务 4.1：Provider 契约与 Tushare 适配器

- 扩展 Provider 接口和结构化结果；
- 实现认证、分页、限流和字段映射；
- Token 只从环境变量读取；
- FixtureProvider 继续满足同一契约；
- 添加完全离线的契约测试。

### 任务 4.2：DuckDB 仓库与迁移

- 建立核心表和版本迁移；
- 实现 upsert、原始快照引用和 PIT 查询；
- 禁止建立第二套权威 Repository；
- 添加迁移、幂等和 PIT 测试。

### 任务 4.3：证券、日历和日线同步

- 建立最小真实数据闭环；
- 实现历史初始化、日增量、分页恢复和质量门禁；
- 使用真实 Repository 运行现有因子和回测；
- 完成后先执行中期验收。

### 任务 4.4：行业和指数股票池

- 同步定义和历史成员；
- 实现 `UniverseResolver`；
- 验证历史日期不使用未来成员；
- 接通行业和指数 CLI。

### 任务 4.5：财务数据

- 同步报表和财务指标；
- 按公告时间构造 `available_at`；
- 处理重复披露和修订版本；
- 验证公告日前不可见。

### 任务 4.6：概念板块

- 验证 `dc_member` 历史能力和许可；
- 可验证历史快照标记为 `PIT_REQUIRED`；
- 当前快照标记为 `CURRENT_ONLY`；
- 历史回测对 `CURRENT_ONLY` 明确报错。

### 任务 4.7：统一 Pipeline 与报告

- 五种股票池进入同一 Pipeline；
- 输出范围、版本、质量、PIT 和组合信息；
- 数据失败和合法空池使用不同退出状态。

### 任务 4.8：调度、文档与回归

- 提供本地盘后任务和状态查询；
- 更新安装、配置、同步和故障恢复文档；
- 运行专项测试、全量测试和 Ruff；
- 验证单股分析、原 CLI、Web 和 fixture 无回归。

开发顺序：

```text
4.1 → 4.2 → 4.3 → 4.4 → 4.5 → 4.6 → 4.7 → 4.8
```

每项任务采用测试先行方式，形成独立、可回退的提交。

## 14. 验收标准

### 14.1 数据采集

1. 活跃 A 股覆盖率不低于供应商当日活跃证券数的 99%。
2. 正常交易日日线完整率不低于应有记录数的 99.5%。
3. 核心表业务主键重复数为 0。
4. 同参数连续运行两次，第二次不产生非预期新增记录，内容哈希一致。
5. 模拟分页中断后可续传，最终结果与无中断运行一致。
6. Token 不出现在代码、日志、fixture、异常文本和新增 Git 历史中。

### 14.2 PIT

1. 查询结果中 `available_at > signal_time` 的记录数为 0。
2. 财务记录在公告或实际披露时间前不可见。
3. 上市、退市、行业和指数成员按历史有效区间查询。
4. 当前概念成员不得进入过去日期的正式回测。
5. `CURRENT_ONLY` 数据进入历史回测时产生明确拒绝错误。
6. PIT 专项测试全部通过。

### 14.3 股票池

全市场、行业、概念、指数、自定义五种模式均可通过 CLI 和应用服务运行，并满足：

1. 原始股票池数量可追踪；
2. included 均属于请求范围且在 `as_of` 有效；
3. excluded 均有结构化原因；
4. 数据错误与合法空股票池可区分；
5. 相同数据版本、配置和信号时间产生相同结果。

### 14.4 选股与组合

1. 输出第 11 节规定字段；
2. 个股权重不超过配置上限；
3. 行业权重不超过配置上限；
4. 现金不低于最低缓冲；
5. 股票权重与现金权重之和约等于 1；
6. 跳空后的实际成交金额不突破目标金额和可用现金；
7. 数据来源、PIT 等级和质量状态可审计。

### 14.5 兼容性与工程质量

1. 原单股分析入口和参数保持兼容。
2. 原 CLI 和 Web 正常启动。
3. fixture 模式在无网络、无 Tushare Token 时正常运行。
4. 阶段 0–3 既有测试不得回归；当前基线为 `156 passed, 1 skipped`。
5. 新增单元、集成、端到端和 CLI 测试全部通过。
6. 测试默认不访问真实网络、不消耗真实额度。
7. Ruff 检查通过。
8. `.env`、Token、缓存、虚拟环境和本地数据库不得提交。

## 15. 开发环境补充

在现有 MVP 环境基础上新增 Tushare SDK、DuckDB Python 包、可选 Parquet 支持库和环境变量 `TUSHARE_TOKEN`。

```text
无 Token：fixture、单元测试和原有功能正常运行
有 Token：允许显式执行真实数据同步
```

导入模块时不得自动联网，测试不得依赖开发者个人 Token。

## 16. 风险与降级

| 风险 | 处理方式 |
|---|---|
| Tushare 积分不足 | 启动前检查能力；缺少关键权限时阻止对应同步 |
| 概念历史不完整 | 标记 `CURRENT_ONLY`，禁止正式历史回测 |
| 供应商字段变化 | 契约校验失败并保留原始响应，不写正式表 |
| 日线未完成更新 | 标记 `STALE/PARTIAL`，不发布当日正式版本 |
| 网络或限流 | 有上限重试和断点续传，不转换为空结果 |
| 多源冲突 | 记录冲突和优先级，不静默覆盖 |
| 商业许可变化 | 部署前重新核对；本阶段不承诺商业使用权 |

## 17. 交付物

1. Provider、Repository、同步、质量和 Universe Resolver 代码；
2. DuckDB 迁移和数据字典；
3. 五种范围的 CLI 与应用服务入口；
4. 离线 fixture、契约测试、PIT 测试和端到端测试；
5. 更新后的数据能力矩阵；
6. 环境、Token、初始化、增量同步和故障恢复文档；
7. 测试与静态检查结果；
8. 已知限制和未获 PIT 认证的数据集清单；
9. 阶段 4 实施总结与独立校验报告。

## 18. 后续边界

阶段 4 验收后再分别设计：多周期策略、Top 候选多 Agent 复核、每日模拟盘与绩效监控、Web 运行管理，以及商业部署所需的数据授权和安全审计。阶段 4 不提前实现这些功能，避免数据接入和策略扩张同时发生而难以验收。


## 19. 独立审核后的规范性修订

> 本节根据独立子智能体审核结果补充，属于阶段 4 的强制规范。如与前文简化描述存在冲突，以本节为准。

### 19.1 时间语义与真实数据完成检查

必须分别记录：

- `signal_time`：策略声明的决策时点；
- `data_available_at`：市场参与者实际可获得数据的时间；
- `ingested_at`：本系统抓取时间；
- `run_time`：任务实际执行时间。

15:30 只是阶段 0–3 fixture 的盘后信号约定，不代表真实供应商已经完整发布全市场数据。Provider 必须通过完整性探测确认发布完成，并记录保守的实际 `available_at`；禁止将更晚到达的数据回填为 15:30 可用。

若数据在 `signal_time` 后才完整，系统只能按配置使用上一已发布完整版本、等待并生成更晚的信号，或终止本次运行。历史回放必须采用当时可证明的可用时间，不能用当前观察到的供应商更新时间反推过去。

### 19.2 复权、公司行为与真实成交价格

新增并纳入阶段 4 必做范围：

- `adjustment_factors`：复权因子及其实际可用时间；
- `corporate_actions`：现金分红、送转、拆并股、配股和除权除息；
- `security_status_history`：ST、退市整理等状态的有效区间；
- `name_history`：证券历史名称及有效区间；
- `suspension_events`：停复牌及临时停牌事件；
- `price_limits`：每日真实涨跌停价或可审计的规则计算结果。

原始 OHLC、成交量、复权因子和公司行为必须分别保存，不得用复权价格覆盖原始行情。因子必须声明 `price_basis=raw|forward_adjusted|backward_adjusted`；成交、订单金额和涨跌停判断使用未复权真实价格。组合账本必须处理现金分红、送转、拆并股和配股。

复权序列只能使用截至 `as_of` 已可获得的因子，禁止使用事后更新的全历史复权结果形成未来信息。ST 判断不得依赖当前名称，停牌不得等同于行情缺失。专项回测至少覆盖主板、创业板、科创板、北交所、ST、退市整理、IPO 初期、停牌和一字涨跌停。

### 19.3 数据源能力探测与许可

Tushare Pro 是首选候选 Provider，不代表部署账号必然拥有全部接口。初始化必须执行 capability probe，记录每个接口的权限、历史起点、日期范围、频率限制、单次行数和许可声明。未通过探测的数据集不得标记为生产就绪，也不得被静默替换为空数据。

Provider 状态至少区分：

`OK | SUCCESS_EMPTY | NOT_AVAILABLE_YET | STALE | PARTIAL | PERMISSION_DENIED | RATE_LIMITED | NETWORK_ERROR | DATA_QUALITY_FAILED`

只有供应商确认的 `SUCCESS_EMPTY` 可以形成合法空股票池。

### 19.4 财务数据 PIT

财务记录至少保存 `report_period`、`announcement_date`、`actual_announcement_time`、`available_at`、`update_flag`、`source_version` 和 `ingested_at`。

只有公告日期而没有实际时间时采用保守规则：该记录在公告日当日信号中不可见，最早从下一交易日开盘前可用。更正和重述必须追加新版本，不得覆盖旧版本，并且仅在新版本自己的 `available_at` 后生效。

### 19.5 板块成员证据模式

每条行业、概念和指数成员关系必须声明：

`membership_mode=effective_interval|dated_snapshot|current_only`

- `effective_interval` 使用半开区间 `[effective_from, effective_to)`；
- `dated_snapshot` 只能从快照日期起向后使用，不得推断更早历史；
- `current_only` 禁止参与历史回测。

历史快照缺失时必须失败或显式降级，禁止用当前成分回填历史。

### 19.6 原子发布、幂等和恢复

每次采集生成唯一 `ingestion_run_id`，数据先进入 staging。检查点至少保存数据集、请求参数、日期分区、分页游标和最后成功批次。完成分页、去重、质量检查和数量校验后，才可以原子发布 `dataset_version`。

Pipeline 只能读取状态为 `PUBLISHED` 的版本。失败或未完成版本不得覆盖最近成功版本。相同输入任务重复执行不得产生不同业务记录。

原始快照必须保存脱敏请求参数、响应摘要、内容哈希、供应商接口版本和抓取时间，且标准化记录可以追溯到对应快照哈希。

### 19.7 可自动计算的验收口径

证券覆盖率：

- 分母：目标交易所证券主表按统计日期生成的目标证券，排除非目标证券类型、上市前和退市后证券；
- 分子：成功发布且通过质量检查的证券主表记录；
- 阈值：不低于 99%。

日线完整率：

- 分母：由证券主表、交易日历和证券状态历史生成，排除上市前、退市后和已确认全天停牌证券；
- 分子：通过质量检查的日线记录；
- 阈值：不低于 99.5%。

覆盖率验收必须输出机器可读的 `status`、`numerator`、`denominator`、`ratio`、`threshold`、排除项和异常明细。真实数据验收与离线 CI 分开执行。

### 19.8 阶段 0–3 兼容契约

阶段 4 只能通过现有 Repository/Provider 边界接入真实数据。不得改变阶段 0–3 已验收的目标权重定义、PIT 比较规则、股票池排除语义和回测成交约束；如确需改变，必须提供迁移说明并重新执行阶段 0–3 验收。

必须增加等价性契约测试：同一 fixture 经原入口和新 Repository 入口运行时，排名、排除原因、目标权重、现金权重、订单和指标在规定数值容差内一致。现有单股分析、CLI、Web 和 fixture 离线模式全部纳入回归测试。
