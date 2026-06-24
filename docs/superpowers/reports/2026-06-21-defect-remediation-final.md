# 现有功能缺陷修复 — 最终验收报告

> 日期：2026-06-22（Stage 6A Task 0 门禁关闭版）
> HEAD：`eacfff6a`（见下方 Task 0 commit）
> 状态：**专项门禁已关闭** — 可进入 Stage 6A Task 1

## 摘要

专项 R0–R7、复审 P1 修复及 Stage 6A Task 0 收尾门禁均已完成。在不改变阶段编号、不引入多策略/Web/实盘的前提下，交付：

- 日频估值指标（Schema v11、免费 Provider、同步/CLI/调度）
- 真实库选股 CLI（`--fixture` 可选）
- 七分析师终端 Registry 与进度看板一致
- Mootdx 传输故障受控重连 + 关闭/调用生命周期安全
- Tier B mootdx **`run_mootdx_probe_subprocess` 可 kill 子进程硬超时**；网络不可达 **BLOCKED/exit 2**
- 分层最终验收脚本与 quickstart

相对 Task 0 基线：全量测试 **475 → 564 passed, 1 skipped**；`git diff --check 08af6011..HEAD` **通过**。

---

## Stage 6A Task 0 — 收尾门禁（2026-06-22）

| 项 | 结果 |
|---|---|
| `run_mootdx_probe_subprocess` | 新增；`subprocess.run(timeout=)` 返回 `{status: BLOCKED\|OK}` |
| 硬超时测试 | `test_mootdx_probe_timeout_returns_without_waiting_for_worker`（<1s 内返回 BLOCKED） |
| Tier A | **PASS / exit 0** |
| Tier B | **BLOCKED / exit 2**（Agent 环境网络受限；~5s 内完成，未挂起） |
| 全量 pytest | **564 passed, 1 skipped** |
| Ruff（改动文件） | **0 错误** |
| `git diff --check 08af6011..HEAD` | **通过** |

**与实施计划示例的差异（以设计为准）：** Task 0 计划示例 probe 使用 `stocks(market=0)`；实际保持 `ac609519` 的轻量 `bars(600000)` 探测，避免 bestip 扫描挂起。超时机制按设计采用可 kill 子进程。

---

## 专项 Commit 清单（`08af6011..HEAD`）

| Commit | 说明 |
|--------|------|
| `08af6011` | Task 0 基线冻结 |
| `a944c076` … `0de3c5f6` | Task 1–7 实现 |
| `fcd6cc4c` | P1 复审修复 |
| `4056db14` | P1-3 子进程硬超时（首版） |
| `ac609519` | Tier B 轻量 mootdx bars 探测 |
| `4036e280` | urllib 死代码清理 + 批间 jitter |
| `78929096` | 七分析师进度看板 Registry 化 |
| `34ed8938` | daily_indicators 接入选股因子 |
| `42143a9b` | chore: ignore `.worktrees/` |
| `eacfff6a` | **Stage 6A Task 0：run_mootdx_probe_subprocess 门禁关闭** |

中期验收：[A](2026-06-21-defect-remediation-acceptance-a.md) · [B](2026-06-21-defect-remediation-acceptance-b.md) · [基线](2026-06-21-defect-remediation-baseline.md)

---

## 分层验收

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --offline
MOOTDX_SKIP_BESTIP=1 MOOTDX_LIVE_SMOKE_TIMEOUT_SEC=60 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --live-smoke --network-mode system
git diff --check 08af6011..HEAD
```

### Tier B 编排规则

- 三项 **始终独立执行**
- 任一项 `network blocked` → 整体 **BLOCKED / exit 2**
- 非网络失败（如 repository `data_error`）→ **FAIL / exit 1**
- mootdx 子进程超时 → `run_mootdx_probe_subprocess` 返回 BLOCKED，**不等待 worker 退出**

---

## 宣告完成

1. R0–R7 + 复审 fix ✅
2. 两次中期验收 ✅
3. 全量回归 ✅（564 passed）
4. Tier A PASS ✅
5. Tier B 有网 PASS 或 BLOCKED/2（本环境 BLOCKED/2，~5s） ✅
6. Task 0 门禁 + `git diff --check` ✅

**专项正式关闭，可开始 Stage 6A Task 1。**
