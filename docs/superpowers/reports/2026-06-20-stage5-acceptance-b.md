# 阶段 5 中期验收 B（第二轮修复）

> 日期：2026-06-20  
> 状态：**待用户确认**

## 本轮修复

| 级别 | 问题 | 修复 |
|------|------|------|
| P0 | 全部候选硬风险过滤后 `KeyError: score` | 空 `portfolio_ranking` 时生成全现金组合（`cash_weight=1.0`），不调用 `construct_portfolio` |
| P1 | 同标题同时间修订被语义去重 | 语义键加入 `source_version` |
| P1 | SUCCESS_EMPTY 无 version_id | 创建空 ingestion run 并发布空 bundle 版本 |
| P1 | 撤销 ST/终止调查误标 CRITICAL | `infer_severity` 识别正向解除语义 → LOW |
| P1 | 修订双重计分 | `_effective_revision_events` 仅保留未被 supersede 的最新可见版本 |

## 新增测试

- `test_all_candidates_hard_risk_filtered_produces_all_cash_portfolio`
- `test_dedup_keeps_same_title_and_time_with_different_version`
- `test_event_sync_success_empty_publishes_empty_version`
- `test_infer_severity_treats_relief_announcements_as_low`
- `test_revision_scoring_uses_latest_visible_version_only`
- `test_st_relief_does_not_trigger_hard_risk_exclusion`

## 验证

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/ -q --capture=no
# 455 passed, 1 skipped
```

## 结论

P0 与剩余 P1 已修复。**请确认通过中期验收 B 并继续 Task 8。**
