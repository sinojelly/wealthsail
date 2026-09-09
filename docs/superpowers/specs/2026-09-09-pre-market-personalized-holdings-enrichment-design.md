# 盘前个性化持仓扩展设计

## 目标

仅增强有持仓用户的 Web 端“研报审计 → 盘前简报 → 报告摘要”持仓表格：

1. 增加“连续变化”列，展示不含今日的连续涨跌天数与累计变化，或当日反转状态。
2. 在市值主值下显示按当前价格、成本价和数量计算的盈亏金额。
3. 不改变企微盘前个性化推送的列表格式，不改变无持仓用户的宏观简报。

## 范围与约束

- 只处理当前实际持仓标的；不读取关注列表，也不改变关注列表报告内容。
- 仅回溯最近 30 个交易日，排除报告当日。
- 不调用模型，不伪造行情、连续变化或盈亏金额。
- 数据不足时显示 `--`。
- 全局开关 `PRE_MARKET_HOLDINGS_ENRICHMENTS_ENABLED` 默认 `true`。
- 开关为 `false` 时保持现有八列表格，不计算或写入趋势缓存。

## 数据来源

持仓快照的实际 schema 是 `ledger.position_snapshots`，不是 `market.position_snapshots`：

- `market_price`：持仓快照价格或净值；
- `market_value`、`unrealized_pnl`：现有派生值；
- `quantity`、`avg_cost`：盈亏金额的输入。

当持仓快照缺少日期价格时，以 `market.market_observations` 补充：

- A 股与场内基金读取 `CLOSE`；
- 场外基金读取 `NAV`；
- 不用 `ESTIMATED_NAV` 作为连续历史，以免把盘中估值混入正式净值连续序列。

## 缓存模型

新增迁移 `060_pre_market_holding_trends.sql`，建立全局市场缓存：

```text
market.instrument_trend_snapshots
  instrument_id            UUID, FK reference.instruments
  trade_date               DATE
  previous_direction       UP | DOWN | FLAT | NULL
  streak_direction         UP | DOWN | NULL
  streak_days              INTEGER
  streak_change_pct        NUMERIC
  history_start_date       DATE | NULL
  history_end_date         DATE | NULL
  history_field_code       CLOSE | NAV | NULL
  quality_status           FRESH | UNAVAILABLE
  method_version           TEXT
  computed_at              TIMESTAMPTZ
  UNIQUE (instrument_id, trade_date)
```

趋势仅与标的和交易日相关，不与用户、租户或账户相关，因此可在用户之间安全复用。应用读取报告上下文时：

1. 查询当前交易日的缓存；
2. 缓存不存在时读取最近 30 个交易日历史并计算；
3. 通过受控 `SECURITY DEFINER` upsert 写入，冲突时读取已有行；
4. 仅当请求是带 `AssetScope(kind="MINE")` 的盘前个性化报告时，为当前持仓行附加趋势结果。

迁移只授予 API/Worker 调用受控函数的权限，保持直接写入 `market` schema 的既有权限边界。

## 连续变化计算

1. 当前方向由当前持仓的 `changePct` 判断：正为 `UP`、负为 `DOWN`、零为 `FLAT`；缺失为不可用。
2. 由排除今日的价格序列逐日计算历史方向；连续序列从昨日开始向前回溯。
3. 当前方向与昨日方向相反时：显示 `转涨` 或 `转跌`，不显示连续天数。
4. 同方向时：显示 `📈 连涨N天 (+X%)` 或 `📉 连跌N天 (-X%)`；`N` 与累计变化均不包含今日。
5. 没有昨日价格、无法得到至少一个历史方向、历史质量不可用或价格无效：显示 `--`。

累计变化采用连续区间首尾价格计算：`(结束价 / 起始价 - 1) × 100`。

## 盈亏金额

每个报告行以如下公式计算：

```text
(最新价 - 成本价) × 持仓数量
```

若任一输入缺失、非有限数值或无法确定，显示 `--`。零值显示 `0.00`。

报告 Markdown 在市值单元格中保存稳定机器可解析形式：

```text
185,000（盈亏 +1,234.56）
```

前端专用表格渲染器将它拆为主值和副值，避免在 Markdown 中注入 HTML。

## 网页渲染

`pre_market_personalized.build_report` 使用专用的增强版持仓 Markdown；共享宏观盘前报告、旧版 `PRE_MARKET` 兼容入口和其他时段继续使用原有持仓 Markdown。增强版完整表格从八列扩为九列：

```text
代码｜名称｜持仓｜成本价｜最新价｜市值｜当日｜连续变化｜仓位
```

`frontend/src/features/reports/components/ReportMarkdown.tsx` 继续只识别这一张持仓表，对市值单元格渲染两行：

- 市值主值：现有样式；
- 盈亏副值：更小字号，盈利 `#cf1322`，亏损 `#389e0d`，持平 `#8c8c8c`。

`frontend/src/index.css` 更新九列的宽度与横向滚动布局。其他 Markdown 表格和所有企微消息不受影响。

## 配置与降级

新增 `Settings.pre_market_holdings_enrichments_enabled`，环境变量名为：

```text
PRE_MARKET_HOLDINGS_ENRICHMENTS_ENABLED=true
```

默认开启。关闭后：

- 不读取或写入趋势缓存；
- Web 报告回退为原有八列；
- 企微、无持仓简报和其他三时段报告保持不变。

当开关开启但单一标的历史不足时，仅该标的“连续变化”和/或盈亏金额显示 `--`；其他标的仍正常展示。

## 测试与验证

后端单元测试覆盖：

- 连涨、连跌、转涨、转跌和历史不足；
- 30 个交易日截断；
- 缓存首算、同日复用及开关关闭时不访问缓存；
- 盈利、亏损、持平和输入缺失的盈亏金额；
- 旧八列 Markdown 兼容性。

前端测试覆盖：

- 九列表格识别；
- 市值下方盈亏的红、绿、灰样式；
- `--` 降级值；
- 非持仓表格不进入专用渲染。

验收方式：使用调试盘前个性化报告接口生成持仓报告，检查审计页九列表格；再关闭开关重新生成，确认回退八列；最后核对企微推送仍为现有列表格式。
