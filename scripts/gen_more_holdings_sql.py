"""根据真实行情价生成持仓插入 SQL（幂等，lenovo 超级用户执行）。

王芳 tenant 003 新增 6 只；张明 tenant 001 新增 4 只。
成本价 = 抓取时真实价（期初导入语义，首日 pnl≈0）。
"""
from __future__ import annotations

import json

with open("/tmp/new_instrument_prices.json", encoding="utf-8") as f:
    PRICES = json.load(f)

INSTRUMENT_IDS = {
    "000858": "20000000-0000-0000-0000-000000000858",
    "300750": "20000000-0000-0000-0000-000000300750",
    "601318": "20000000-0000-0000-0000-000000601318",
    "600036": "20000000-0000-0000-0000-000000600036",
    "588000": "20000000-0000-0000-0000-000000588000",
    "159915": "20000000-0000-0000-0000-000000159915",
    "005827": "20000000-0000-0000-0000-000000005827",
}

# (ticker, quantity) 王芳 6 只 + 张明 4 只
WANGFANG = [
    ("000858", 500), ("300750", 200), ("601318", 800),
    ("588000", 60000), ("159915", 30000), ("005827", 40000),
]
ZHANGMING = [
    ("000858", 300), ("600036", 1500), ("588000", 50000), ("005827", 30000),
]

TENANTS = {
    "wangfang": {
        "tenant": "00000000-0000-0000-0000-000000000003",
        "account": "30000000-0000-0000-0000-000000000004",
        "topup_id": "40000000-0000-0000-0000-000000000106",
        "topup_amount": "400000.00",
        "buy_start": 0x107,
        "holdings": WANGFANG,
        "key_suffix": "wf",
    },
    "zhangming": {
        "tenant": "00000000-0000-0000-0000-000000000001",
        "account": "30000000-0000-0000-0000-000000000001",
        "topup_id": "40000000-0000-0000-0000-000000000201",
        "topup_amount": "200000.00",
        "buy_start": 0x202,
        "holdings": ZHANGMING,
        "key_suffix": "zm",
    },
}

out: list[str] = []
out.append("-- 生成：真实行情价成本锚定持仓（王芳 6 只 / 张明 4 只）")
out.append("BEGIN;")
out.append("")

for name, t in TENANTS.items():
    out.append(f"-- ============ {name} ============")
    # 补充本金
    out.append(
        f"""INSERT INTO ledger.ledger_events
    (id, tenant_id, account_id, event_type, status, trade_date, occurred_at,
     idempotency_key, description, schema_version)
VALUES ('{t['topup_id']}', '{t['tenant']}', '{t['account']}',
        'CASH_IN', 'CONFIRMED', CURRENT_DATE, now(),
        'seed-topup-{t['key_suffix']}-20260824', '补充本金（多持仓测试）', 1)
ON CONFLICT (id) DO NOTHING;"""
    )
    out.append(
        f"""INSERT INTO ledger.ledger_legs (tenant_id, event_id, leg_type, quantity, amount, unit_price)
VALUES ('{t['tenant']}', '{t['topup_id']}', 'CASH', NULL, {t['topup_amount']}, NULL);"""
    )
    # 买入事件
    for i, (ticker, qty) in enumerate(t["holdings"]):
        price = PRICES[ticker]["price"]
        amount = round(float(price) * qty, 2)
        event_id = f"40000000-0000-0000-0000-{t['buy_start'] + i:012d}"
        instrument_id = INSTRUMENT_IDS[ticker]
        key = f"seed-buy-{t['key_suffix']}-{ticker}-20260824"
        out.append(
            f"""INSERT INTO ledger.ledger_events
    (id, tenant_id, account_id, event_type, status, trade_date, occurred_at,
     idempotency_key, description, schema_version)
VALUES ('{event_id}', '{t['tenant']}', '{t['account']}',
        'BUY', 'CONFIRMED', CURRENT_DATE, now(),
        '{key}', '测试持仓 {ticker} {qty} 股 @{price}', 1)
ON CONFLICT (id) DO NOTHING;"""
        )
        out.append(
            f"""INSERT INTO ledger.ledger_legs (tenant_id, event_id, leg_type, instrument_id, quantity, amount, unit_price)
VALUES ('{t['tenant']}', '{event_id}', 'INSTRUMENT', '{instrument_id}', {qty}, NULL, {price}),
       ('{t['tenant']}', '{event_id}', 'CASH', NULL, NULL, -{amount:.2f}, NULL);"""
        )
    # 投影快照（upsert）
    out.append("-- 投影锚点（真实价成本，refresh_market 将刷新市值）")
    for i, (ticker, qty) in enumerate(t["holdings"]):
        price = PRICES[ticker]["price"]
        amount = round(float(price) * qty, 2)
        instrument_id = INSTRUMENT_IDS[ticker]
        out.append(
            f"""INSERT INTO ledger.position_snapshots
    (tenant_id, account_id, instrument_id, as_of, quantity, avg_cost,
     market_price, market_value, unrealized_pnl, data_quality_status)
VALUES ('{t['tenant']}', '{t['account']}', '{instrument_id}', CURRENT_DATE,
        {qty}, {price}, {price}, {amount:.2f}, 0.00, 'STALE')
ON CONFLICT (tenant_id, account_id, instrument_id, as_of) DO UPDATE
SET quantity = EXCLUDED.quantity, avg_cost = EXCLUDED.avg_cost,
    market_price = EXCLUDED.market_price, market_value = EXCLUDED.market_value,
    unrealized_pnl = EXCLUDED.unrealized_pnl,
    data_quality_status = EXCLUDED.data_quality_status;"""
        )
    out.append("")

out.append("COMMIT;")

with open("/tmp/seed_more_holdings.sql", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"SQL 生成完毕: /tmp/seed_more_holdings.sql ({len(out)} 行)")

# 校验：各租户买入总额
for name, t in TENANTS.items():
    total = sum(round(float(PRICES[ticker]["price"]) * qty, 2) for ticker, qty in t["holdings"])
    print(f"{name}: 买入合计 {total:,.2f}（补充本金 {t['topup_amount']}）")
