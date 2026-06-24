# 现有功能缺陷修复 — 冻结基线（Task 0）

> 日期：2026-06-21  
> 用途：专项缺陷修复（Task 1–7）前的零回归对照基线

## Git 基线

| 项 | 值 |
|---|---|
| 基线 commit（Task 0 开始前） | `bf587e72` |
| 分支 | `main` |
| 相对 `origin/main` | 领先 70 commits |
| 工作区 | 干净；仅未跟踪 `docs/superpowers/plans/2026-06-21-existing-defects-remediation.md`（实施计划，本 Task 不提交） |

## Schema 与数据

| 项 | 值 |
|---|---|
| DuckDB Schema 版本 | `10`（`tradingagents/market_data/migrations.py`） |
| 主筛选 Fixture | `tests/fixtures/screener/mvp_market.json` |
| Fixture SHA256 | `42e43a4ba99c8d81812aaa0fb875d2f70072e5555f984a1cadd0680ad6731b6e` |

## 测试基线

| 项 | 值 |
|---|---|
| 全量离线测试 | `475 passed`，`1 skipped`（`tests/test_google_api_key.py`） |
| 命令 | `MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no` |
| Task 0 新增测试 | `tests/remediation/test_baseline.py` |

## 阶段五验收（修复前状态）

| 层级 | 命令 | 结果 |
|---|---|---|
| Tier A 离线 | `python3 scripts/accept_event_enrichment.py --offline --recorded-contract` | 通过 |
| 中期验收 B | 见 `docs/superpowers/reports/2026-06-20-stage5-acceptance-b.md` | 已通过 |

## 阶段四默认选股输出（冻结哈希）

配置：`ScreenerConfig` 默认 + 放宽 `min_listing_days=2`、`min_avg_amount_20d=1_000_000`；`event_enrichment.enabled=false`。

哈希字段：`excluded_reasons`、`ranking`、`target_weights`、`cash_weight`、`factor_contributions`、`top_symbol`、`positions`、`metrics`。

| 项 | 值 |
|---|---|
| 默认选股输出 SHA256 | `339f144bf7120d678a5c86e78b801fe99492c6c70732131ffdbce8800545381b` |

## 阶段五关闭时业务等价字段

与 `tests/events/test_stage4_equivalence.py` 中 `STAGE4_EQUIVALENCE_KEYS` 一致：

- `excluded_reasons`、`ranking`、`target_weights`、`cash_weight`、`top_symbol`
- `positions`、`orders`、`metrics`、`industry_by_symbol`

## 四分析师 CLI 行为（修复前）

| 键 | 终端 Agent 名 | 报告字段 |
|---|---|---|
| `market` | Market Analyst | `market_report` |
| `social` | Social Analyst | `sentiment_report` |
| `news` | News Analyst | `news_report` |
| `fundamentals` | Fundamentals Analyst | `fundamentals_report` |

`cli/utils.py` 选择器仍为上述 4 项；不含 policy / hot_money / lockup。

## Ruff 基线（修复前遗留）

命令：`PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts`

| 项 | 值 |
|---|---|
| 总错误数 | **134**（exit 1） |
| F401 unused-import | 62 |
| E402 module-import-not-at-top-of-file | 35 |
| F405 undefined-local-with-import-star-usage | 32 |
| F403 undefined-local-with-import-star | 3 |
| F841 unused-variable | 2 |

**专项约束：** 本修复不得新增 Ruff 错误；每个 Task 对自身改动文件 Ruff 必须为零错误。不得在本专项未完成时宣称全仓 Ruff 通过。

## Task 0 验收结论

- [x] 记录 HEAD、Schema 版本、全量测试数、阶段五验收结果和工作区状态
- [x] 冻结阶段四默认选股输出哈希
- [x] 冻结阶段五关闭时等价性字段口径
- [x] 冻结现有四分析师 CLI 映射
- [x] 保存 Ruff 遗留错误统计

**默认业务行为：零变化。**
