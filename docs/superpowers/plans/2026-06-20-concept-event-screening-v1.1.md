# TradingAgents-Astock 阶段 5：概念与事件增强选股（V1.1）实施计划

> 日期：2026-06-20  
> 状态：经双重审核修订，待用户确认后实施  
> 前置基线：阶段 4 基础选股与真实免费数据主链路已验收  
> 阶段边界：本阶段仅完成事件数据、事件评分和增强排名；模拟组合、Scheduler 自动化、连续运行与 Web 管理归入阶段 6

## 0. 执行原则

1. 编码前完整阅读：
   - `docs/superpowers/specs/2026-06-19-auto-stock-screening-design.md`
   - `docs/superpowers/specs/2026-06-19-real-data-and-universe-screening-design.md`
   - `docs/data/data-capability-matrix.yaml`
   - 本计划。
2. 先检查 Git 状态，禁止覆盖用户已有修改。
3. 严格按 Task 0→8 执行；每个 Task 遵循“失败测试→确认失败→最小实现→专项通过→全量回归→提交”。
4. Task 4、Task 7 后暂停，提交中期验收报告并等待确认。
5. 默认免费路径不得读取 `TUSHARE_TOKEN`，不得静默切换付费源。
6. `CURRENT_ONLY`、`BEST_EFFORT` 数据不得进入正式历史绩效。
7. 网络、权限、限流和质量错误不得伪装成合法空数据。
8. Pipeline 与历史回放只读 Repository，禁止隐式联网。
9. 事件增强默认关闭；关闭时阶段 4 业务输出必须等价。
10. 不实现实盘、模拟盘、ML 排序、LLM 候选复核、分钟交易、Scheduler 扩展或分布式队列。

每个 Task 至少运行：

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest <本任务测试文件> -q --capture=no
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents tests scripts
git diff --check
```

首次测试必须因缺少本任务能力而失败，不得因导入路径、网络或测试环境错误而失败。提交前再运行全量离线测试。

## 1. 阶段目标与数据流

阶段 5 在阶段 4 基础排名之后，只对有限候选集读取已经同步并发布的概念、公告、新闻、资金流和热点数据，形成可解释增强排名与风险标签。

```text
全市场/行业/概念/指数/自定义股票池
  → 阶段4基础过滤与 base_ranking
  → Top N（默认≤100）
  → 独立事件同步服务联网采集并原子发布
  → Pipeline 按 signal_time 只读已发布事件
  → event_ranking + risk_flags
  → 受限融合 enhanced_ranking
  → 复用阶段4 Portfolio 约束与报告
```

事件不负责发现股票，不修改阶段 4 因子，不对候选集以外股票逐股抓取。基础分、事件分、风险惩罚和降级原因必须分别保存。

## 2. 范围边界

### 2.1 本阶段包含

- 复用现有行业、概念、指数成员表和 `UniverseResolver`，增补板块别名、目录查询及组合股票池表达式；
- 公告、新闻、资金流、热点的统一事件契约；
- 免费数据源能力探测、录制契约、原始响应审计；
- 事件标准化、PIT、去重、修订、原子发布；
- 确定性规则评分、可信硬风险过滤、增强排名；
- 现有筛选 CLI 的事件同步/增强参数、JSON/Markdown 报告；
- 离线 fixture、单日人工 live smoke 和历史 PIT 防未来验证。

### 2.2 明确移入阶段 6

- 每日模拟组合、订单、持仓和净值账本；
- Scheduler 自动同步、检查点、幂等恢复与连续 5 个交易日运营观察；
- 多 Agent 风险复核、Web 管理和通知。

当前 `scheduler/jobs.py` 对历史日期使用 `max(post_close_signal_time(...), now)`，接入事件 Scheduler 前必须在阶段 6 修复：历史任务固定使用历史交易日 15:30，未来公告不得改变历史结果。本阶段不通过 Scheduler 做历史验收。

### 2.3 不包含

- 券商实盘、真实订单、ML 排序、全市场 LLM 扫描；
- 分钟、逐笔和高频数据；
- 用当前概念、新闻或资金流倒推历史；
- 无可靠发布时间或无历史快照事件的正式历史回测；
- 未经许可保存、再分发新闻全文。

## 3. 架构与文件边界

不建立第二套 Repository、板块成员体系或状态模型。

```text
tradingagents/events/
├── contracts.py       # 事件模型、枚举、PIT 等级
├── providers.py       # 可选 EventDataProvider Protocol；复用统一 Result/Capability 状态
├── normalizer.py
├── dedup.py
├── scoring.py
└── service.py         # 同步编排和只读查询服务

tradingagents/market_data/
├── migrations.py      # 事件 staging/正式表
├── repository.py      # 唯一持久化和 PIT 查询边界
└── providers/         # 免费源联网适配器；不在 events 层另建联网栈

tradingagents/screener/
├── universe_resolver.py  # 唯一股票池解析入口
├── config.py
├── pipeline.py
└── report.py

tests/events/
scripts/accept_event_enrichment.py
docs/event-data-quickstart.md
```

- `MarketDataProvider` 阶段 4 协议不强制新增事件方法。
- `EventDataProvider` 是可组合的可选协议，必须复用现有 `ProviderResult`、capability、错误和 probe 语义。
- `FreeAStockProvider` 可组合事件适配器，但 Pipeline 不得直接持有 Provider。
- `BoardNameResolver` 只处理标准代码、名称和别名；最终股票集合仍由现有 `UniverseResolver` 生成。

## 4. 数据等级、PIT 与降级规则

| 数据集 | 默认等级 | 当日增强 | 正式历史 |
|---|---|---:|---:|
| 官方公告及可靠发布时间 | `PIT_REQUIRED` | 是 | 历史覆盖和修订审计后 |
| 新闻 | `BEST_EFFORT` | 是 | 否 |
| 行业/概念/指数当前成员 | `CURRENT_ONLY` | 是 | 否 |
| 资金流 | `BEST_EFFORT` | 是 | 否 |
| 热点主题 | `CURRENT_ONLY` | 是 | 否 |

公告可用时间规则：

1. 有可靠日期和具体时间：`available_at=reported_at`，统一为 `Asia/Shanghai`。
2. 只有可靠公告日期：保守设为该日期之后的下一开市日 09:30，防止盘中或盘后信息前视。
3. 公告日期也不可靠：降级为 `BEST_EFFORT` 或拒绝入库，不得标记 `PIT_REQUIRED`。
4. 正式历史使用还必须验证历史覆盖、修订链和发布版本。

其他规则：

- `CURRENT_ONLY` 历史请求必须返回结构化 `data_error`。
- `BEST_EFFORT` 不计入正式历史绩效，也不能触发硬排除。
- 只有官方公告、交易所状态等已认证 `PIT_REQUIRED` 数据可以触发硬风险排除。
- 必需源失败则增强失败；非必需源失败保留基础排名并记录降级。
- 只有供应商明确确认的 `SUCCESS_EMPTY` 才是合法空结果。

## Task 0：冻结阶段 4 基线与兼容口径

**文件**

- Modify: `tradingagents/screener/config.py`
- Modify: `config/screener.example.yaml`
- Create: `tests/events/test_config.py`
- Create: `tests/events/test_stage4_equivalence.py`
- Create: `docs/superpowers/reports/2026-06-20-stage4-frozen-baseline.md`

配置：

```yaml
event_enrichment:
  enabled: false
  candidate_limit: 100
  max_event_age_days: 30
  event_weight: 0.20
  event_half_life_days: 7
  hard_risk_filter: true
  require_announcements: false
  require_news: false
  require_fund_flow: false
```

- [ ] 记录基线 commit、Schema 版本、测试数、fixture 哈希和 live smoke 摘要。
- [ ] 测试未知键、非法权重、负半衰期和候选数小于持仓数被拒绝。
- [ ] `enabled=false` 等价字段：股票池、排除原因、因子值、基础排名、目标权重、现金权重及回测结果。
- [ ] 新增但关闭的事件元数据单独比较，不纳入旧配置哈希等价判断。
- [ ] 历史筛选的 `signal_time` 必须由请求显式给出或确定性计算，禁止回退到当前时间。
- [ ] Commit：`feat(events): freeze stage4 compatibility baseline`

**验收：** 默认业务行为零变化。

## Task 1：免费数据源能力冻结门

**文件**

- Modify: `docs/data/data-capability-matrix.yaml`
- Create: `docs/superpowers/reports/2026-06-20-event-provider-probe.md`
- Create: `tests/fixtures/events/recorded/README.md`
- Create: `tests/events/test_provider_probe_contract.py`

逐数据集锁定主源、备源和禁用源。候选源可以包括交易所/法定披露平台及现有免费适配器，但不得在探测前假定可用。

每个源必须记录：具体平台和接口、认证要求、许可与保存范围、分页和历史范围、速率限制、公告时间精度、空结果语义、错误类型、响应样本哈希、主备切换条件。

- [ ] 覆盖沪市、深市、创业板、科创板；至少包含有公告、无公告、修订公告、分页、限流和网络失败。
- [ ] 录制响应必须脱敏；默认测试完全离线。
- [ ] 受许可限制时只保存元数据、许可摘要、URL 和哈希，不保存全文。
- [ ] 核心公告源若没有免费、无需 Token、时间字段可信的方案通过探测，则状态为 `BLOCKED`，暂停 Task 2–8，不以新闻源替代官方公告。
- [ ] 东财只能作为可选增强，失败不得阻断免费核心路径。
- [ ] Commit：`docs(events): freeze free provider capabilities`

**验收：** 数据源选择是可复现证据，不是原则性描述。

## Task 2：事件契约与 Provider 边界

**文件**

- Create: `tradingagents/events/__init__.py`
- Create: `tradingagents/events/contracts.py`
- Create: `tradingagents/events/providers.py`
- Create: `tests/events/test_contracts.py`
- Create: `tests/events/test_provider_protocol.py`

事件至少包含：

```text
event_id, event_type, title, summary, published_at, available_at,
source, source_url, source_record_id, source_version, content_hash,
pit_level, sentiment, severity, announcement_date_source,
raw_snapshot_id, dataset_version_id, ingestion_run_id, quality_status,
supersedes_event_id, ingested_at
```

股票关联单独存入 `event_symbol_links`，避免多股票公告复制正文。

- [ ] 写模型、枚举、时区、修订链和 PIT 失败测试。
- [ ] `EventDataProvider` 只定义拉取/probe 能力，复用阶段 4 的结果与错误结构。
- [ ] 旧 `MarketDataProvider` 和 Fixture 的阶段 4 契约不被强迫实现空方法。
- [ ] 事件类型覆盖财报、预告、分红、回购、增减持、质押、合同、重组、调查、处罚、ST/退市、停复牌、解禁、管理层变化、新闻、资金流和热点。
- [ ] Commit：`feat(events): define event contracts and provider boundary`

## Task 3：Schema、Repository 与原子发布

**文件**

- Modify: `tradingagents/market_data/migrations.py`
- Modify: `tradingagents/market_data/repository.py`
- Create: `tests/events/test_repository.py`
- Modify: `tests/market_data/test_migrations.py`
- Modify: `tests/market_data/test_staging_publish.py`

新增表：

```text
market_events, staging_market_events,
event_symbol_links, staging_event_symbol_links,
event_tags, staging_event_tags,
board_aliases
```

不新增 `board_catalog`、`board_snapshots` 或第二套成员表；复用 `board_definitions`、`board_memberships`、`staging_board_memberships`。资金流和热点按事件契约存储，本阶段不建独立权威快照表。

- [ ] 新增 Schema 版本，不修改旧迁移 SQL。
- [ ] 事件 bundle（主表、symbol links、tags）在同一事务中 staging→quality gate→publish；任一环节失败，三类正式数据均不可见。
- [ ] 发布状态唯一以现有 `dataset_versions.status=PUBLISHED` 为准；事件主表、links 和 tags 均保存 `dataset_version_id` 并关联该版本。
- [ ] `quality_status` 只表示单条记录质量（`VALID/WARN/REJECTED`），不得承担数据集发布状态。
- [ ] Pipeline 只能读取已关联 `PUBLISHED` 数据集版本、单条质量非 `REJECTED` 且 `available_at<=signal_time` 的记录。
- [ ] 稳定主键为 `source + source_record_id + source_version`；无稳定 ID 时使用规范化哈希并保存依据。
- [ ] 修订追加记录并通过 `supersedes_event_id` 关联，不覆盖旧记录。
- [ ] 区分物理重复与跨源疑似语义重复，输出各自统计。
- [ ] 测试发布中途异常、失败版本不可见、重跑幂等、修订并存和快照追踪。
- [ ] 不承诺数据库 downgrade；升级前备份 DuckDB，失败回滚为“代码回退+备份恢复”。测试旧库升级、重复升级和失败后旧库仍可读。
- [ ] Commit：`feat(events): add atomic event repository schema`

## Task 4：Fixture、板块别名与组合股票池

**文件**

- Modify: `tradingagents/market_data/providers/fixture.py`
- Modify: `tradingagents/screener/universe_resolver.py`
- Modify: `tradingagents/market_data/repository.py`
- Create: `tests/fixtures/events/provider_events.json`
- Create: `tests/events/test_fixture_provider.py`
- Create: `tests/events/test_board_aliases.py`
- Modify: `tests/market_data/test_phase44.py`
- Modify: `tests/market_data/test_phase46.py`

- [ ] Fixture 覆盖正/负/中性、重复、修订、未来、多股票、合法空和网络错误。
- [ ] `board_definitions` 表示目录，`board_memberships` 表示成员关系，`board_aliases` 只参与查询且不得改变标准代码。
- [ ] 支持精确代码、精确名称、别名；模糊或多义名称只返回候选，不静默选择。
- [ ] 支持字段内 `any/all`、字段间 `and/or`，最终集合仅由 `UniverseResolver` 生成。
- [ ] 当前快照仅允许当日 live；历史请求明确拒绝。
- [ ] Fixture probe 不读 Token、不联网，错误不得转换为合法空。
- [ ] Commit：`test(events): add fixtures and extend canonical universe resolver`

### 中期验收 A：必须暂停

提交：能力探测报告、迁移与回滚结果、原子发布测试、Fixture 全绿、五类股票池与交并集测试、`CURRENT_ONLY` 历史拒绝、阶段 4 等价性和 Git 提交清单。等待用户确认。

## Task 5：免费 Provider、标准化与同步服务

**文件**

- Modify: `tradingagents/market_data/providers/free_astock.py`
- Modify: `tradingagents/market_data/providers/free_astock_sources.py`
- Create: `tradingagents/events/normalizer.py`
- Create: `tradingagents/events/dedup.py`
- Create: `tradingagents/events/service.py`
- Create: `tests/events/test_free_event_provider.py`
- Create: `tests/events/test_normalizer.py`
- Create: `tests/events/test_dedup.py`
- Create: `tests/events/test_event_sync.py`

- [ ] 仅实现 Task 1 已通过并冻结的接口；每类数据独立 probe、限流和状态。
- [ ] 实现超时、有限重试、指数退避、节流和结构化错误。
- [ ] 同步服务负责联网、标准化、质量门禁和发布；查询服务与 Pipeline 只读 Repository。
- [ ] 标准化 6 位代码、`Asia/Shanghai`、标题、事件类型和 PIT 时间。
- [ ] 去重顺序：稳定来源 ID→公告编号/规范 URL→标题+时间+股票→内容哈希。
- [ ] 保存脱敏原始快照和响应哈希；网络失败不发布空版本。
- [ ] URL 使用域名白名单，限制重定向、响应大小和 MIME；HTML 清洗后再解析。
- [ ] Token、Cookie、查询参数脱敏；原始响应默认 `JSON.gz`，保留 30 天并提供清理命令，磁盘使用超过配置水位时报警。
- [ ] 新闻默认不保存完整正文，不得未经许可再分发。
- [ ] Commit：`feat(events): sync normalize and publish free event data`

## Task 6：确定性事件评分与风险规则

**文件**

- Create: `tradingagents/events/scoring.py`
- Create: `tests/events/test_scoring.py`
- Create: `tests/events/test_risk_rules.py`

冻结 V1.1 公式：

```text
每条事件原始影响值 x ∈ [-1, 1]
时间衰减 d = exp(-ln(2) * age_days / event_half_life_days)
各数据集子分 = clip(Σ(x*d) / max(1, Σd), -1, 1)
基础权重：公告0.45、新闻0.20、资金流0.20、概念热度0.15
缺失数据集不计入分母；全部缺失时不融合并保留 base_ranking
soft_risk_penalty ∈ [0, 0.50]
event_score = clip(可用子分加权平均 - soft_risk_penalty, -1, 1)
event_component = (event_score + 1) / 2
enhanced_score = (1-event_weight)*normalized_base_score + event_weight*event_component
```

原始影响值不得由实现者自由判断，按下列规则计算：

```text
sentiment_numeric：positive=+1，neutral/unknown=0，negative=-1
severity_multiplier：low=0.50，medium=0.75，high=1.00，critical=1.00
x = clip(sentiment_numeric * type_magnitude * severity_multiplier, -1, 1)
```

| 事件类型 | type_magnitude |
|---|---:|
| 财报 | 0.50 |
| 业绩预告 | 0.60 |
| 分红 | 0.35 |
| 回购 | 0.65 |
| 增减持 | 0.50 |
| 质押 | 0.45 |
| 重大合同 | 0.45 |
| 重组 | 0.70 |
| 调查/立案 | 0.90 |
| 处罚 | 0.80 |
| ST/退市 | 1.00 |
| 停复牌 | 0.60 |
| 解禁 | 0.35 |
| 管理层变化 | 0.30 |
| 新闻 | 0.30 |
| 资金流 | 0.40 |
| 热点主题 | 0.25 |

方向由标准化后的 `sentiment` 决定。例如“增持/利好合同”为 positive，“减持/处罚”为 negative；无法可靠判向时必须为 `unknown`，即 `x=0` 并保留 unknown 标记。

`soft_risk_penalty` 只来自明确的软风险标签，不因普通负面分数自动重复惩罚：每个去重后的风险类别按 `low=0.05`、`medium=0.15`、`high=0.30` 累加，总和封顶 `0.50`；`critical` 由可信硬风险规则处理。相同风险类别在同一信号日只计一次。

`normalized_base_score` 固定使用候选集内 min-max：`(base-min)/(max-min)`；零方差时全部取 `0.5`。同分排序依次使用 `enhanced_score desc`、`base_score desc`、`symbol asc`。

- [ ] 同类型同日事件累计影响绝对值封顶 1.0，避免重复轰炸放大。
- [ ] 硬风险仅由已认证官方 `PIT_REQUIRED` 且 `severity=critical` 的退市、立案、严重处罚、重大造假或长期停牌触发。
- [ ] 普通减持、质押和负面新闻只产生软惩罚，不自动排除。
- [ ] `unknown` 不得伪装为中性；全部缺失时输出降级原因。
- [ ] 测试时间衰减、边界、并列规则、重复不重复计分、最大影响及贡献可逆算。
- [ ] Commit：`feat(events): add deterministic explainable scoring`

## Task 7：Pipeline、报告与现有 CLI 集成

**文件**

- Modify: `tradingagents/screener/pipeline.py`
- Modify: `tradingagents/screener/report.py`
- Modify: `tradingagents/screener/config.py`
- Modify: `tradingagents/screener/cli.py`
- Create: `tests/events/test_pipeline.py`
- Create: `tests/events/test_cli.py`
- Modify: `tests/screener/test_phase47.py`
- Modify: `tests/screener/test_pipeline_lookahead.py`

```text
base_ranking→Top candidate_limit→Repository PIT 查询
→event_score/risk_flags→可信硬风险过滤→受限融合→复用现有 Portfolio
```

报告新增：

```text
base_ranking, event_ranking, enhanced_ranking,
event_contributions, risk_flags, event_dataset_versions,
event_data_sources, event_degradations, event_pit_level
```

- [ ] CLI 复用现有筛选入口，不新增重复 console script；事件同步可挂载现有 market-data CLI。
- [ ] 关闭增强时 Task 0 规定的阶段 4 业务字段完全等价。
- [ ] 开启时保留 base ranking 和原始因子，不覆盖阶段 4 输出。
- [ ] 只查询候选集；测试候选外 Provider/Repository 调用数为 0。
- [ ] 必需源失败 `data_error`；非必需源失败保留基础排名并记录降级。
- [ ] 历史 `CURRENT_ONLY` 明确拒绝；未来公告不得改变过去结果。
- [ ] 相同版本、配置和时间结果确定，贡献项能重算最终分。
- [ ] Commit：`feat(screener): integrate optional event enrichment`

### 中期验收 B：必须暂停

提交：阶段 4 等价性、三套排名样例、PIT 防未来测试、可信硬风险过滤、降级测试、版本/来源/贡献审计。等待用户确认。

## Task 8：验收脚本、文档与最终独立审核

**文件**

- Create: `scripts/accept_event_enrichment.py`
- Create: `docs/event-data-quickstart.md`
- Create: `tests/events/test_acceptance.py`

- [ ] 验收脚本分离 `--offline`、`--recorded-contract` 和 `--live-smoke`，不得在脚本中内嵌 pytest。
- [ ] JSON 报告包含 `status`、退出码语义、数据版本、来源、PIT 等级、请求量、耗时、内存、失败源和降级原因。
- [ ] `SUCCESS_EMPTY`、`BLOCKED`、`DATA_ERROR`、网络错误分别验收。
- [ ] live smoke 覆盖沪市、深市、创业板、科创板及有公告/无公告/修订/分页样本；网络受限时结论为 `BLOCKED`，不得假绿。
- [ ] Fixture 模拟连续 5 个交易日重放只验证确定性；真实连续 5 日属于阶段 6 运营验收。
- [ ] 独立审核所有 diff，不只审核 commit；关闭全部 P0/P1 后才可提交完成结论。
- [ ] Commit：`test(events): add layered acceptance and quickstart`

## 5. 验收标准

### 5.1 工程与回归

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/ -q --capture=no
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents tests scripts
git diff --check
git status --short
```

`git status --short` 仅供人工审计，不要求工作区绝对干净；必须确认 Token、DuckDB、缓存和原始响应未被暂存或提交。

### 5.2 数据、发布与 PIT

- 已使用事件中 `available_at > signal_time` 数量为 0。
- 事件主表、links、tags 原子发布；失败 bundle 全部不可见。
- 修订只在自身可用时间后生效，旧版本仍可追溯。
- `CURRENT_ONLY` 历史全部拒绝；`BEST_EFFORT` 不进入正式历史绩效。
- 网络错误不形成合法空；每条正式事件可追溯至版本和原始快照。
- 物理重复键数量为 0；跨源疑似重复单独报告，不以删除掩盖。

### 5.3 股票池与增强排名

- 全市场、行业、概念、指数、自定义及交并集继续可运行。
- 多义名称不静默猜测；未知代码返回 `data_error`。
- 关闭增强时阶段 4 业务输出等价。
- 开启后基础、事件、增强排名并列输出，排名变化可由贡献项逆算。
- 数据缺失、合法空、错误和降级可区分。

### 5.4 性能与稳定性

- 默认仅增强 Top 100，禁止候选外逐股请求和全市场 LLM。
- 离线和单日 live 分别记录耗时、请求、限流、重试、内存和失败分区。
- 正式性能阈值在首次真实基准后冻结，不在无证据时预设。

### 5.5 分级验收结论

最终报告必须分别给出，互不替代：

```text
A. 离线功能与 Fixture 验收
B. 当日免费 live 事件增强验收
C. 正式历史事件回测验收
D. 连续 5 个交易日运营稳定性（阶段 6）
```

本阶段完成要求 A 通过、B 通过或因外部网络明确 `BLOCKED` 且有人工环境复验安排。免费事件没有可靠历史快照时 C 必须标记“未验收”；D 不属于阶段 5 完成条件。

## 6. 真实数据验收矩阵

| 路径 | 本阶段要求 | 通过条件 |
|---|---:|---|
| Fixture 事件链路 | 必验 | 全绿、确定性、五日可重放 |
| 免费公告 live smoke | 必验 | 四市场板块样本、时间/来源/状态正确 |
| Top 100 增强 | 必验 | 不访问候选外股票 |
| 当日行业/概念/指数 | 必验 | 复用现有成员、来源明确 |
| 东财增强 | 可选 | 失败不阻断核心路径 |
| 全市场基础选股 | 必验 | 阶段 4 不回归 |
| 全市场事件逐股抓取 | 禁止 | 不得实现 |
| 历史 current-only | 必验 | 返回 `data_error` |
| 正式历史事件回测 | 条件验收 | 仅可靠历史公告覆盖通过后允许 |
| 模拟组合/Scheduler 连续五日 | 阶段 6 | 不阻塞阶段 5 |

## 7. 风险与治理

| 风险 | 应对 |
|---|---|
| 免费接口变化 | 录制契约、响应哈希、质量门禁 |
| 核心公告源不可用 | Task 1 阻断，不以弱来源冒充 |
| 新闻时间或版权不可靠 | `BEST_EFFORT`，只存许可元数据/摘要 |
| 概念漂移 | 当前快照，不倒推历史 |
| 多源重复 | 稳定 ID、规范哈希、疑似重复报告 |
| 修订覆盖旧版 | 追加版本和 `supersedes_event_id` |
| 事件误判 | 可信度门槛、确定性规则、贡献可解释 |
| 事件过度影响 | 融合权重和同类累计上限、消融测试 |
| 隐式联网 | Sync 联网、Repository 发布、Pipeline 只读 |
| 请求过多 | 先基础筛选，仅同步/增强 Top N |
| 原始快照膨胀 | 压缩、30 日保留、清理命令、磁盘水位 |
| 恶意响应或链接 | 域名/MIME/大小限制、HTML 清洗、脱敏 |

## 8. 任务依赖、提交顺序与完成定义

```text
5.0  阶段4基线与等价口径
  ↓
5.1  免费数据源能力冻结门（未通过则 BLOCKED）
  ↓
5.2  事件契约与 Provider 边界
  ↓
5.3  Schema、Repository 与原子发布
  ↓
5.4  Fixture、别名和现有 UniverseResolver 扩展
     ─── 中期验收 A ───
  ↓
5.5  免费 Provider、标准化与同步服务
  ↓
5.6  确定性评分与可信风险规则
  ↓
5.7  Pipeline、报告与现有 CLI
     ─── 中期验收 B ───
  ↓
5.8  分层验收、文档与最终独立审核
```

只有满足以下条件才可宣称阶段 5 代码完成：Task 0–8 已提交；两次中期确认；阶段 4 零回归；免费核心公告源能力有证据；事件 bundle 原子发布；当日五类股票池可运行；三套排名、风险和贡献可审计；免费路径无 Tushare；未认证数据未进入正式历史绩效；最终独立审核无未关闭 P0/P1。

“阶段 5 代码完成”不等于“正式历史事件回测通过”或“连续 5 日运营通过”，必须按 §5.5 分项表述。阶段 6 再单独设计模拟组合、Scheduler、连续运行、多 Agent 风险复核与 Web 管理。
