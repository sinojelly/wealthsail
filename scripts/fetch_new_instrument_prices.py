"""为新持仓注册标的并抓取真实行情价（成本锚点）。

用法：python scripts/fetch_new_instrument_prices.py
输出：/tmp/new_instrument_prices.json  {ticker: {"price": "...", "type": "CN_STOCK|EXCHANGE_FUND|OFF_EXCHANGE_FUND", "name": "..."}}
"""
from __future__ import annotations

import asyncio
import json

from backend.common.database import create_pool
from backend.config import get_settings
from backend.integrations import market_data as md

# 新增标的：ticker → (类型, 名称)
NEW_INSTRUMENTS: dict[str, tuple[str, str]] = {
    "000858": ("CN_STOCK", "五粮液"),
    "300750": ("CN_STOCK", "宁德时代"),
    "601318": ("CN_STOCK", "中国平安"),
    "600036": ("CN_STOCK", "招商银行"),
    "588000": ("EXCHANGE_FUND", "科创50ETF"),
    "159915": ("EXCHANGE_FUND", "创业板ETF"),
    "005827": ("OFF_EXCHANGE_FUND", "易方达蓝筹精选"),
}

STOCK = [t for t, (typ, _) in NEW_INSTRUMENTS.items() if typ == "CN_STOCK"]
ETF = [t for t, (typ, _) in NEW_INSTRUMENTS.items() if typ == "EXCHANGE_FUND"]
FUND = [t for t, (typ, _) in NEW_INSTRUMENTS.items() if typ == "OFF_EXCHANGE_FUND"]


async def main() -> None:
    settings = get_settings()
    pool = await create_pool(settings.worker_database_url, min_size=1, max_size=2)
    try:
        # 1. 注册标的（幂等）
        async with pool.acquire() as conn:
            market = {"CN_STOCK": "SSE", "EXCHANGE_FUND": "SSE", "OFF_EXCHANGE_FUND": "FUND_CN"}
            for ticker, (typ, name) in NEW_INSTRUMENTS.items():
                instrument_id = f"20000000-0000-0000-0000-{int(ticker):012d}"
                await conn.execute(
                    """
                    INSERT INTO reference.instruments
                        (id, asset_type, primary_market, trading_currency, canonical_name, lifecycle_status)
                    VALUES ($1, $2, $3, 'CNY', $4, 'ACTIVE')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    instrument_id, typ, market[typ], name,
                )
                await conn.execute(
                    """
                    INSERT INTO reference.instrument_aliases
                        (instrument_id, alias_type, alias_value, market, effective_from)
                    VALUES ($1, 'TICKER', $2, $3, '2026-01-01')
                    ON CONFLICT (alias_type, alias_value, market, effective_from) DO NOTHING
                    """,
                    instrument_id, ticker, market[typ],
                )
                print(f"registered {ticker} {name} ({typ}) -> {instrument_id}")

        # 2. 抓真实行情
        observations = await md.fetch_all(STOCK, ETF, FUND)
        prices: dict[str, dict] = {}
        for obs in observations:
            if obs.quality_status == md.Q_UNAVAILABLE:
                continue
            typ = "CN_STOCK" if obs.subject_type == "INSTRUMENT" and obs.symbol in STOCK else (
                "EXCHANGE_FUND" if obs.subject_type == "INSTRUMENT" and obs.symbol in ETF else "OFF_EXCHANGE_FUND"
            )
            if obs.symbol not in NEW_INSTRUMENTS:
                continue
            prices[obs.symbol] = {
                "price": str(obs.value),
                "type": typ,
                "name": NEW_INSTRUMENTS[obs.symbol][1],
                "quality": obs.quality_status,
                "method": obs.method_version,
            }
        with open("/tmp/new_instrument_prices.json", "w", encoding="utf-8") as f:
            json.dump(prices, f, ensure_ascii=False, indent=2)
        for ticker, info in prices.items():
            print(f"price {ticker} {info['name']}: {info['price']} ({info['quality']}, {info['method']})")
        print(f"\n{len(prices)}/{len(NEW_INSTRUMENTS)} 只有价")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
