# 阶段 5 中期验收 B（已通过）

> 日期：2026-06-20  
> 状态：**已通过** — Task 8 验收脚本与 quickstart 已落地

## Task 8 交付

| 文件 | 说明 |
|------|------|
| `scripts/accept_event_enrichment.py` | 分层验收：`--offline` / `--recorded-contract` / `--live-smoke` |
| `docs/event-data-quickstart.md` | 事件同步、增强配置、验收层级说明 |
| `tests/events/test_acceptance.py` | 验收脚本集成测试（不内嵌 pytest） |

## 验收命令

```bash
PYTHONPATH='.pip_packages:.' python3 scripts/accept_event_enrichment.py --offline --recorded-contract
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/events/test_acceptance.py -q
```

## 第三轮修复（0268aa73 / 9dac54c4）

| 级别 | 问题 | 修复 |
|------|------|------|
| P0 | 未来空同步可放行历史筛选（PIT 前视） | PIT 查询 + 全量候选 + 窗口覆盖 |
| P1 | 空 bundle 哈希不含查询范围 | 稳定 coverage 字段纳入哈希 |
| P1 | 空同步重跑不幂等（`as_of` 污染哈希） | 哈希仅含 dataset/symbols/start/end/success_empty |

## 验证

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/ -q --capture=no
# 467+ passed, 1 skipped
```

## 结论

中期验收 B 已通过。阶段 5 完成条件：Tier A 通过、Tier B 通过或明确 `BLOCKED`。
