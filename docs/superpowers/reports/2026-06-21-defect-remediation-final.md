# 现有功能缺陷修复 — 最终验收报告

> 日期：2026-06-21（复审修复版）
> HEAD：`c2e16df4`（代码修复 `fcd6cc4c`）
> 状态：**P1 复审修复已提交** — 待复跑 Tier B 与独立 diff 复审

## 摘要

专项 R0–R7 已全部提交，并完成首轮独立审核提出的 **3 类 P1** 修复。在不改变阶段编号、不引入多策略/Web/实盘的前提下，完成：

- 日频估值指标（Schema v11、免费 Provider、同步/CLI/调度）
- 真实库选股 CLI（`--fixture` 可选）
- 七分析师终端 Registry 与报告保存一致
- Mootdx 传输故障受控重连（最多一次）+ 关闭/调用生命周期安全
- 分层最终验收脚本与 quickstart

相对 Task 0 基线：全量测试 **475 → 553**；Ruff 全仓 **134 → 127**（无新增）；阶段四 fixture 选股输出哈希 **未变**。

---

## 首轮审核结论（2026-06-21）

| 结论 | 说明 |
|------|------|
| 总体 | **暂不通过** — 不可宣告专项完成 |
| P0 | 未发现新增 |
| P1 | 3 类（见下表） |

| ID | 问题 | 修复 |
|----|------|------|
| P1-1 | `call()` 释放锁后 `close()` 可与活跃 I/O 竞态 | `_active_calls` + 延迟 `_clients_to_close`；新增 `test_close_waits_until_active_operation_finishes` |
| P1-2 | `_build_name_code_map()` 绕过 `MootdxConnectionManager` | 改为 `get_mootdx_manager().call(lambda c: c.stocks(...))` |
| P1-3 | Tier B 仅腾讯失败算 BLOCKED；mootdx/repository 被跳过；线程超时不可靠 | 三项独立执行；`--probe-mootdx` **独立子进程** + `subprocess.run(timeout=)` 硬超时；`test_run_probe_subprocess_timeout_is_hard` |

---

## 专项 Commit 清单

自 Task 0 基线 `08af6011` 起（含 Task 0 与复审 fix commit，提交后更新 HEAD）：

| Commit | 说明 |
|--------|------|
| `08af6011` | Task 0：冻结基线 |
| `a944c076` | Task 1：DailyIndicator schema v11 |
| `9edd2d48` | Task 2：免费 Provider |
| `1d34733e` | Task 3：Repository / 同步 / CLI / 调度 |
| `04c3e8a9` | 中期验收 A 修复 |
| `588dd8ed` | Task 4：真实库选股 CLI |
| `7a307de2` | Task 5：七分析师 Registry |
| `2d36cef0` | 中期验收 B 修复 |
| `e998af2b` | 验收 B 文档对齐 |
| `f2591ed0` | Task 6：Mootdx 受控重连 |
| `0de3c5f6` | Task 7：分层最终验收 |
| `a8782490` | 最终验收报告（首版） |
| `fcd6cc4c` | P1 复审修复（mootdx 生命周期、name map、Tier B 状态机） |
| *(本次提交)* | P1-3 子进程硬超时 + 文档行尾空格清理 |

中期验收记录：

- [中期验收 A](2026-06-21-defect-remediation-acceptance-a.md)
- [中期验收 B](2026-06-21-defect-remediation-acceptance-b.md)
- [Task 0 基线](2026-06-21-defect-remediation-baseline.md)

---

## 全量回归

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no
```

| 项 | Task 0 基线 | 复审修复后 |
|---|---|---|
| 全量 | 475 passed, 1 skipped | **553 passed, 1 skipped** |
| 专项子集 | — | **76 passed**（remediation + dataflows + 指标/选股/CLI） |

---

## 分层验收（Task 7）

脚本：`scripts/accept_existing_defect_remediation.py`
文档：`docs/defect-remediation-quickstart.md`
CI 门禁：`tests/remediation/test_remediation_acceptance.py`

### Tier A — 离线（必须通过）

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --offline
```

| 项 | 结果 |
|---|---|
| `status` | **PASS** |
| `exit_code` | **0** |
| 步骤 | 7/7 通过 |

### Tier B — Live smoke（三项独立）

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --live-smoke \
  --home-dir /tmp/ta-accept-remediation-live
```

| Step | 说明 |
|------|------|
| `live_tencent_indicators` | 腾讯 HTTP |
| `live_mootdx_connect` | mootdx TCP（`--probe-mootdx` 独立子进程；`MOOTDX_LIVE_SMOKE_TIMEOUT_SEC` 默认 60s，`subprocess` 硬超时） |
| `live_repository_screen` | 本地 fixture 库选股（不依赖外网） |

**编排规则（P1-3 修复后）：**

- 三项 **始终独立执行**（腾讯失败不跳过 mootdx/repository）
- 任一项网络不可达 → 该项 `AssertionError: network blocked: ...`
- 全部失败均为 network blocked → **`status=BLOCKED`, exit 2**
- 非网络失败（如 repository `data_error`）→ **`status=FAIL`, exit 1**

离线状态机测试（不依赖外网）：

- `test_compute_report_status_tencent_ok_mootdx_blocked`
- `test_compute_report_status_tencent_blocked_still_runs_other_steps`
- `test_compute_report_status_repository_failure_is_fail`

---

## Ruff

命令：`PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts`

| 项 | Task 0 基线 | 当前 |
|---|---|---|
| 全仓错误数 | 134 | **127**（净减 7，无新增） |
| `cli/main.py` | 35 | **35**（未新增） |

本专项核心改动文件：**0 错误**（含 `mootdx_connection.py`、`accept_existing_defect_remediation.py`）。

---

## Mootdx 生命周期（P1-1 修复要点）

- `_active_calls` 计数：活跃 `call()` 期间 `invalidate()`/`close()` **延迟关闭**至 `_clients_to_close`
- 传输错误重试：在 `finally` 中递减计数并 drain 待关闭客户端
- 测试：`test_close_waits_until_active_operation_finishes` 验证 `operation_observed_closed=False`

---

## 独立审核清单（复审）

请在 `08af6011..HEAD` 完整 diff 上确认：

- [ ] P1-1：`close()` 与活跃 `call()` 无竞态（见 mootdx 测试）
- [ ] P1-2：`_build_name_code_map()` 仅经 `MootdxConnectionManager`
- [ ] P1-3：Tier B 三项独立；腾讯-only/mootdx-only 网络失败均为 BLOCKED
- [ ] 无新增 P0
- [ ] Tier B 有网环境 PASS 或记录 BLOCKED 原因
- [ ] `git diff --check` 通过（无文档行尾空格）

---

## 宣告完成条件

1. R0–R7 + 复审 fix 全部提交 ✅
2. 两次中期验收确认 ✅
3. 全量回归通过 ✅
4. Tier A PASS ✅
5. Tier B 有网签字 ⏳
6. 独立 diff 复审无 P0/P1 ⏳

---

## 快速复验命令

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --offline
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --live-smoke
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts
git diff --check
```
