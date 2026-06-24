# Stage 6A Tier C — 五个真实交易日观测 Checklist

Tier C 是**合入后的运营观察**，不是代码验收。目标：在真实 A 股交易日跑通 paper 闭环，并收集 **5 个不同 open date** 的 `daily_manifest.json`。

## 目录与环境

```bash
export HOME_DIR=~/.tradingagents
export OBS_DIR=$HOME_DIR/observations/stage6a
export ACCOUNT_ID=demo
mkdir -p "$OBS_DIR"
```

## 一次性准备（Day 0）

```bash
cd /path/to/TradingAgents-astock
pip install -e ".[screener,dev]"

tradingagents-market-data init --home-dir "$HOME_DIR" --provider free
tradingagents-paper init --account-id "$ACCOUNT_ID" --home-dir "$HOME_DIR"
```

## 每个交易日 T（重复直到凑齐 5 个不同日期）

### 1. 开盘 job（T 日）

```bash
export TRADE_DATE=YYYY-MM-DD   # 真实交易日

tradingagents-scheduler run-open \
  --trade-date "$TRADE_DATE" \
  --account-id "$ACCOUNT_ID" \
  --home-dir "$HOME_DIR" \
  --provider free \
  | tee "$OBS_DIR/$TRADE_DATE/run-open.json"
echo "run-open exit: $?"
```

### 2. 盘后 job（T 日）

```bash
tradingagents-scheduler run-after-close \
  --trade-date "$TRADE_DATE" \
  --account-id "$ACCOUNT_ID" \
  --home-dir "$HOME_DIR" \
  --provider free \
  | tee "$OBS_DIR/$TRADE_DATE/run-after-close.json"
echo "run-after-close exit: $?"
```

### 3. 只读核对

```bash
tradingagents-paper status \
  --account-id "$ACCOUNT_ID" \
  --home-dir "$HOME_DIR"
```

确认：现金/持仓非负、无 duplicate fill、无 unexplained NAV 偏差。若需 recovery：

```bash
tradingagents-scheduler recover \
  --trade-date "$TRADE_DATE" \
  --account-id "$ACCOUNT_ID" \
  --home-dir "$HOME_DIR"
```

### 4. 生成 daily_manifest.json

```bash
mkdir -p "$OBS_DIR/$TRADE_DATE"

python3 scripts/collect_stage6a_daily_manifest.py \
  --observation-dir "$OBS_DIR" \
  --trade-date "$TRADE_DATE" \
  --home-dir "$HOME_DIR" \
  --account-id "$ACCOUNT_ID" \
  --run-open "$OBS_DIR/$TRADE_DATE/run-open.json" \
  --run-after-close "$OBS_DIR/$TRADE_DATE/run-after-close.json"
```

或使用包装脚本：

```bash
./scripts/collect_daily_manifest.sh \
  --observation-dir "$OBS_DIR" \
  --trade-date "$TRADE_DATE" \
  --home-dir "$HOME_DIR" \
  --run-open "$OBS_DIR/$TRADE_DATE/run-open.json" \
  --run-after-close "$OBS_DIR/$TRADE_DATE/run-after-close.json"
```

脚本会写入 `$OBS_DIR/$TRADE_DATE/daily_manifest.json`。

### 5. 当日打勾

| 检查项 | ✓ |
|--------|---|
| `run-open` 已执行，JSON 已保存 | |
| `run-after-close` 已执行，JSON 已保存 | |
| `daily_manifest.json` 已生成 | |
| 账本 invariant 无异常 | |
| exit `2` 已在 manifest `steps` 中体现为 blocked | |

**Exit code 语义：** `0` 成功，`1` 本地/数据错误，`2` 外部网络 BLOCKED（可接受，需在 manifest 中记录）。

## 凑齐 5 日后汇总

```bash
PYTHONPATH='.pip_packages:.' python3 scripts/summarize_stage6a_observation.py "$OBS_DIR"
```

**Tier C PASS 条件：**

- `distinct_open_dates >= 5`
- 最近 5 条 manifest 的 `status` 均为 `"completed"`
- 命令 exit code `0`，JSON 中 `"passed": true`

## 相关文档

- 快速上手：`docs/stage6a-paper-quickstart.md`
- 最终验收说明：`docs/superpowers/reports/2026-06-22-stage6a-final-acceptance.md`
