# 盘前个性化持仓扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为有持仓用户的 Web 盘前个性化报告新增连续变化和市值下方盈亏金额，且不改变企微与其他报告。

**Architecture:** 用 `market.instrument_trend_snapshots` 缓存标的每天一次的连续变化事实。首次生成盘前个性化报告时计算、通过受控函数写缓存；完整报告用九列 Markdown 表格记录结果，网页端仅对该表做双行市值和红绿灰盈亏渲染。

**Tech Stack:** PostgreSQL 16、asyncpg、FastAPI、Python dataclass、React、TypeScript、react-markdown、Vitest、pytest。

**Spec:** `docs/superpowers/specs/2026-09-09-pre-market-personalized-holdings-enrichment-design.md`

## Global Constraints

- 只处理 `AssetScope(kind="MINE")` 且具有 `audience_user_id` 的 `PRE_MARKET_PERSONALIZED`。
- 只回溯最近 30 个交易日并排除报告当日；缺事实显示 `--`，不推断。
- `PRE_MARKET_HOLDINGS_ENRICHMENTS_ENABLED` 默认 `true`；为 `false` 时不读写缓存、保持八列表格。
- 不改变 `pre_market_personalized.format_push_content`、无持仓简报、共享宏观简报和其他三时段报告。
- `market` schema 的直接写入保持禁止，运行时只执行受控 `SECURITY DEFINER` 函数。
- 每个任务遵循 Red → Green → Refactor，并以中文提交。

---

### Task 1: 建立趋势缓存数据库契约

**Files:**
- Create: `migrations/060_pre_market_holding_trends.sql`
- Modify: `scripts/migrate.py`
- Create: `tests/unit/test_pre_market_holding_trends_migration_contract.py`
- Modify: `tests/unit/test_migration_runner.py`

**Interfaces:**
- Produces: `market.instrument_trend_snapshots`，唯一键 `(instrument_id, trade_date)`。
- Produces: `market.upsert_instrument_trend_snapshot(...)`，供 API/Worker 调用。

- [ ] **Step 1: 写失败的迁移契约测试**

```python
def test_trend_cache_migration_has_one_row_per_instrument_and_day() -> None:
    sql = migration_text("060_pre_market_holding_trends.sql")
    assert "market.instrument_trend_snapshots" in sql
    assert "UNIQUE (instrument_id, trade_date)" in sql

def test_trend_cache_mutation_is_function_only() -> None:
    sql = migration_text("060_pre_market_holding_trends.sql")
    assert "SECURITY DEFINER" in sql
    assert "GRANT EXECUTE ON FUNCTION market.upsert_instrument_trend_snapshot" in sql
    assert "REVOKE INSERT, UPDATE, DELETE ON market.instrument_trend_snapshots" in sql
```

- [ ] **Step 2: 验证测试失败**

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_pre_market_holding_trends_migration_contract.py -q`

Expected: 因 `060` 不存在而失败。

- [ ] **Step 3: 实现迁移与 readback**

建立表字段：`instrument_id`、`trade_date`、`previous_direction`、`streak_direction`、`streak_days`、`streak_change_pct`、`history_start_date`、`history_end_date`、`history_field_code`、`quality_status`、`method_version`、`computed_at`。方向检查只允许 `UP`、`DOWN`、`FLAT` 或空；质量只允许 `FRESH`、`UNAVAILABLE`；`streak_days` 为 1–30 或空。函数使用 `SET search_path = pg_catalog, market, reference`、`INSERT ... ON CONFLICT DO NOTHING`。撤销直接写权限，仅授予 API/Worker 的函数执行权。为 `scripts/migrate.py` 增加表、唯一键、函数与 grant 的 readback。

- [ ] **Step 4: 验证迁移契约**

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_pre_market_holding_trends_migration_contract.py tests/unit/test_migration_runner.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add migrations/060_pre_market_holding_trends.sql scripts/migrate.py tests/unit/test_pre_market_holding_trends_migration_contract.py tests/unit/test_migration_runner.py
git commit -m "feat：建立盘前持仓趋势缓存契约"
```

### Task 2: 实现趋势计算与缓存仓储

**Files:**
- Create: `backend/reports/holding_trends.py`
- Modify: `backend/reports/repository.py`
- Create: `tests/unit/test_holding_trends.py`
- Modify: `tests/unit/test_pre_market_context_p2.py`

**Interfaces:**
- Produces: `PricePoint(as_of, price, field_code, quality)` 与 `HoldingTrend(display, previous_direction, streak_days, streak_change_pct, quality)`。
- Produces: `calculate_holding_trend(today_change_pct, history) -> HoldingTrend`。
- Produces: `repository.resolve_instrument_trends(conn, instrument_ids, trade_date, today_changes)`。

- [ ] **Step 1: 写失败的纯计算测试**

```python
def test_prior_same_direction_streak_excludes_today() -> None:
    history = [
        PricePoint(date(2026, 9, 4), Decimal("100"), "CLOSE", "FRESH"),
        PricePoint(date(2026, 9, 7), Decimal("102"), "CLOSE", "FRESH"),
        PricePoint(date(2026, 9, 8), Decimal("103"), "CLOSE", "FRESH"),
    ]
    assert calculate_holding_trend(Decimal("0.5"), history).display == "📈 连涨2天 (+3.00%)"

def test_opposite_today_direction_is_reversal() -> None:
    history = [
        PricePoint(date(2026, 9, 7), Decimal("102"), "CLOSE", "FRESH"),
        PricePoint(date(2026, 9, 8), Decimal("100"), "CLOSE", "FRESH"),
    ]
    assert calculate_holding_trend(Decimal("0.5"), history).display == "转涨"

def test_missing_history_is_unavailable() -> None:
    trend = calculate_holding_trend(Decimal("1"), [])
    assert (trend.display, trend.quality) == ("--", "UNAVAILABLE")
```

- [ ] **Step 2: 验证测试失败**

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_holding_trends.py -q`

Expected: 因模块不存在而失败。

- [ ] **Step 3: 实现纯计算与仓储**

纯函数仅接受有限正价格和 `FRESH` 历史点；按日期升序、最多 30 个交易日。今日方向与昨日方向相反时返回 `转涨`/`转跌`；同方向时从昨日向前计算连续段和 `(末价 / 首价 - 1) × 100`；零、缺失或不足两条可比较历史返回 `--`。仓储先读当日缓存；缺失项从 `market.market_observations` 读取 A 股/场内基金 `CLOSE`、场外基金 `NAV`，按缺失日期再从 `ledger.position_snapshots.market_price` 补齐。仓储不返回任何账户/数量字段。计算后调用 `SELECT market.upsert_instrument_trend_snapshot(...)`，冲突则重新读取缓存。

- [ ] **Step 4: 写并运行缓存复用测试**

```python
@pytest.mark.asyncio
async def test_same_day_cache_hit_skips_history_lookup() -> None:
    conn = CachedTrendConnection()
    trends = await repository.resolve_instrument_trends(
        conn, ["instrument-1"], date(2026, 9, 9), {"instrument-1": Decimal("1")}
    )
    assert trends["instrument-1"].display == "📈 连涨2天 (+3.00%)"
    assert conn.history_queries == 0
```

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_holding_trends.py tests/unit/test_pre_market_context_p2.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/reports/holding_trends.py backend/reports/repository.py tests/unit/test_holding_trends.py tests/unit/test_pre_market_context_p2.py
git commit -m "feat：实现盘前持仓连续变化缓存计算"
```

### Task 3: 仅为个性化盘前上下文附加趋势与盈亏

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/reports/service.py:450-643`
- Modify: `tests/unit/test_pre_market_context_p2.py`
- Modify: `tests/unit/test_pre_market_personalized.py`

**Interfaces:**
- Consumes: `Settings.pre_market_holdings_enrichments_enabled`、`repository.resolve_instrument_trends`。
- Produces: position 的 `trendDisplay`、`pnlAmount` 和 `preMarket["holdingsEnrichmentsEnabled"]`。

- [ ] **Step 1: 写失败的上下文测试**

```python
@pytest.mark.asyncio
async def test_personalized_context_adds_trend_and_pnl_when_enabled() -> None:
    context = await build_report_context(
        conn, "tenant-1", "PRE_MARKET", audience_user_id="user-1",
        asset_scope=AssetScope(kind="MINE", owner_user_id="user-1"),
        settings=Settings(_env_file=None, pre_market_holdings_enrichments_enabled=True),
    )
    assert context["positions"][0]["trendDisplay"] == "📈 连涨2天 (+3.00%)"
    assert context["positions"][0]["pnlAmount"] == "1234.56"

@pytest.mark.asyncio
async def test_shared_context_never_resolves_personal_holding_trends() -> None:
    await build_report_context(conn, "tenant-1", "PRE_MARKET", settings=Settings(_env_file=None))
    assert conn.trend_calls == 0
```

- [ ] **Step 2: 验证测试失败**

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_pre_market_context_p2.py tests/unit/test_pre_market_personalized.py -q`

Expected: 因开关和新上下文字段不存在而失败。

- [ ] **Step 3: 实现开关和范围隔离**

在 `Settings` 新增 `pre_market_holdings_enrichments_enabled: bool = Field(default=True)`。为 `build_report_context` 增加可选 `settings: Settings | None`，所有生成路径传入真实 settings，兼容直接调用时默认 `Settings(_env_file=None)`。只在 `session == "PRE_MARKET"`、开关开启、`audience_user_id` 非空且 `asset_scope.kind == "MINE"` 时解析趋势。盈亏金额严格按 `(price - avgCost) * quantity`，任一输入缺失、非有限或无法转换则为 `None`。共享、无持仓、关注列表与其他时段不得调用趋势仓储。

- [ ] **Step 4: 验证范围隔离**

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_pre_market_context_p2.py tests/unit/test_pre_market_personalized.py -q`

Expected: PASS，且共享路径断言 `trend_calls == 0`。

- [ ] **Step 5: 提交**

```bash
git add backend/config.py backend/reports/service.py tests/unit/test_pre_market_context_p2.py tests/unit/test_pre_market_personalized.py
git commit -m "feat：为盘前个性化报告附加趋势与盈亏"
```

### Task 4: 生成个性化九列 Markdown，保持八列兼容

**Files:**
- Modify: `backend/reports/pre_market.py:66-240`
- Modify: `backend/reports/pre_market_personalized.py:160-210`
- Modify: `tests/unit/test_pre_market_report.py`
- Modify: `tests/unit/test_pre_market_personalized.py`

**Interfaces:**
- Produces: `personalized_holdings_markdown(context) -> str`。
- Consumes: position `trendDisplay`、`pnlAmount` 与 `holdingsEnrichmentsEnabled`。

- [ ] **Step 1: 写失败的 Markdown 测试**

```python
def test_personalized_markdown_has_trend_and_market_value_pnl() -> None:
    markdown = personalized_holdings_markdown(_context_with_position(
        marketValue="185000", pnlAmount="1234.56", trendDisplay="📈 连涨3天 (+3.50%)"
    ))
    assert "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 市值 | 当日 | 连续变化 | 仓位 |" in markdown
    assert "185,000（盈亏 +1,234.56）" in markdown
    assert "📈 连涨3天 (+3.50%)" in markdown

def test_standard_markdown_keeps_eight_columns() -> None:
    assert "连续变化" not in holdings_markdown(_context_with_position()).splitlines()[0]
```

- [ ] **Step 2: 验证测试失败**

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_pre_market_report.py tests/unit/test_pre_market_personalized.py -q`

Expected: 因九列渲染器不存在而失败。

- [ ] **Step 3: 实现隔离渲染器**

保持 `holdings_markdown` 原样供共享和旧路径使用。新增 `personalized_holdings_markdown`：开关关闭时委托给 `holdings_markdown`；开启时使用同一 `position_table_rows` 生成九列。盈亏使用带千分位的两位小数，正数显式 `+`，零为 `0.00`，缺失为 `--`。`pre_market_personalized.build_report` 通过新增 `summary_with_holdings(..., holdings_renderer=personalized_holdings_markdown)` 使用该渲染器；不得改动 `format_push_content`。

- [ ] **Step 4: 验证渲染与企微不变**

Run: `.venv\\Scripts\\python.exe -m pytest tests/unit/test_pre_market_report.py tests/unit/test_pre_market_personalized.py -q`

Expected: PASS，已有企微列表测试仍为绿色。

- [ ] **Step 5: 提交**

```bash
git add backend/reports/pre_market.py backend/reports/pre_market_personalized.py tests/unit/test_pre_market_report.py tests/unit/test_pre_market_personalized.py
git commit -m "feat：渲染盘前个性化九列持仓表格"
```

### Task 5: 渲染网页市值下方红绿灰盈亏

**Files:**
- Modify: `frontend/src/features/reports/components/ReportMarkdown.tsx`
- Modify: `frontend/src/features/reports/components/ReportMarkdown.test.tsx`
- Modify: `frontend/src/index.css:1816-1885`

**Interfaces:**
- Consumes: 第六列 `市值（盈亏 +1,234.56）`。
- Produces: `report-holding-market-value`、`report-holding-pnl`、`report-pnl-profit`、`report-pnl-loss`、`report-pnl-flat`。

- [ ] **Step 1: 写失败的前端测试**

```tsx
it("renders a market value with red profit below it", () => {
  render(<ReportMarkdown>{nineColumnSummary("185,000（盈亏 +1,234.56）")}</ReportMarkdown>);
  expect(screen.getByText("185,000")).toHaveClass("report-holding-market-value");
  expect(screen.getByText("+1,234.56")).toHaveClass("report-holding-pnl", "report-pnl-profit");
});

it.each([["-1,234.56", "report-pnl-loss"], ["0.00", "report-pnl-flat"], ["--", "report-pnl-flat"]])(
  "renders %s using %s", (amount, className) => {
    render(<ReportMarkdown>{nineColumnSummary(`185,000（盈亏 ${amount}）`)}</ReportMarkdown>);
    expect(screen.getByText(amount)).toHaveClass(className);
  },
);
```

- [ ] **Step 2: 验证测试失败**

Run: `npm test -- --run frontend/src/features/reports/components/ReportMarkdown.test.tsx`

Expected: 当前单行市值单元格无法找到 P&L 子元素而失败。

- [ ] **Step 3: 实现严格解析与 CSS**

持仓表识别同时接受八列和九列表头。只在持仓表第六列使用正则 `^(.*)（盈亏 ([+-]?[0-9][0-9,]*(?:\\.[0-9]{2})?|--)）$` 拆分市值和盈亏；其他表格仍走现有文本渲染。CSS 为 P&L 使用 `font-size: 9px`，盈利 `#cf1322`，亏损 `#389e0d`，持平/`--` `#8c8c8c`；将持仓表最小宽度和九列宽度更新，保留横向滚动。

- [ ] **Step 4: 验证网页**

Run: `npm test -- --run frontend/src/features/reports/components/ReportMarkdown.test.tsx`

Run: `npm run lint`

Run: `npm run build`

Expected: 所有命令退出码为 0。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/features/reports/components/ReportMarkdown.tsx frontend/src/features/reports/components/ReportMarkdown.test.tsx frontend/src/index.css
git commit -m "feat：展示盘前持仓市值下方盈亏金额"
```

### Task 6: 更新运维说明并做完整验证

**Files:**
- Modify: `docs/11.数据库升级方法.md`
- Modify: `docs/20.WealthSail累计收益与标准报告任务设计及运维.md`

- [ ] **Step 1: 增加部署说明**

在数据库升级文档将目标迁移更新到 `060`；在报告运维文档写明 `PRE_MARKET_HOLDINGS_ENRICHMENTS_ENABLED=true`、关闭时回退八列、以及个性化调试接口验证九列和 `--` 降级的步骤。

- [ ] **Step 2: 运行完整验证**

Run: `.venv\\Scripts\\python.exe -m pytest -q`

Run: `npm test -- --run`

Run: `npm run lint`

Run: `npm run build`

Run: `git diff --check`

Expected: 所有命令退出码为 0；现有第三方弃用/pytest 缓存警告单独记录，不视为测试失败。

- [ ] **Step 3: 提交并推送**

```bash
git add docs/11.数据库升级方法.md docs/20.WealthSail累计收益与标准报告任务设计及运维.md
git commit -m "docs：补充盘前持仓趋势功能运维说明"
git push origin master
```

报告迁移 `060`、开关名称、测试结果和服务器验证顺序；任何时候不得提交 `.tmp/`。
