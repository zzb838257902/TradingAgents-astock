# 阶段 5 中期验收 B（Task 7 后，P0/P1 修复版）

> 日期：2026-06-20  
> 状态：**待用户确认**

## 1. P0/P1 修复摘要

| 级别 | 问题 | 修复 |
|------|------|------|
| P0 | 事件增强未参与组合构建 | Pipeline 改为：基础排名 → 事件增强/硬风险过滤 → Portfolio → 回测；`ranking`/`base_ranking` 保留为审计字段 |
| P0 | 硬风险股票仍在增强排名末尾 | `sort_enhanced_ranking` 完全排除硬风险；组合候选同步排除 |
| P0 | 必需源失败仍 `status=ok` | 必需数据集缺失时返回 `ScreeningStatus.DATA_ERROR`，不生成权重/订单 |
| P1 | 修订公告被 URL 去重误删 | URL 去重键加入 `source_version`；record 键使用 `stable_event_id` |
| P1 | Repository 可读 NULL 版本 | `get_market_events` / `get_event_tags` 强制 `INNER JOIN` 已发布版本 |
| P1 | `event_score=0` 被当作缺失 | 专用排序键，不再使用 `or float("-inf")` |
| P1 | 公告 severity 恒为 MEDIUM | 新增 `infer_severity()`，ST/立案/重大处罚/长期停牌可触发 CRITICAL |
| P1 | SUCCESS_EMPTY 被标 BLOCKED | 合法空结果返回 `EventSyncStatus.PUBLISHED` |
| P1 | 四类数据集共用同一版本 | 仅对实际存在的数据集填充 `event_dataset_versions` |

## 2. 自动化证据

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/ -q --capture=no
# 449 passed, 1 skipped

PYTHONPATH='.pip_packages:.' python3 -m ruff check \
  tradingagents/events tradingagents/screener/event_enrichment.py \
  tradingagents/screener/pipeline.py tradingagents/market_data/repository.py \
  tests/events/
```

## 3. 关键测试

- `test_portfolio_reflects_hard_risk_filter`：硬风险开/关组合权重变化，过滤后零持仓
- `test_hard_risk_excludes_symbol_from_ranking_and_portfolio`：增强排名与 `target_weights` 均不含硬风险股
- `test_required_announcements_missing_returns_data_error_without_portfolio`：`DATA_ERROR` + 空权重/订单
- `test_revision_events_both_readable_after_publish` / `test_dedup_keeps_revision_with_new_source_version`：修订共存
- `test_disabled_enrichment_preserves_stage4_ranking_and_portfolio`：关闭增强阶段 4 等价

## 4. 结论

P0/P1 已修复并补充验证测试。**请确认是否通过中期验收 B 并继续 Task 8。**
