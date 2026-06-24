# 阶段 4 冻结基线报告（阶段 5 Task 0）

> 日期：2026-06-20
> 用途：阶段 5 事件增强开发前的零回归对照基线

## Git 基线

| 项 | 值 |
|---|---|
| 基线 commit（Task 0 开始前） | `21808d89` |
| 分支 | `main` |
| 相对 `origin/main` | 领先 54 commits |

## Schema 与数据

| 项 | 值 |
|---|---|
| DuckDB Schema 版本 | `9`（`tradingagents/market_data/migrations.py`） |
| 主筛选 Fixture | `tests/fixtures/screener/mvp_market.json` |
| Fixture SHA256 | `42e43a4ba99c8d81812aaa0fb875d2f70072e5555f984a1cadd0680ad6731b6e` |

## 测试基线

| 项 | 值 |
|---|---|
| 全量离线测试（Task 0 完成后） | 332 passed，1 skipped |
| Task 0 新增测试 | `tests/events/test_event_config.py`、`tests/events/test_stage4_equivalence.py` |

## Live Smoke 摘要（阶段 4 已验收）

阶段 4 免费数据路径 smoke 已于 commit `b9f2f58e` 后通过（2 股选股闭环）。本 Task 不重复宣称 live 验收；阶段 5 事件 live 属于后续 Task。

## 阶段 4 兼容口径（冻结）

### 默认配置

`event_enrichment.enabled=false`（默认），其余字段见 `config/screener.example.yaml`。

### 业务等价字段（`enabled=false` 时必须不变）

- 股票池与 `excluded_reasons`
- 因子贡献 `factor_contributions`
- 基础排名 `ranking`
- 目标权重 `target_weights`、`cash_weight`
- 回测 `orders`、`metrics`、`positions`、`top_symbol`

### 配置哈希

- `ScreenerConfig.stage4_config_hash()`：`enabled=false` 时排除 `event_enrichment` 块
- CLI `config_hash` 使用 `stage4_config_hash()`，关闭增强时修改事件元数据不影响阶段 4 哈希

### 历史 `signal_time`

- 正常路径：`post_close_signal_time(fixture 倒数第二交易日)`
- PIT 错误路径：同样使用 fixture 确定性日期，禁止 `datetime.now()` 回退

## Task 0 验收结论

- [x] 记录基线 commit、Schema、测试数、fixture 哈希
- [x] 未知键、非法权重、负半衰期、候选数 < 持仓数被拒绝
- [x] `enabled=false` 时阶段 4 业务输出等价
- [x] 关闭的事件元数据不纳入 `stage4_config_hash`
- [x] 历史筛选 `signal_time` 确定性计算

**默认业务行为：零变化。**
