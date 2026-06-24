# 现有功能缺陷修复 — 中期验收 B（复审）

> 日期：2026-06-21
> 状态：**已通过复审** — 阻塞项已修复并提交 `2d36cef0`

## 专项 Commit 清单

自 Task 0 基线 `08af6011` 起，专项共 **8** 个 commit（`08af6011..HEAD` 范围为 **7** 个后续 commit）：

| Commit | 说明 |
|--------|------|
| `08af6011` | Task 0：冻结基线 |
| `a944c076` | Task 1：DailyIndicator schema |
| `9edd2d48` | Task 2：免费 Provider |
| `1d34733e` | Task 3：Repository / 同步 / CLI |
| `04c3e8a9` | 中期验收 A 修复 |
| `588dd8ed` | Task 4：真实库选股 CLI |
| `7a307de2` | Task 5：七分析师 Registry |
| `2d36cef0` | 中期验收 B 修复（信号日门禁、报告文件名、指数/缺失测试） |

## 全量回归

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no
```

| 项 | 结果 |
|---|---|
| 全量 | **538 passed, 1 skipped** |

## 专项测试

| 范围 | 结果 |
|------|------|
| `tests/screener/test_screener_live_cli.py` | 14 passed（含指数、信号日缺失） |
| `tests/cli/` | 13 passed |
| Task 4 + CLI 合计 | 27 passed |

## Ruff

命令：`PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts`

| 项 | Task 0 基线 | 当前 |
|---|---|---|
| 全仓错误数 | 134 | **127**（净减 7，来自 `cli/models.py` / `cli/utils.py` 清理） |
| 新增错误 | — | **0** |
| `cli/main.py` | 35（既有 E402/F405/F841 等） | **35**（未新增；本专项未宣称该文件清零） |
| 本次改动核心文件 | — | `pipeline.py`、`jobs.py`、`analyst_registry.py`、`tests/screener/`、`tests/cli/` **0 错误** |

## 已通过项（复审保留）

- 真实库 CLI 正常路径、空库、空股票池、历史 `--as-of`
- 七分析师 Registry、状态更新、七份最终报告（`1_analysts/*.md`）
- Fixture 输出哈希不变（`test_fixture_screen_cli_output_is_unchanged`）
- 原四分析师 `social → sentiment_report` 映射回归

## 真实库 CLI 样例（fixture 库）

```bash
PYTHONPATH='.pip_packages:.' python3 -m tradingagents.screener.cli screen \
  --home-dir /tmp/ta-screen \
  --config config/screener.example.yaml \
  --as-of 2026-01-03T15:30:00+08:00 \
  --universe custom --symbols 600001
# 期望：source=repository, status=ok（fixture 库已 seed）

# 删除信号日行情后应失败：
# DELETE FROM daily_bars WHERE trade_date = '2026-01-03'
# 期望：status=data_error, errors 含 missing published quotes for signal date
```

**中期验收 B 已通过；继续 Task 6。**

---

## Stage 6A Task 0 追加（2026-06-22）

专项收尾门禁已关闭（killable mootdx 子进程、`BLOCKED/2` 状态机、564 pytest）；详见 [最终验收报告](2026-06-21-defect-remediation-final.md)。

## 首轮阻塞项与修复（`2d36cef0`）

| 级别 | 问题 | 修复 |
|------|------|------|
| P0 | 交易日历已到信号日，但信号日行情缺失时仍用上一日 K 线完成选股（假绿 `status=ok`） | `pipeline.py` 增加 `_signal_day_bar` 门禁；目标宇宙任一标的缺信号日已发布行情 → `data_error`；移除 `history[-1]` 静默回退 |
| P0 连带 | 调度 `run_after_close` 对历史 `trade_date` 使用 `max(..., now)` 导致信号日漂移到今天 | `jobs.py` 仅在 `trade_date == shanghai_today()` 时才与 `now` 取 max |
| P1 | 运行中自动保存仍写 `policy_report.md`，与 Registry 的 `policy.md` 双轨 | `report_section_output_filename()` 统一流式保存文件名 |
| 缺口 | Task 4 缺指数成分股测试 | `test_repository_screen_index` |
| 缺口 | 缺信号日行情缺失回归测试 | `test_missing_signal_day_quotes_returns_data_error` |
