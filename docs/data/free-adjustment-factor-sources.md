# 免费复权因子数据源调研

> 日期：2026-06-20  
> 状态：已确认方向 — **正式 PIT 路径用 mootdx `xdxr` 公司行为；新浪 `qfq.js` 仅作交叉验证 / 探索**

## 目标

为动量因子提供 `price_basis=forward_adjusted`（前复权等价）序列，且满足：

- 每条因子变更带 `available_at`（不得在事件公布前可见）
- 成交与涨跌停仍用**未复权**真实价格（`limits.py`）
- 不得用「今日下载的全历史新浪复权因子」直接冒充历史 PIT

## 候选源对比

| 来源 | 协议 | 历史深度 | PIT 可用性 | 许可 | 稳定性 | 结论 |
|------|------|----------|------------|------|--------|------|
| **mootdx `xdxr`** | TCP 7709 | 全历史除权除息 | **可用** — 按 `ex_date` 设 `available_at` | 开源客户端 + 通达信协议 | 中（需配置行情服务器） | **正式免费主路径** |
| **新浪 `qfq.js` / `hfq.js`** | HTTP | 全历史因子曲线 | **不可用作正式 PIT** — 曲线随今日重算 | 公开页面 | 中 | 仅 `BEST_EFFORT` 交叉验证 |
| **mootdx `utils.adjust.fq_factor`** | HTTP→新浪 | 同新浪 | 同新浪 | 同新浪 | 中 | 包装层，不提升 PIT |
| **东财 push2** | HTTP | 无独立因子 API | 需从公司行为推导 | 公开 | 中 | 备用事件来源（未接入） |
| **Tushare `adj_factor`** | 付费 API | 全市场 | 可用（需 Token + 审计） | 积分/许可 | 高 | **可选增强** |

## 推荐架构

```text
mootdx.xdxr(symbol)
  → parse category==1 除权除息行
  → corporate_actions 表（ex_date, 派息/送转/配股字段, available_at）
  → adjustments.build_pit_adjustment_rows() 生成 ex_date 因子阶跃
  → adjustment_factors 表（symbol, trade_date=ex_date, factor, available_at）

查询层：对任意 trade_date 取最近 ex_date 的 factor（forward-fill）
动量：adjusted_close = raw_close / factor   # 前复权等价
```

## PIT 规则（强制）

1. `corporate_actions.available_at` ≥ 除权除息日收盘后（默认 `ex_date 15:00 +08:00`）。
2. `adjustment_factors` 仅在 `ex_date` 写入阶跃；禁止把未来事件回填到过去 `available_at`。
3. 新浪 `qfq.js` 全曲线**不得**写入 `pit_required` 正式表；可用于 `price_basis=raw` 实验模式对比。
4. 正式历史策略验收：目标 `as_of` 须有可审计的 `xdxr` 事件链 + 已同步 `adjustment_factors`。

## 已知限制

- mootdx 需本地配置行情服务器（`python -m mootdx bestip`）；无 TCP 时同步阻断，不静默降级新浪 PIT。
- ETF/特殊品种 `category==11` 等需单独处理（阶段 4 后续）。
- 北交所覆盖需单独验证。

## 实现落点

| 模块 | 职责 |
|------|------|
| `tradingagents/market_data/adjustments.py` | `xdxr` → 因子/公司行为行 |
| `providers/free_astock_sources.py` | `fetch_xdxr_frame(symbol)` |
| `market_data/sync.py` | `sync_adjustment_factors()` |
| `market_data/repository.py` | 已有 `adjustment_factors` / `corporate_actions` 表 |

## 验收

- 离线：`tests/market_data/test_adjustments.py`（mock xdxr）
- 在线（可选）：`scripts/accept_free_data_path.py --live` 含 `sync --dataset adjustment-factors`
