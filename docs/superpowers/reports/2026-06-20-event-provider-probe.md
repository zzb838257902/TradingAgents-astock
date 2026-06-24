# 阶段 5 Task 1：免费事件数据源能力探测报告

> 日期：2026-06-20  
> 探测方式：当日 live HTTP 抽样 + 离线契约固化  
> 结论：**核心公告源 PASS**（可继续 Task 2–8）

## 1. 总结

| 数据集 | PIT 等级 | 核心源 | 探测状态 | 阻断增强 |
|---|---|---|---|---|
| official_announcements | pit_required | 新浪 VIP 公告列表 | **PASS** | 是 |
| event_news | best_effort | 东财 search-api | PASS | 否 |
| event_fund_flow | best_effort | 东财 push2 | PASS | 否 |
| event_hot_topics | current_only | 同花顺涨停题材 | PASS | 否 |

- 免费路径 **未读取** `TUSHARE_TOKEN`
- 新闻源 **不得** 替代官方公告（矩阵 `forbidden_substitutes` 已锁定）
- 东财 datacenter 披露为 **optional_enhancement**，`blocks_core_on_failure: false`

## 2. 核心公告源（official_announcements）

### 2.1 主源：sina.corp.vCB_AllBulletin

| 属性 | 值 |
|---|---|
| 平台 | 新浪财经 VIP |
| 接口 | `vCB_AllBulletin.php?stockid={code}&Page={n}` |
| 认证 | 无 |
| 许可 | 公开披露元数据；测试仅存哈希与字段清单 |
| 分页 | `Page` 参数，约 30 条/页 |
| 时间精度 | **仅日期** → PIT 保守为下一开市日 09:30 |
| 空结果 | 仅当 HTML 表格零行时为 `SUCCESS_EMPTY` |
| 错误 | `NETWORK_ERROR` / `RATE_LIMITED` / `PARSE_ERROR` / `HTTP_ERROR` |

### 2.2 备源：sina.CompanyFinanceService.getFinanceReport2022

- 覆盖定期财报披露 `report_list`（含 `publish_date` 与监管截止日回退）
- **不能**替代一般公告，仅作财报类披露补充

### 2.3 四板块 live 抽样（2026-06-20）

| 板块 | 代码 | 公告列表 HTTP | report_list 期数 | bulletin hash (16) |
|---|---|---:|---:|---|
| 沪市主板 | 600000 | 200 | 5 | 8f88cbc9857c92d4 |
| 深市主板 | 000001 | 200 | 5 | 4410cd78ade3fcb0 |
| 创业板 | 300001 | 200 | 5 | f4dd635fae7edb38 |
| 科创板 | 688001 | 200 | 5 | 0deee66cceb80a3a |

分页：`Page=1` 与 `Page=2` 均返回 30 行，首条日期不同（`2026-06-05` vs `2026-02-13`）。

### 2.4 禁止替代

- `eastmoney.search.cmsArticleWebOld`（新闻）
- `sina.corp.vCB_AllNewsStock`（个股新闻）
- `tushare.anns`（付费）

## 3. 非核心数据集

### event_news（best_effort）

- 主：东财 `search-api-web.eastmoney.com`
- 备：新浪 `vCB_AllNewsStock`
- 失败不阻断核心公告同步

### event_fund_flow（best_effort）

- 东财 push2 / push2his（复用阶段 4 `get_fund_flow`）
- 正式历史绩效不计入

### event_hot_topics（current_only）

- 同花顺 `getharden` 当日题材
- 历史请求必须 `data_error`

## 4. 场景契约（离线 recorded）

见 `tests/fixtures/events/recorded/README.md`：

- with_announcements / no_announcements / revised_announcement
- pagination / rate_limited / network_error

## 5. 门禁结论

```text
core_announcement_gate_status = PASS
```

**未触发 BLOCKED**；可继续 Task 2。

## 6. 风险与限制

1. 公告列表仅日期无时刻 → 必须用交易日历保守 `available_at`
2. HTML 解析脆弱 → Task 4 fixture 需覆盖修订与分页
3. 东财增强失败不得阻断免费核心路径
4. 新闻/BEST_EFFORT 不得进入正式历史绩效
