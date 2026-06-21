# Existing-defects remediation quickstart

专项修复范围：日频估值指标、真实库选股 CLI、七分析师终端报告、Mootdx 受控重连。

实施计划：`docs/superpowers/plans/2026-06-21-existing-defects-remediation.md`

## Install

```bash
pip install -e ".[screener]"
```

离线验收与 Tier A 不需要 `TUSHARE_TOKEN` 或 live 网络。

## Tier A — 离线验收

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --offline
```

覆盖项：

| Step | 验证内容 |
|------|----------|
| `offline_schema_migration` | DuckDB 迁移幂等、`daily_indicators` 表 |
| `offline_provider_semantics` | 今日 `OK`、历史 `NOT_AVAILABLE_YET` |
| `offline_publish_idempotent` | 指标同步重复发布复用 `content_hash` |
| `offline_repository_screen` | 真实库路径选股（fixture 种子库） |
| `offline_fixture_cli_regression` | `--fixture` 模式零回归 |
| `offline_seven_analyst_registry` | 七分析师 Registry 映射 |
| `offline_mootdx_bounded_retry` | 传输错误最多重试一次 |

输出 JSON，`status=PASS` 时退出码 `0`。

## Tier B — Live smoke（可选）

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' \
  python3 scripts/accept_existing_defect_remediation.py --live-smoke \
  --home-dir /tmp/ta-accept-remediation-live
```

| Step | 验证内容 |
|------|----------|
| `live_tencent_indicators` | 腾讯指标 HTTP（600000） |
| `live_mootdx_connect` | mootdx TCP（`--probe-mootdx` 子进程；轻量 `bars(600000)` 探测，避免 `stocks()` 触发 bestip 扫描） |
| `live_repository_screen` | 真实库选股路径（fixture 种子库，不依赖外网） |

三项 **独立执行**；任一项网络不可达映射为 `network blocked` → 整体 **BLOCKED/exit 2**（非 FAIL）。腾讯失败时仍会探测 mootdx 并跑 repository。

## 全量回归

```bash
MOOTDX_SKIP_BESTIP=1 PYTHONPATH='.pip_packages:.' python3 -m pytest tests -q --capture=no
PYTHONPATH='.pip_packages:.' python3 -m ruff check tradingagents cli tests scripts
git diff --check
git status --short
```

Ruff：相对 Task 0 基线不得新增错误；本专项改动文件应为 0 错误。

## 相关 CLI

```bash
# 真实库选股（无 --fixture）
PYTHONPATH='.pip_packages:.' python3 -m tradingagents.screener.cli screen \
  --home-dir ~/.tradingagents \
  --config config/screener.example.yaml \
  --universe custom --symbols 600001

# 日频指标同步
PYTHONPATH='.pip_packages:.' python3 -m tradingagents.market_data.cli sync \
  --dataset daily-indicators --home-dir ~/.tradingagents
```

## CI 门禁

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/remediation/test_remediation_acceptance.py -q
```

该测试调用验收脚本（不内嵌 pytest 于脚本本身）。
