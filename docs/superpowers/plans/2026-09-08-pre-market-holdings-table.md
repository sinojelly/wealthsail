# Pre-Market Holdings Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a grounded holdings table to the 08:35 personalized pre-market audit report, render it as a responsive GFM table on Web, and send a WeCom-compatible compact holdings summary under 200 characters.

**Architecture:** Enrich the report-only PRE_MARKET context with persisted, price-aligned quote metadata without changing the public portfolio API. Pure backend formatters create a deterministic seven-column GFM table for the stored report and complete-line compact rows for WeCom. A focused React Markdown component renders the stored summary safely and applies A-share rise/fall colors.

**Tech Stack:** Python 3.12, FastAPI service layer, asyncpg/PostgreSQL, pytest, React 19, TypeScript, react-markdown 10, remark-gfm 4, Vitest, Testing Library, CSS.

**Spec:** `docs/superpowers/specs/2026-09-08-pre-market-holdings-table-design.md`

## Global Constraints

- The holdings table belongs only to `PRE_MARKET_PERSONALIZED` at 08:35; `PRE_MARKET_MACRO` at 08:30 remains tenant-neutral.
- Use only persisted holdings, portfolio projections, and market observations; do not call AI or an external market provider.
- Do not change public API paths or response contracts and do not add a database migration.
- Missing or unaligned day-change data renders as `--`; never infer it from cost or cumulative P&L.
- `STALE`, `UNAVAILABLE`, or exposed `MISSING` quality adds `⚠️` after the asset name.
- Full reports use GFM Markdown; WeCom remains `message_type="markdown"` because the self-built app API does not support `markdown_v2` or GFM tables.
- The WeCom personalized summary remains at most 200 characters and must never truncate a Markdown marker or holding line halfway.
- Do not enable raw HTML rendering in the report viewer.
- Preserve unrelated user changes and the main checkout's untracked `.tmp/` directory.

---

### Task 1: Add the persisted, price-aligned quote contract

**Files:**
- Modify: `backend/reports/repository.py:1316`
- Modify: `backend/reports/service.py:450`
- Modify: `tests/unit/test_pre_market_report.py`
- Modify: `tests/unit/test_pre_market_context_p2.py:143`
- Modify: `tests/unit/test_personalized_report_push.py:620`

**Interfaces:**
- Consumes: position dictionaries returned by `backend.portfolio.service.get_positions()`, including `instrumentId` and `price`; one captured `report_as_of: datetime`.
- Produces: `repository.get_pre_market_position_quotes(conn, position_prices, report_as_of) -> dict[str, dict]`, where each value has `changePct: str`, `quality: str`, and `sourceAsOf: datetime`; PRE_MARKET position entries gain report-internal `changePct`, `changeQuality`, and `changeSourceAsOf` keys.

- [ ] **Step 1: Write repository contract tests that reject unaligned or future observations**

Add a recording connection test to `tests/unit/test_pre_market_report.py`:

```python
@pytest.mark.asyncio
async def test_pre_market_position_quotes_require_price_alignment_and_report_cutoff():
    as_of = datetime(2026, 9, 8, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai"))
    conn = _CoverageConnection(
        [
            {
                "instrument_id": "11111111-1111-1111-1111-111111111111",
                "change_pct": "0.84",
                "quality_status": "STALE",
                "source_as_of": as_of,
            }
        ]
    )

    result = await repository.get_pre_market_position_quotes(
        conn,
        {"11111111-1111-1111-1111-111111111111": "39.15"},
        as_of,
    )

    assert result == {
        "11111111-1111-1111-1111-111111111111": {
            "changePct": "0.84",
            "quality": "STALE",
            "sourceAsOf": as_of,
        }
    }
    assert "o.as_of <= $3" in conn.query
    assert "o.value = target.market_price" in conn.query
    assert "o.field_code IN ('CLOSE', 'NAV', 'ESTIMATED_NAV')" in conn.query
```

Add this empty-input test and import `ZoneInfo` if the test file does not already import it:

```python
@pytest.mark.asyncio
async def test_pre_market_position_quotes_skip_database_for_empty_input():
    conn = _CoverageConnection([])

    result = await repository.get_pre_market_position_quotes(
        conn,
        {},
        datetime(2026, 9, 8, 8, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result == {}
    assert conn.query == ""
```

- [ ] **Step 2: Run the repository tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_report.py -k "position_quotes"
```

Expected: FAIL with `AttributeError: module 'backend.reports.repository' has no attribute 'get_pre_market_position_quotes'`.

- [ ] **Step 3: Implement the repository query and strict DTO conversion**

Add this interface near `latest_day_change_pct()` in `backend/reports/repository.py`:

```python
async def get_pre_market_position_quotes(
    conn: asyncpg.Connection,
    position_prices: dict[str, object],
    report_as_of: datetime,
) -> dict[str, dict]:
    """Return persisted change percentages aligned with projection prices."""
    if not position_prices:
        return {}
    instrument_ids = list(position_prices)
    market_prices: list[Decimal | None] = []
    for instrument_id in instrument_ids:
        raw_price = position_prices[instrument_id]
        try:
            parsed_price = Decimal(str(raw_price)) if raw_price is not None else None
        except (ArithmeticError, TypeError, ValueError):
            parsed_price = None
        market_prices.append(
            parsed_price if parsed_price is not None and parsed_price.is_finite() else None
        )
    rows = await conn.fetch(
        """
        WITH target AS (
            SELECT input.instrument_id, input.market_price
              FROM unnest($1::uuid[], $2::numeric[])
                   AS input(instrument_id, market_price)
        )
        SELECT target.instrument_id,
               quote.change_pct,
               quote.quality_status,
               quote.source_as_of
          FROM target
          LEFT JOIN LATERAL (
              SELECT o.metadata ->> 'changePct' AS change_pct,
                     o.quality_status,
                     o.as_of AS source_as_of
                FROM market.market_observations o
               WHERE o.subject_id = target.instrument_id
                 AND o.as_of <= $3
                 AND o.value = target.market_price
                 AND o.field_code IN ('CLOSE', 'NAV', 'ESTIMATED_NAV')
                 AND o.metadata ->> 'changePct'
                     ~ '^[+-]?[0-9]+([.][0-9]+)?$'
               ORDER BY o.as_of DESC, o.observed_at DESC, o.revision DESC
               LIMIT 1
          ) quote ON TRUE
        """,
        instrument_ids,
        market_prices,
        report_as_of,
    )
    result: dict[str, dict] = {}
    for row in rows:
        if row["change_pct"] is None:
            continue
        try:
            change_pct = Decimal(str(row["change_pct"]))
        except (ArithmeticError, TypeError, ValueError):
            continue
        if not change_pct.is_finite():
            continue
        result[str(row["instrument_id"])] = {
            "changePct": str(change_pct),
            "quality": str(row["quality_status"] or "UNAVAILABLE"),
            "sourceAsOf": row["source_as_of"],
        }
    return result
```

The exact SQL whitespace may differ, but retain all three safety predicates: report cutoff, exact price alignment, and allowed field codes.

- [ ] **Step 4: Run repository tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_report.py -k "position_quotes"
```

Expected: PASS.

- [ ] **Step 5: Write service tests for PRE_MARKET-only enrichment**

Extend `_stub_context_deps()` in `tests/unit/test_pre_market_context_p2.py` so every PRE_MARKET context test installs a default quote mock:

```python
monkeypatch.setattr(
    service.repository,
    "get_pre_market_position_quotes",
    mock.AsyncMock(return_value={}),
)
```

Add a focused test:

```python
@pytest.mark.asyncio
async def test_pre_market_context_enriches_positions_with_aligned_quote(monkeypatch):
    conn = _stub_context_deps(
        monkeypatch, ak_items=[], db_actions=[], cutoff_rules=[]
    )
    quote_as_of = datetime(
        2026, 9, 5, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    service.repository.get_pre_market_position_quotes.return_value = {
        "inst-kweichow": {
            "changePct": "0.84",
            "quality": "STALE",
            "sourceAsOf": quote_as_of,
        }
    }

    context = await service.build_report_context(
        conn, "tenant-1", "PRE_MARKET", audience_user_id="user-1"
    )

    position = context["positions"][0]
    assert position["changePct"] == "0.84"
    assert position["changeQuality"] == "STALE"
    assert position["changeSourceAsOf"] == quote_as_of
    called_prices = service.repository.get_pre_market_position_quotes.await_args.args[1]
    assert called_prices == {
        "inst-kweichow": position.get("price"),
        "inst-pingan": context["positions"][1].get("price"),
        "inst-fund": context["positions"][2].get("price"),
    }
```

Update the three `_stub_context_deps()` positions with `price` values `"1680.00"`, `"12.50"`, and `"1.40"`. In the PRE_MARKET setup inside `tests/unit/test_personalized_report_push.py`, install the same isolated default:

```python
monkeypatch.setattr(
    report_service.repository,
    "get_pre_market_position_quotes",
    AsyncMock(return_value={}),
)
```

- [ ] **Step 6: Run service tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_context_p2.py tests/unit/test_personalized_report_push.py -k "pre_market or context"
```

Expected: the new enrichment test FAILS because the position has no `changePct`.

- [ ] **Step 7: Add report-only enrichment in `build_report_context()`**

Capture one clock value at the beginning of `build_report_context()` and use it for summary and PRE_MARKET cutoff calculations:

```python
report_as_of = now_shanghai()
summary = await get_summary(conn, tenant_id, report_as_of, scope=asset_scope)
```

Inside the PRE_MARKET branch, after active positions are known and before constructing `pre_market_context`, call the repository and merge only returned data:

```python
position_prices = {
    str(position["instrumentId"]): position.get("price")
    for position in positions
    if position.get("instrumentId")
}
position_quotes = await repository.get_pre_market_position_quotes(
    conn, position_prices, report_as_of
)
for position in positions:
    quote = position_quotes.get(str(position.get("instrumentId") or ""))
    if quote is None:
        position["changePct"] = None
        position["changeQuality"] = "UNAVAILABLE"
        position["changeSourceAsOf"] = None
        continue
    position["changePct"] = quote["changePct"]
    position["changeQuality"] = quote["quality"]
    position["changeSourceAsOf"] = quote["sourceAsOf"]
```

Do not add these keys in non-PRE_MARKET contexts and do not modify `get_positions()`.

- [ ] **Step 8: Run Task 1 tests and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_report.py tests/unit/test_pre_market_context_p2.py tests/unit/test_personalized_report_push.py
.\.venv\Scripts\python.exe -m ruff check backend/reports/repository.py backend/reports/service.py tests/unit/test_pre_market_report.py tests/unit/test_pre_market_context_p2.py tests/unit/test_personalized_report_push.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 9: Commit the quote contract**

```powershell
git add backend/reports/repository.py backend/reports/service.py tests/unit/test_pre_market_report.py tests/unit/test_pre_market_context_p2.py tests/unit/test_personalized_report_push.py
git commit -m "feat(reports): align pre-market holding changes"
```

---

### Task 2: Generate the deterministic full holdings table

**Files:**
- Modify: `backend/reports/pre_market.py:894`
- Modify: `backend/reports/pre_market_personalized.py:168`
- Modify: `tests/unit/test_pre_market_report.py`
- Modify: `tests/unit/test_pre_market_personalized.py:17`

**Interfaces:**
- Consumes: context positions enriched in Task 1.
- Produces: `pre_market.position_table_rows(context) -> list[dict]`, `pre_market.holdings_markdown(context) -> str`, and `pre_market.summary_with_holdings(context, section_summary) -> str`; personalized and legacy standard PRE_MARKET summaries start with the same holdings contract, while the shared macro variant remains unchanged.

- [ ] **Step 1: Add failing formatter tests for all table rules**

Add a rich fixture and assertions to `tests/unit/test_pre_market_report.py`:

```python
def test_holdings_markdown_sorts_formats_marks_and_escapes():
    context = {
        "positions": [
            {
                "instrumentId": "fund",
                "symbol": "110022",
                "name": "基金|成长\nA",
                "type": "场外基金",
                "quantity": "118420.52",
                "avgCost": "1.73",
                "price": None,
                "changePct": None,
                "weight": None,
                "quality": "FRESH",
                "changeQuality": "UNAVAILABLE",
            },
            {
                "instrumentId": "stock",
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "8500",
                "avgCost": "38.82",
                "price": "39.15",
                "changePct": "0.84",
                "weight": "34.85",
                "quality": "FRESH",
                "changeQuality": "STALE",
            },
            {
                "instrumentId": "etf",
                "symbol": "510300",
                "name": "沪深300ETF",
                "type": "场内ETF",
                "quantity": "62000",
                "avgCost": "4.14",
                "price": "4.12",
                "changePct": "-0.31",
                "weight": "29.68",
                "quality": "FRESH",
                "changeQuality": "FRESH",
            },
        ]
    }

    markdown = pre_market.holdings_markdown(context)

    lines = markdown.splitlines()
    assert lines[0] == "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 当日 | 仓位 |"
    assert lines[2].startswith("| 600036 | 招商银行⚠️ | 8,500股 |")
    assert lines[3].startswith("| 510300 | 沪深300ETF | 62,000份 |")
    assert lines[4].startswith(r"| 110022 | 基金\|成长 A⚠️ | 118,420.52份 |")
    assert "| +0.84% | 34.85% |" in lines[2]
    assert "| -0.31% | 29.68% |" in lines[3]
    assert "| -- | -- | -- |" in lines[4]
```

Add tests for stable symbol ordering, non-finite values, and empty positions:

```python
def test_position_table_rows_use_symbol_as_stable_tie_break():
    context = {
        "positions": [
            {
                "symbol": symbol,
                "name": symbol,
                "type": "A股",
                "quantity": "1",
                "weight": "10",
                "quality": "FRESH",
                "changeQuality": "FRESH",
            }
            for symbol in ("600519", "000001")
        ]
    }

    assert [row["symbol"] for row in pre_market.position_table_rows(context)] == [
        "000001",
        "600519",
    ]


def test_holdings_markdown_renders_non_finite_values_as_missing():
    context = {
        "positions": [
            {
                "symbol": "600036",
                "name": "招商银行",
                "type": "A股",
                "quantity": "Infinity",
                "avgCost": "NaN",
                "price": None,
                "changePct": None,
                "weight": None,
                "quality": "MISSING",
                "changeQuality": "UNAVAILABLE",
            }
        ]
    }

    row = pre_market.holdings_markdown(context).splitlines()[2]
    assert row == "| 600036 | 招商银行⚠️ | -- | -- | -- | -- | -- |"


def test_holdings_markdown_empty_state():
    assert pre_market.holdings_markdown({"positions": []}) == (
        "暂无持仓，今日市场概况"
    )
```

- [ ] **Step 2: Run formatter tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_report.py -k "holdings_markdown"
```

Expected: FAIL because `holdings_markdown` is not defined.

- [ ] **Step 3: Implement pure row normalization and Markdown rendering**

In `backend/reports/pre_market.py`, add focused helpers with these signatures:

```python
_POSITION_QUALITY_PRIORITY = {
    "FRESH": 0,
    "ESTIMATED": 1,
    "STALE": 2,
    "UNAVAILABLE": 3,
    "MISSING": 3,
}


def position_table_rows(context: dict) -> list[dict]:
    """Normalize and deterministically order holdings for report rendering."""
    rows: list[dict] = []
    for position in context.get("positions") or []:
        if not isinstance(position, dict):
            continue
        position_quality = str(position.get("quality") or "UNAVAILABLE").upper()
        change_quality = str(
            position.get("changeQuality") or "UNAVAILABLE"
        ).upper()
        quality = max(
            (position_quality, change_quality),
            key=lambda value: _POSITION_QUALITY_PRIORITY.get(value, 3),
        )
        if quality == "MISSING":
            quality = "UNAVAILABLE"
        asset_type = str(position.get("type") or "").upper()
        rows.append(
            {
                "symbol": str(position.get("symbol") or "--"),
                "name": str(position.get("name") or "未命名标的"),
                "quantity": safe_decimal(position.get("quantity")),
                "unit": (
                    "股"
                    if asset_type in {"A股", "股票", "STOCK", "CN_STOCK"}
                    else "份"
                ),
                "avgCost": safe_decimal(position.get("avgCost")),
                "price": safe_decimal(position.get("price")),
                "changePct": safe_decimal(position.get("changePct")),
                "weight": safe_decimal(position.get("weight")),
                "quality": quality,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["weight"] is None,
            -(row["weight"] or Decimal("0")),
            row["symbol"],
        ),
    )


def holdings_markdown(context: dict) -> str:
    """Render the seven-column GFM holdings table or the exact empty copy."""
    rows = position_table_rows(context)
    if not rows:
        return "暂无持仓，今日市场概况"
    lines = [
        "| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 当日 | 仓位 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        warning = "⚠️" if row["quality"] in {"STALE", "UNAVAILABLE"} else ""
        quantity = _format_table_number(row["quantity"], grouped=True)
        change = _format_table_number(
            row["changePct"], places=2, signed=True, suffix="%"
        )
        weight = _format_table_number(row["weight"], places=2, suffix="%")
        cells = (
            row["symbol"],
            f'{row["name"]}{warning}',
            f'{quantity}{row["unit"]}' if quantity != "--" else "--",
            _format_table_number(row["avgCost"]),
            _format_table_number(row["price"]),
            change,
            weight,
        )
        lines.append("| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |")
    return "\n".join(lines)


def summary_with_holdings(context: dict, section_summary: str) -> str:
    return f"## 持仓概览\n\n{holdings_markdown(context)}\n\n{section_summary}"
```

The implementation above uses the existing `safe_decimal()` helper and these exact output keys: `symbol`, `name`, `quantity`, `unit`, `avgCost`, `price`, `changePct`, `weight`, and `quality`. Add `from decimal import Decimal` if this module does not already import it. Treat `MISSING` as `UNAVAILABLE`; choose the worse of `position.quality` and `position.changeQuality`; sort with `(weight is None, -weight, symbol)`.

Implement Markdown escaping as a separate private helper:

```python
def _markdown_cell(value: object) -> str:
    return (
        str(value or "--")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
        or "--"
    )


def _format_table_number(
    value: Decimal | None,
    *,
    places: int | None = None,
    signed: bool = False,
    grouped: bool = False,
    suffix: str = "",
) -> str:
    if value is None or not value.is_finite():
        return "--"
    if places is not None:
        sign = "+" if signed and value >= 0 else ""
        return f"{sign}{value:,.{places}f}{suffix}"
    rendered = format(value, ",f" if grouped else "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"
```

Use comma grouping for quantity while stripping insignificant zeros. Determine units from normalized asset type: `A股`, `股票`, `STOCK`, and `CN_STOCK` map to `股`; every other supported holding type maps to `份`. Format day change with an explicit sign and two decimal places; format weight with two decimal places; preserve the existing string precision of cost and latest price rather than inventing additional digits.

- [ ] **Step 4: Run formatter tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_report.py -k "holdings_markdown"
```

Expected: PASS.

- [ ] **Step 5: Write failing personalized-summary integration tests**

Extend `_positions()` in `tests/unit/test_pre_market_personalized.py` with table fields. Update the existing section-title test to preserve the three report items, then assert the summary adds a table independently of `items`:

```python
def test_personalized_report_prepends_holdings_table_without_changing_items():
    draft = pre_market_personalized.build_report(
        _context(positions=_positions(), watchlist=_watchlist())
    )

    assert draft["summary"].startswith(
        "## 持仓概览\n\n| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 当日 | 仓位 |"
    )
    assert "| 600036 | 招商银行" in draft["summary"]
    assert [item["title"] for item in draft["items"]] == [
        "持仓今日影响",
        "关注标的动态",
        "今日操作提醒",
    ]
```

Add the watchlist-only case:

```python
def test_personalized_report_uses_exact_no_holdings_copy_for_watchlist_only():
    draft = pre_market_personalized.build_report(
        _context(positions=[], watchlist=_watchlist())
    )
    assert draft["summary"].startswith(
        "## 持仓概览\n\n暂无持仓，今日市场概况"
    )
```

Update `test_full_report_has_seven_grounded_sections_and_explicit_missing_data()` in `tests/unit/test_pre_market_report.py` so the standard PRE_MARKET path reuses the same table while preserving all seven existing section lines:

```python
assert draft["summary"].startswith(
    "## 持仓概览\n\n| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 当日 | 仓位 |"
)
assert draft["summary"].splitlines()[-7:] == [
    "1. 隔夜市场速览：美股主要指数：暂无；A50期货：暂无。",
    "2. 今日焦点事件：招商银行今日除权，预计低开。",
    "3. 今日重点关注：经济数据发布：暂无；持仓公司行动：招商银行(600036)今日现金分红除权；申赎状态变化：暂无。",
    "4. 持仓今日影响：招商银行(600036)现金分红除权，价格可能出现技术性调整；现金到账日为2026-09-04，实际收益以到账和税费为准。",
    "5. 操作参考：招商银行今日除权，若除权后价格偏低，可关注填权行情；易方达消费行业今日15:00前可申赎，请留意截止时间。",
    "6. 今日操作提醒：易方达消费行业(110022)默认渠道截止时间15:00（Asia/Shanghai），今日15:00前可提交申购/赎回（以渠道状态为准）。",
    "7. 一句话总结：情绪偏强；招商银行除权；1只基金15:00前可提交。",
]
```

- [ ] **Step 6: Run personalized report tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_personalized.py -k "prepends_holdings or exact_no_holdings"
```

Expected: FAIL because the summary starts with the numbered text sections.

- [ ] **Step 7: Prepend the full table to both user-scoped PRE_MARKET summaries**

In `backend/reports/pre_market.py`, replace the direct numbered summary with `summary_with_holdings(context, section_summary)`. In `backend/reports/pre_market_personalized.py`, import `summary_with_holdings` and apply it to its own three numbered sections. The personalized form is:

```python
section_summary = "\n".join(
    f"{index}. {title}：{body}"
    for index, (title, body, _item_type, _item_quality) in enumerate(
        sections, start=1
    )
)
summary = summary_with_holdings(context, section_summary)
```

Assign `summary` to each draft. Do not change `pre_market_macro.py`, do not add the table as a report item, and do not change evidence persistence.

- [ ] **Step 8: Run Task 2 tests and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_report.py tests/unit/test_pre_market_personalized.py
.\.venv\Scripts\python.exe -m ruff check backend/reports/pre_market.py backend/reports/pre_market_personalized.py tests/unit/test_pre_market_report.py tests/unit/test_pre_market_personalized.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 9: Commit the full report table**

```powershell
git add backend/reports/pre_market.py backend/reports/pre_market_personalized.py tests/unit/test_pre_market_report.py tests/unit/test_pre_market_personalized.py
git commit -m "feat(reports): add pre-market holdings table"
```

---

### Task 3: Replace the WeCom body with a bounded compatible summary

**Files:**
- Modify: `backend/reports/pre_market_personalized.py:222`
- Modify: `tests/unit/test_pre_market_personalized.py:206`
- Verify only: `backend/reports/repository.py:914`
- Verify only: `backend/jobs/outbox.py:113`
- Verify only: `backend/integrations/wecom.py:128`
- Verify only: `tests/unit/test_outbox_delivery.py`

**Interfaces:**
- Consumes: `pre_market.position_table_rows(context)` from Task 2 and existing watchlist/action/reminder helpers.
- Produces: `format_push_content(context) -> str`, containing complete lines only, no GFM table, and no more than 200 characters.

- [ ] **Step 1: Replace old four-label expectations with failing compatibility tests**

Update the push tests in `tests/unit/test_pre_market_personalized.py`:

```python
def test_personalized_push_uses_two_compact_holdings_and_overflow_count():
    positions = [
        {
            "instrumentId": str(index),
            "symbol": f"60003{index}",
            "name": f"标的{index}",
            "type": "A股",
            "quantity": "100",
            "avgCost": "10.00",
            "price": "10.00",
            "changePct": str(index - 1),
            "weight": str(30 - index),
            "quality": "FRESH",
            "changeQuality": "FRESH",
        }
        for index in range(3)
    ]

    content = pre_market_personalized.format_push_content(
        _context(positions=positions)
    )

    assert content.splitlines()[:3] == [
        "**盘前持仓概览**",
        "600030 标的0 30.00%｜-1.00%",
        "600031 标的1 29.00%｜+0.00%",
    ]
    assert "另1项见完整报告" in content
    assert "600032" not in content
    assert len(content) <= 200
```

Add these exact boundary tests. They prove that only asset names are clipped, complete lines are preserved, and the old action/reminder facts survive whenever they fit:

```python
def test_personalized_push_clips_only_name_and_keeps_complete_lines():
    position = {
        "instrumentId": _HELD_ID,
        "symbol": "600036",
        "name": "这是一个非常非常长的持仓标的名称",
        "type": "A股",
        "quantity": "100",
        "avgCost": "10",
        "price": "11",
        "changePct": "1",
        "weight": "80",
        "quality": "FRESH",
        "changeQuality": "FRESH",
    }

    content = pre_market_personalized.format_push_content(
        _context(positions=[position])
    )

    assert "600036 这是一个非常非常长的持仓… 80.00%｜+1.00%" in content
    assert all(line and not line.endswith("**…") for line in content.splitlines())
    assert len(content) <= 200


def test_personalized_push_watchlist_only_uses_no_holdings_copy():
    content = pre_market_personalized.format_push_content(
        _context(positions=[], watchlist=_watchlist())
    )

    assert content.splitlines()[1] == "暂无持仓，今日市场概况"
    assert "关注" not in content or "贵州茅台" in content
    assert len(content) <= 200


def test_personalized_push_keeps_reminder_and_action_when_budget_allows():
    content = pre_market_personalized.format_push_content(
        _context(
            positions=_positions(),
            pre_market={
                "reportDate": "2026-09-08",
                "fundCutoffRules": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "110022",
                        "name": "基金A",
                        "salesChannel": "DEFAULT",
                        "cutoffTime": "15:00:00",
                        "timezone": "Asia/Shanghai",
                    }
                ],
                "corporateActions": [
                    {
                        "instrumentId": _HELD_ID,
                        "symbol": "600036",
                        "name": "招商银行",
                        "actionType": "EX_RIGHTS",
                        "actionDate": "2026-09-08",
                    }
                ],
            },
        )
    )

    assert "提醒：" in content and "15:00" in content
    assert "行动：" in content and "招商银行" in content
    assert len(content) <= 200
```

- [ ] **Step 2: Run push tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_personalized.py -k "push"
```

Expected: FAIL because the current push begins with `持仓：` and has no compact rows.

- [ ] **Step 3: Implement complete-line budgeting**

In `backend/reports/pre_market_personalized.py`, replace blind line clipping with bounded line construction:

```python
_PUSH_LIMIT = 200


def _append_push_line(lines: list[str], candidate: str) -> bool:
    rendered = "\n".join((*lines, candidate))
    if len(rendered) > _PUSH_LIMIT:
        return False
    lines.append(candidate)
    return True
```

Use these helpers and build content in this order:

```python
def _compact_holding_name(value: object, limit: int = 12) -> str:
    name = str(value or "未命名标的").replace("\r", " ").replace("\n", " ")
    return name if len(name) <= limit else f"{name[: limit - 1]}…"


def _holding_push_line(row: dict) -> str:
    warning = "⚠️" if row["quality"] in {"STALE", "UNAVAILABLE"} else ""
    weight = _push_percent(row["weight"])
    change = _push_percent(row["changePct"], signed=True)
    return (
        f'{row["symbol"]} {_compact_holding_name(row["name"])}{warning} '
        f"{weight}｜{change}"
    )


def _push_percent(value: object, *, signed: bool = False) -> str:
    parsed = safe_decimal(value)
    if parsed is None:
        return "--"
    sign = "+" if signed and parsed >= 0 else ""
    return f"{sign}{parsed:.2f}%"


def format_push_content(context: dict) -> str:
    rows = position_table_rows(context)
    lines = ["**盘前持仓概览**"]
    if rows:
        for row in rows[:2]:
            _append_push_line(lines, _holding_push_line(row))
        if len(rows) > 2:
            _append_push_line(lines, f"另{len(rows) - 2}项见完整报告")
    else:
        _append_push_line(lines, "暂无持仓，今日市场概况")
    _append_push_line(lines, f"提醒：{cutoff_push_text(context)}")
    _append_push_line(lines, f"行动：{action_list_text(context, limit=3)}")
    content = "\n".join(lines)
    assert len(content) <= _PUSH_LIMIT
    return content
```

The function builds content in this order:

1. `**盘前持仓概览**`.
2. At most two rows from `position_table_rows(context)`, each formatted as `代码 名称 仓位｜当日`; clip only the name before constructing the row.
3. `另N项见完整报告` when more than two holdings exist.
4. For watchlist-only users, `暂无持仓，今日市场概况` replaces holding rows.
5. Append one bounded `f"提醒：{cutoff_push_text(context)}"` line, then one bounded `f"行动：{action_list_text(context, limit=3)}"` line, only if the complete candidate fits.

If a required holding line cannot fit because source fields are unexpectedly long, reduce the already-normalized display name and retry; never slice the final joined string. Assert the final length defensively.

- [ ] **Step 4: Run push and transport regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_personalized.py tests/unit/test_outbox_delivery.py tests/unit/test_personalized_report_push.py
```

Expected: PASS; Outbox assertions still show `message_type == "markdown"`.

- [ ] **Step 5: Confirm transport code remains unchanged and lint**

Run:

```powershell
git diff -- backend/jobs/outbox.py backend/integrations/wecom.py
.\.venv\Scripts\python.exe -m ruff check backend/reports/pre_market_personalized.py tests/unit/test_pre_market_personalized.py
```

Expected: the transport diff is empty and Ruff reports no errors. If a test requires `markdown_v2`, fix the test because that message type violates the approved design; do not widen `wecom.py`.

- [ ] **Step 6: Commit the compatible WeCom summary**

```powershell
git add backend/reports/pre_market_personalized.py tests/unit/test_pre_market_personalized.py
git commit -m "feat(reports): compact pre-market WeCom holdings"
```

---

### Task 4: Render the full GFM table safely on the audit page

**Files:**
- Create: `frontend/src/features/reports/components/ReportMarkdown.tsx`
- Create: `frontend/src/features/reports/components/ReportMarkdown.test.tsx`
- Modify: `frontend/src/features/reports/components/ReportViewer.tsx:93`
- Modify: `frontend/src/index.css:1776`

**Interfaces:**
- Consumes: `summary: string` from `ReportDetail`.
- Produces: `ReportMarkdown({ children }: { children: string })`, rendering GFM without raw HTML; signed percentage cells receive `report-value-up` or `report-value-down`.

- [ ] **Step 1: Write the failing frontend component tests**

Create `frontend/src/features/reports/components/ReportMarkdown.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import ReportMarkdown from "./ReportMarkdown";

const summary = `## 持仓概览

| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 当日 | 仓位 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 600036 | 招商银行 | 8,500股 | 38.82 | 39.15 | +0.84% | 34.85% |
| 510300 | 沪深300ETF | 62,000份 | 4.14 | 4.12 | -0.31% | 29.68% |`;

it("renders a GFM table and A-share rise/fall colors", () => {
  render(<ReportMarkdown>{summary}</ReportMarkdown>);

  const table = screen.getByRole("table");
  expect(within(table).getByText("+0.84%")).toHaveClass("report-value-up");
  expect(within(table).getByText("-0.31%")).toHaveClass("report-value-down");
  expect(within(table).getByText("34.85%")).not.toHaveClass(
    "report-value-up",
  );
});

it("does not execute raw HTML from a report", () => {
  render(
    <ReportMarkdown>{`<img src=x onerror="window.__reportXss=true">`}</ReportMarkdown>,
  );

  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
```

The second assertion is the security contract; do not add `rehype-raw` to make the text prettier.

- [ ] **Step 2: Run the component test and verify RED**

Run:

```powershell
pnpm --dir frontend test -- ReportMarkdown.test.tsx
```

Expected: FAIL because `./ReportMarkdown` does not exist.

- [ ] **Step 3: Implement the safe Markdown component**

Create `ReportMarkdown.tsx` with `ReactMarkdown`, `remarkGfm`, and controlled renderers:

```tsx
import { Children, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function plainText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      typeof child === "string" || typeof child === "number" ? String(child) : "",
    )
    .join("")
    .trim();
}

function movementClass(children: ReactNode): string | undefined {
  const value = plainText(children);
  if (/^\+[0-9]+(?:\.[0-9]+)?%$/.test(value)) return "report-value-up";
  if (/^-[0-9]+(?:\.[0-9]+)?%$/.test(value)) return "report-value-down";
  return undefined;
}

export default function ReportMarkdown({ children }: { children: string }) {
  return (
    <div className="report-summary-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children: tableChildren }) => (
            <div className="report-markdown-table-scroll">
              <table>{tableChildren}</table>
            </div>
          ),
          td: ({ children: cellChildren }) => (
            <td
              className={movementClass(cellChildren)}
            >
              {cellChildren}
            </td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
```

Keep the strict signed-percentage regex so unsigned weight cells remain neutral. The table cells generated by this feature contain plain text only; no recursive HTML traversal or raw-node rendering is needed.

- [ ] **Step 4: Run the component test and verify GREEN**

Run:

```powershell
pnpm --dir frontend test -- ReportMarkdown.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Integrate `ReportMarkdown` into `ReportViewer`**

Import the component and replace only the summary paragraph:

```tsx
import ReportMarkdown from "./ReportMarkdown";
```

```tsx
<ReportMarkdown>{report.summary}</ReportMarkdown>
```

Keep report items, evidence actions, model metadata, timestamps, and version history unchanged.

- [ ] **Step 6: Add responsive report-only table styles**

Replace `.report-summary-text` in `frontend/src/index.css` with report-scoped styles:

```css
.report-summary-markdown {
  color: var(--slate-700);
  font-size: 11px;
  line-height: 1.7;
}
.report-summary-markdown > :first-child { margin-top: 0; }
.report-summary-markdown > :last-child { margin-bottom: 0; }
.report-markdown-table-scroll {
  max-width: 100%;
  margin: 12px 0 16px;
  overflow-x: auto;
}
.report-summary-markdown table {
  width: 100%;
  min-width: 720px;
  border-collapse: collapse;
}
.report-summary-markdown th,
.report-summary-markdown td {
  padding: 9px 10px;
  border: 1px solid var(--slate-200);
  text-align: left;
  white-space: nowrap;
}
.report-summary-markdown th {
  background: var(--slate-50);
  font-weight: 700;
}
.report-value-up { color: #dc2626; font-weight: 700; }
.report-value-down { color: #059669; font-weight: 700; }
```

The existing root token `--slate-50: #f8fafc` is present and must be reused; do not introduce a duplicate color token.

- [ ] **Step 7: Add a page-level regression assertion**

Add this test to `frontend/src/features/reports/reports.test.tsx`; it proves the viewer integration, not only the isolated component:

```tsx
it("renders a holdings Markdown table in the report summary", () => {
  reportState.detail = {
    ...reportDetailFixture,
    summary: `## 持仓概览

| 代码 | 当日 |
| --- | ---: |
| 600036 | +0.84% |`,
  };

  renderWithSession(<ReportsPage />, "/reports?reportId=R-POST-20260823");

  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("+0.84%")).toHaveClass("report-value-up");
});
```

- [ ] **Step 8: Run frontend tests, typecheck, lint, and build**

Run:

```powershell
pnpm --dir frontend test -- ReportMarkdown.test.tsx reports.test.tsx
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend build
```

Expected: all commands exit 0 and Vite creates the production bundle.

- [ ] **Step 9: Commit the audit-page renderer**

```powershell
git add frontend/src/features/reports/components/ReportMarkdown.tsx frontend/src/features/reports/components/ReportMarkdown.test.tsx frontend/src/features/reports/components/ReportViewer.tsx frontend/src/features/reports/reports.test.tsx frontend/src/index.css
git commit -m "feat(frontend): render report holdings tables"
```

---

### Task 5: Run the cross-layer acceptance suite and document verification

**Files:**
- Modify only if an assertion reveals a defect: files already listed in Tasks 1–4.
- Do not create migrations, change transport message types, or edit deployment secrets.

**Interfaces:**
- Consumes: committed backend quote/table/push contracts and frontend Markdown renderer.
- Produces: a clean branch whose focused and full verification suites pass.

- [ ] **Step 1: Run all focused backend report and delivery tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_report.py tests/unit/test_pre_market_context_p2.py tests/unit/test_pre_market_personalized.py tests/unit/test_personalized_report_push.py tests/unit/test_outbox_delivery.py tests/unit/test_split_pre_market_runner.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the full backend quality gate**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check backend tests scripts
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: Ruff and the entire pytest suite exit 0.

- [ ] **Step 3: Run the full frontend quality gate**

Run:

```powershell
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend lint
pnpm --dir frontend build
```

Expected: Vitest, TypeScript, ESLint, and the production build all exit 0.

- [ ] **Step 4: Inspect the final diff for forbidden scope changes**

Run:

```powershell
git diff d121067...HEAD -- backend/jobs/outbox.py backend/integrations/wecom.py migrations
git diff --check
git status --short
```

Expected: no Outbox/WeCom/migration behavior change, no whitespace errors, and no uncommitted files.

- [ ] **Step 5: Review the generated contract with a deterministic sample**

Run the formatter unit test with verbose output and verify its expected Markdown remains:

```markdown
| 代码 | 名称 | 持仓 | 成本价 | 最新价 | 当日 | 仓位 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 600036 | 招商银行⚠️ | 8,500股 | 38.82 | 39.15 | +0.84% | 34.85% |
| 510300 | 沪深300ETF | 62,000份 | 4.14 | 4.12 | -0.31% | 29.68% |
```

Expected: the complete report has the table; WeCom has compact rows and its existing report link; 08:30 shared macro output contains neither symbol.

- [ ] **Step 6: Create a final verification commit only if Task 5 required fixes**

If Task 5 changed code, stage only those reviewed fixes and commit:

```powershell
git add backend frontend tests
git commit -m "test(reports): verify pre-market holdings delivery"
```

If Task 5 changed nothing, do not create an empty commit.

- [ ] **Step 7: Prepare deployment handoff without pushing**

Record:

- branch name and all new commit hashes;
- backend and frontend verification command results;
- confirmation that no migration is required;
- manual production validation steps: generate `PRE_MARKET_PERSONALIZED`, open its report detail, verify table/colors, then inspect the corresponding SENT Outbox message and link.

Do not push until the user explicitly authorizes the target remote and branch.
