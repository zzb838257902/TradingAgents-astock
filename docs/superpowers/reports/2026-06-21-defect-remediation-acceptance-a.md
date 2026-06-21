# 现有功能缺陷修复 — 中期验收 A（复审）

> 日期：2026-06-21  
> 状态：**待复审** — 已修复首轮验收问题

## 首轮问题修复

| 级别 | 问题 | 修复 |
|------|------|------|
| P0 | 空证券列表/空腾讯响应/全部解析失败误发 `SUCCESS_EMPTY` | `free_astock` 仅返回 `OK` 或 `PARSE_ERROR`/`DATA_QUALITY_FAILED`；`sync` 仅 `fixture` Provider 可发布 `SUCCESS_EMPTY` |
| P1 | Schema 回归测试仍断言 v10 | `test_event_repository.py` 更新为 v11 |
| P1 | 非交易日可通过 CLI 同步 | `sync_daily_indicators` 增加 `_require_open_trade_date`；CLI 默认日期改用 `shanghai_today()` |
| P1 | 未发布记录可被查询 | `get_daily_indicators` 改为 `INNER JOIN` + `v.status = 'PUBLISHED'` |
| P2 | 生产默认无批间等待 | 默认 `batch_pause=0.3`，新增默认正值测试 |

## 全量回归

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no
```

| 项 | 结果 |
|---|---|
| 全量 | **512 passed, 1 skipped** |

## 专项测试（指标相关）

| 范围 | 结果 |
|------|------|
| Provider + Sync + Schema | 26 passed |

## Ruff（涉及文件）

涉及文件 **0 错误**（全仓基线 134 既有错误未变）。

## 真实数据验证

腾讯 smoke（600000、000001）仍可用；空响应路径已改为 `PARSE_ERROR`，不再发布。

## Commit

待提交：`fix(market-data): harden daily indicator empty-path guards`

**请复审中期验收 A；通过后继续 Task 4。**
