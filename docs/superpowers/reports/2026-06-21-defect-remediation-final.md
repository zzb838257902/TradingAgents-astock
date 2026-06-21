# 现有功能缺陷修复 — 最终验收报告

> 日期：2026-06-21  
> HEAD：`0de3c5f6`  
> 状态：**自动化门禁已通过** — 待人工独立 diff 审核签字

## 摘要

专项 R0–R7 已全部提交。在不改变阶段编号、不引入多策略/Web/实盘的前提下，完成：

- 日频估值指标（Schema v11、免费 Provider、同步/CLI/调度）
- 真实库选股 CLI（`--fixture` 可选）
- 七分析师终端 Registry 与报告保存一致
- Mootdx 传输故障受控重连（最多一次）
- 分层最终验收脚本与 quickstart

相对 Task 0 基线：**全量测试 +74**（475 → 549）；**Ruff 全仓 -7**（134 → 127，无新增）；阶段四 fixture 选股输出哈希 **未变**。

---

## 专项 Commit 清单

自 Task 0 基线 `08af6011` 起，`08af6011..HEAD` 共 **10** 个后续 commit；含 Task 0 共 **11** 个 commit：

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

中期验收记录：

- [中期验收 A](2026-06-21-defect-remediation-acceptance-a.md)
- [中期验收 B](2026-06-21-defect-remediation-acceptance-b.md)
- [Task 0 基线](2026-06-21-defect-remediation-baseline.md)

---

## 全量回归

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no
```

| 项 | Task 0 基线 | 最终 |
|---|---|---|
| 全量 | 475 passed, 1 skipped | **549 passed, 1 skipped** |
| 新增专项测试 | `tests/remediation/test_baseline.py` | + `test_remediation_acceptance.py`、`test_mootdx_connection.py`、指标/选股/CLI 等 |

专项子集（指标 + 选股 + Registry + Mootdx + remediation）：

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest \
  tests/remediation/ tests/dataflows/ tests/screener/test_screener_live_cli.py \
  tests/cli/ tests/market_data/test_daily_indicators_*.py -q
```

| 结果 | **73 passed** |

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

| Step | 验证点 |
|------|--------|
| `offline_schema_migration` | Schema v11、`daily_indicators` 表、迁移幂等 |
| `offline_provider_semantics` | 今日 `OK`、历史 `NOT_AVAILABLE_YET` |
| `offline_publish_idempotent` | 指标同步重复发布复用 `content_hash` |
| `offline_repository_screen` | 真实库路径选股 `status=ok` |
| `offline_fixture_cli_regression` | `--fixture` 模式 fixture SHA256 不变 |
| `offline_seven_analyst_registry` | 7 分析师顺序与 `sentiment_report` 回归 |
| `offline_mootdx_bounded_retry` | 传输错误最多重试 1 次；解析错误不重试 |

### Tier B — Live smoke（人工 / 有网环境）

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --live-smoke \
  --home-dir /tmp/ta-accept-remediation-live
```

| 项 | CI 沙箱 | 期望（有网） |
|---|---|---|
| `status` | **BLOCKED**（DNS/网络受限） | **PASS** |
| `exit_code` | **2** | **0** |

网络不可达时必须 **BLOCKED**，不得假绿 **PASS**。人工签字前请在可访问腾讯 / mootdx 的环境复跑 Tier B。

---

## Ruff

命令：`PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts`

| 项 | Task 0 基线 | 最终 |
|---|---|---|
| 全仓错误数 | 134 | **127**（净减 7） |
| 新增错误 | — | **0** |
| `cli/main.py` | 35（既有 E402/F405 等） | **35**（未新增） |

本专项核心改动文件（抽样）：**0 错误**

- `tradingagents/dataflows/mootdx_connection.py`
- `tradingagents/screener/pipeline.py`
- `cli/analyst_registry.py`
- `scripts/accept_existing_defect_remediation.py`
- Task 1–5 指标/选股相关改动文件

**未宣称全仓 Ruff 通过**；既有债务见 Task 0 基线报告。

---

## 最终验收标准对照

### 数据层

| 标准 | 状态 |
|------|------|
| Schema v10→v11 原子升级，失败可回滚 | ✅ `test_failed_migration_does_not_advance_schema_version` |
| 免费路径不读 `TUSHARE_TOKEN` | ✅ Provider/Sync 测试 + 离线验收 |
| 今日指标 canonical CNY 单位 | ✅ `test_normalize_tencent_row_converts_yi_to_cny` 等 |
| 历史请求 `NOT_AVAILABLE_YET` | ✅ 离线验收 `offline_provider_semantics` |
| 网络/空路径不发布假版本 | ✅ 中期 A 修复 + sync 测试 |
| 相同数据重跑复用版本 | ✅ `offline_publish_idempotent` |
| 指标缺失不破坏基础选股 | ✅ Scheduler 降级测试 + 选股不依赖指标 |

### Live 选股

| 标准 | 状态 |
|------|------|
| 不传 `--fixture` 可读真实库 | ✅ `test_screener_live_cli.py` + `offline_repository_screen` |
| 显式历史 `--as-of` 不用未来数据 | ✅ 历史 PIT 测试 |
| 空库/数据不足结构化错误 | ✅ 空库、信号日缺失测试 |
| `--fixture` 零回归 | ✅ 冻结 SHA256 + `offline_fixture_cli_regression` |

### 七分析师 CLI

| 标准 | 状态 |
|------|------|
| 七角色可选、展示、保存 | ✅ `tests/cli/test_complete_reports.py` |
| 字段名与 Graph 一致 | ✅ `test_analyst_registry.py` |
| 固定团队报告独立 | ✅ Registry 未覆盖 investment_plan 等 |
| 原四角色行为不变 | ✅ `social → sentiment_report` 回归 |

### Mootdx

| 标准 | 状态 |
|------|------|
| 传输故障只重连一次 | ✅ `test_mootdx_connection.py` + 离线验收 |
| 非网络错误不重试 | ✅ |
| 并发单例 | ✅ `test_concurrent_calls_share_one_client` |
| 连续失败可观测、无死循环 | ✅ `test_consecutive_transport_errors_raise_without_loop` |

---

## 零回归确认

| 项 | 值 / 结论 |
|---|---|
| MVP fixture SHA256 | `42e43a4ba99c8d81812aaa0fb875d2f70072e5555f984a1cadd0680ad6731b6e`（未变） |
| 默认选股输出 SHA256 | `339f144bf7120d678a5c86e78b801fe99492c6c70732131ffdbce8800545381b`（未变） |
| 阶段五关闭等价字段 | `tests/remediation/test_baseline.py` 通过 |

---

## 已知限制与遗留

| 项 | 说明 |
|---|---|
| Tier B live smoke | CI 沙箱网络受限 → **BLOCKED**；生产签字需人工有网复验 |
| 全仓 Ruff | 127 条既有债务（ mainly `cli/main.py` import-star） |
| 实施计划文件 | `docs/superpowers/plans/2026-06-21-existing-defects-remediation.md` 仍为未跟踪本地文档 |
| 历史估值回填 | 按范围 **不做** |
| Tushare 默认路径 | 按范围 **不改** |

---

## 独立审核清单（人工）

请在完整 diff（`08af6011..0de3c5f6`）上确认并签字：

- [ ] 无新增 **P0**（数据假绿、PIT 泄漏、静默回退、双重重试）
- [ ] 无新增 **P1**（契约漂移、CLI/Graph 字段不一致、未文档化行为变更）
- [ ] Tier B live smoke 在有网环境 **PASS** 或记录外部 **BLOCKED** 原因
- [ ] 不提交 Token、DuckDB、缓存或原始响应

### 已关闭的历史阻塞项（复审保留）

| 阶段 | 级别 | 问题 | 修复 commit |
|------|------|------|-------------|
| 中期 A | P0 | 空响应误 `SUCCESS_EMPTY` | `04c3e8a9` |
| 中期 B | P0 | 信号日行情缺失静默用 `history[-1]` | `2d36cef0` |
| 中期 B | P0 | 历史 `trade_date` 信号漂移到 today | `2d36cef0` |
| 中期 B | P1 | 流式保存 `policy_report.md` 双轨 | `2d36cef0` |

**当前自动化审核：未发现未修复的 P0/P1。**

---

## 宣告完成条件

仅当以下全部满足时，可对外宣称「本次专项缺陷修复完成」：

1. R0–R7 全部提交 ✅  
2. 两次中期验收确认 ✅  
3. 全量回归 549 passed ✅  
4. Tier A 离线验收 PASS ✅  
5. Tier B 人工 live 签字 ⏳  
6. 独立 diff 审核无新增 P0/P1 ⏳  

---

## 快速复验命令

```bash
# 全量
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no

# 分层验收
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --offline
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --live-smoke

# 卫生检查
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts
git diff --check
git status --short
```
