# 阶段 5 中期验收 B（Task 7 后）

> 日期：2026-06-20  
> 基线 commit（Task 6）：`f87d2515`  
> Task 7 commit：`4fb42665`  
> 状态：**待用户确认**

## 1. 验收范围

Pipeline / 报告 / CLI 集成可选事件增强，默认关闭时阶段 4 业务输出等价。

## 2. 自动化证据

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/ -q --capture=no
# 443 passed, 1 skipped（langchain_google_genai 可选依赖）

PYTHONPATH='.pip_packages:.' python3 -m ruff check \
  tradingagents/screener/event_enrichment.py \
  tradingagents/screener/pipeline.py tradingagents/screener/report.py \
  tradingagents/screener/cli.py tradingagents/market_data/cli.py \
  tradingagents/market_data/repository.py \
  tests/events/test_pipeline.py tests/events/test_event_cli.py \
  tests/screener/test_phase47.py
```

## 3. 检查项

| 项 | 结果 | 证据 |
|---|---|---|
| 阶段 4 等价性（`enabled=false`） | PASS | `tests/events/test_stage4_equivalence.py`、`test_disabled_enrichment_preserves_stage4_ranking_and_portfolio` |
| 三套排名并列输出 | PASS | `base_ranking` / `event_ranking` / `enhanced_ranking`；`ranking` 保持阶段 4 基础序 |
| PIT 防未来 | PASS | `test_future_events_do_not_affect_historical_enrichment`；Repository `available_at <= signal_time` |
| 可信硬风险过滤 | PASS | `test_hard_risk_flags_exclude_symbol_from_enhanced_ranking` |
| 降级与必需源失败 | PASS | `test_required_announcements_missing_records_enrichment_error`；非必需缺失保留 `ranking` |
| 仅查询 Top N 候选 | PASS | `test_repository_queries_only_top_candidates` |
| 贡献可审计 | PASS | `event_contributions`、`event_dataset_versions`、`event_data_sources` |
| CLI 集成 | PASS | `tests/events/test_event_cli.py`；`market-data sync --dataset events` |

## 4. 行为摘要

- **默认关闭**：`ranking`、权重、组合、回测与阶段 4 一致；事件侧车字段为空。
- **开启后**：组合仍由阶段 4 `ensemble_score` 构建；事件仅影响 `event_ranking` / `enhanced_ranking` 与审计字段。
- **数据路径**：Pipeline 只读 `MarketDataRepository.get_market_events()`，不隐式联网。
- **历史信号**：`CURRENT_ONLY` 热点数据集拒绝并记录 `event_degradations`。

## 5. 样例（fixture `mvp_market.json`，信号日 2025-12-18）

注入三只候选事件后（600001 回购利好 / 600002 处罚+软风险 / 600003 中性）：

- `ranking` == `base_ranking`（阶段 4 因子序）
- `event_ranking` 按 `event_score` 重排
- `enhanced_ranking` 融合 `event_weight=0.20` 后的序
- `event_contributions["600001"]` 含可逆算条目（`raw_impact`、`decay`、`weighted_impact`）

## 6. 未纳入本验收

- Task 8 分层验收脚本（`scripts/accept_event_enrichment.py`）
- 当日 free live smoke（属验收 B 的运营子项，Task 8）
- 模拟组合 / Scheduler 连续五日（阶段 6）

## 7. 结论

**中期验收 B：自动化项全部通过，请确认是否继续 Task 8。**
