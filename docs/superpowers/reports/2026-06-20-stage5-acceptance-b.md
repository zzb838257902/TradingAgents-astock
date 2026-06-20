# 阶段 5 中期验收 B（第三轮修复）

> 日期：2026-06-20  
> 状态：**待用户确认**

## 本轮修复

| 级别 | 问题 | 修复 |
|------|------|------|
| P0 | 未来空同步可放行历史筛选（PIT 前视） | `has_success_empty_announcement_sync` 改为 PIT 查询：`published_at <= signal_time`、候选全量覆盖、`start/end` 覆盖事件查询窗口 |
| P1 | 空 bundle 哈希不含查询范围 | 空 bundle 的 `content_hash` 纳入 ingestion `params`（symbols/start/end） |

## 新增/更新测试

- `test_required_announcements_satisfied_by_success_empty_sync`（回灌 `published_at` 与日期窗口）
- `test_future_empty_sync_does_not_satisfy_historical_signal`
- `test_partial_candidate_coverage_empty_sync_fails`
- `test_empty_bundle_hash_includes_query_params`
- `test_success_empty_announcement_sync_requires_pit_coverage`

## 验证

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/ -q --capture=no
# 462 passed, 1 skipped
```

## 结论

P0 前视问题已修复；空同步覆盖证据与查询范围已对齐。**请确认通过中期验收 B 并继续 Task 8。**

## 历史轮次（a0200e68）

| 级别 | 问题 | 修复 |
|------|------|------|
| P0 | 全部候选硬风险过滤后 `KeyError: score` | 空 `portfolio_ranking` 时生成全现金组合 |
| P1 | 同标题同时间修订被语义去重 | 语义键加入 `source_version` |
| P1 | SUCCESS_EMPTY 无 version_id | 创建空 ingestion run 并发布空 bundle 版本 |
| P1 | 撤销 ST/终止调查误标 CRITICAL | `infer_severity` 识别正向解除语义 → LOW |
| P1 | 修订双重计分 | `_effective_revision_events` 仅保留未被 supersede 的最新可见版本 |
