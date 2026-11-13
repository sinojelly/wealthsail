"""Bounded public display quotes, independent of frozen research and triggers."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import logging
from time import perf_counter

from backend.common.timing import trace_stage
from backend.dependencies import now_shanghai
from backend.investment.study_intraday import _current_quote, _refresh_time
from backend.investment.study_price_watch import _number, _source_uri, _stamp, _UNITS

logger = logging.getLogger("wealthsail.fund_quotes")


async def _confirmed_observations(codes):
    """The existing disclosed-NAV provider is sequential; fan out at most four."""
    from backend.integrations.market_data import fetch_fund_observations

    semaphore = asyncio.Semaphore(4)

    async def one(code):
        async with semaphore:
            return await fetch_fund_observations([code])

    tasks = [asyncio.create_task(one(code)) for code in codes]
    try:
        done, pending = await asyncio.wait(tasks, timeout=3.5)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return [
            row
            for task in done
            if not task.cancelled() and task.exception() is None
            for row in task.result()
        ]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _percent(value):
    try:
        result = Decimal(str(value))
        return str(result.quantize(Decimal("0.01"))) if result.is_finite() else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def _confirmed(observation, code, now):
    if observation is None:
        return None
    stamp = _stamp(observation.as_of)
    metadata = observation.metadata or {}
    if (
        observation.symbol != code
        or observation.subject_type != "FUND"
        or observation.field_code != "NAV"
        or metadata.get("officialUnitNav") is not True
        or observation.unit not in _UNITS
        or not _number(observation.value)
        or not _source_uri(observation.origin_uri)
        or stamp is None
        or stamp.date() > now.date()
        or (now.date() - stamp.date()).days > 10
    ):
        return None
    return {
        "subjectKind": "nav",
        "subjectId": code,
        "fundCode": code,
        "value": str(observation.value),
        "asOf": stamp.date().isoformat(),
        "retrievedAt": now.isoformat(),
        "sourceUri": observation.origin_uri,
        "priceBasis": "UNIT_NAV",
        "unit": observation.unit,
        "quality": "READY",
        "changePct": _percent(metadata.get("returnPct")),
        "usable": True,
    }


class LatestQuoteCache:
    """Small per-process cache of public facts only; failures are briefly cached."""

    def __init__(self, *, timeout=4):
        self.timeout = timeout
        self.observations = {}
        self.pending = {}
        self.retry_after = {}

    def cached_estimates(self, codes, now):
        """Fast study reads never initiate provider work or await in-flight fetches."""
        return [
            observation
            for code in codes
            for fetched, observation in [
                self.observations.get(("estimate.provider", code), (None, None))
            ]
            if fetched is not None
            and observation is not None
            and timedelta(0) <= now - fetched < timedelta(seconds=60)
        ]

    async def _load(self, kind, codes, now, loader, ttl):
        def expired(code):
            retry_after = self.retry_after.get((kind, code))
            if retry_after is not None and now < retry_after:
                return False
            cached = self.observations.get((kind, code))
            if cached is None:
                return True
            fetched, observation = cached
            # A bounded provider batch can omit work that never even started.
            # Keep successful facts for the full TTL, but retry only its misses
            # after a short cooldown so later codes make progress on each view.
            lifetime = (
                ttl
                if observation is not None and _number(observation.value) is not None
                else min(ttl, timedelta(seconds=5))
            )
            return not timedelta(0) <= now - fetched < lifetime

        missing = [code for code in codes if expired(code)]
        if not missing:
            logger.info(
                "fund_quotes stage=%s cache=hit count=%d duration_ms=0",
                kind,
                len(codes),
            )
            return
        key = kind, tuple(missing)
        pending = self.pending.get(key)
        if pending is None:

            async def fetch():
                started = perf_counter()
                outcome = "ok"
                try:
                    with trace_stage(f"fund_quotes.{kind}"):
                        rows = await asyncio.wait_for(
                            loader(missing), timeout=self.timeout
                        )
                except Exception as exc:
                    outcome = type(exc).__name__
                    rows = []
                by_code = {row.symbol: row for row in rows}
                for code in missing:
                    observation = by_code.get(code)
                    if (
                        observation is not None
                        and _number(observation.value) is not None
                    ):
                        self.observations[kind, code] = now, observation
                        self.retry_after.pop((kind, code), None)
                    else:
                        # A failed refresh must not erase the last disclosed
                        # fact or change its real retrieval timestamp. Retry
                        # promptly without repeatedly calling an unavailable
                        # provider during the same short cooldown.
                        self.observations.setdefault((kind, code), (now, None))
                        self.retry_after[kind, code] = now + timedelta(seconds=5)
                # No user identities or holdings enter this shared cache.
                if len(self.observations) > 512:
                    oldest = sorted(
                        self.observations, key=lambda entry: self.observations[entry][0]
                    )
                    for entry in oldest[: len(self.observations) - 512]:
                        self.observations.pop(entry, None)
                        self.retry_after.pop(entry, None)
                logger.info(
                    "fund_quotes stage=%s cache=miss count=%d available=%d outcome=%s duration_ms=%.1f",
                    kind,
                    len(missing),
                    len(rows),
                    outcome,
                    (perf_counter() - started) * 1000,
                )

            pending = asyncio.create_task(fetch())
            self.pending[key] = pending

            def discard(task):
                if self.pending.get(key) is task:
                    self.pending.pop(key, None)

            pending.add_done_callback(discard)
        try:
            await asyncio.shield(pending)
        finally:
            if pending.done() and self.pending.get(key) is pending:
                self.pending.pop(key, None)

    async def get(
        self,
        codes,
        now,
        *,
        trading_day,
        estimate_loader=None,
        confirmed_loader=None,
        now_provider=now_shanghai,
        allow_external_fetch=False,
    ):
        from backend.integrations.market_data import fetch_fund_intraday_estimates

        now = _stamp(now)
        codes = sorted(set(codes))
        fetch_estimate = trading_day is True and _refresh_time(now)
        if allow_external_fetch:
            work = [
                self._load(
                    "confirmed.provider",
                    codes,
                    now,
                    confirmed_loader or _confirmed_observations,
                    timedelta(minutes=5),
                )
            ]
            if fetch_estimate:
                work.append(
                    self._load(
                        "estimate.provider",
                        codes,
                        now,
                        estimate_loader or fetch_fund_intraday_estimates,
                        timedelta(seconds=60),
                    )
                )
            await asyncio.gather(*work)
        finished = _stamp(now_provider()) if allow_external_fetch else now
        finished = finished if finished and finished >= now else now
        items = []
        for code in codes:
            official_fetched, official = self.observations.get(
                ("confirmed.provider", code), (None, None)
            )
            estimate_fetched, raw = self.observations.get(
                ("estimate.provider", code), (None, None)
            )
            plan = {
                "subjectKind": "nav",
                "subjectId": code,
                "fundCode": code,
                "priceBasis": "UNIT_NAV",
                "unit": "元/份",
                "asOf": now.date().isoformat(),
            }
            confirmed = _confirmed(official, code, finished)
            estimate = (
                _current_quote(raw, plan, finished)
                if fetch_estimate and raw is not None and finished.date() == now.date()
                else None
            )
            if confirmed is not None:
                confirmed["retrievedAt"] = official_fetched.isoformat()
            if estimate is not None:
                estimate["retrievedAt"] = estimate_fetched.isoformat()
            items.append(
                {
                    "fundCode": code,
                    "confirmedNav": confirmed,
                    "currentEstimate": estimate,
                }
            )
        return deepcopy({"items": items, "asOf": finished.isoformat()})


latest_quote_cache = LatestQuoteCache()


async def read_local_fund_quotes(conn, codes, now):
    """Public, dated NAV coordinates only; never wait on a provider task."""
    from backend.funds.nav_history import load_nav_histories, select_history_view

    codes = sorted(set(codes))
    cached = await latest_quote_cache.get(
        codes, now, trading_day=True, allow_external_fetch=False
    )
    histories = await load_nav_histories(conn, codes, now)
    for row in cached["items"]:
        code = row["fundCode"]
        view = select_history_view(
            code, code, "2y", [h for h in histories if h["fundCode"] == code], now
        )
        if not view.get("points"):
            continue
        point = view["points"][-1]
        current = row.get("confirmedNav")
        if current and (current.get("asOf") or "") >= point["date"]:
            continue
        row["confirmedNav"] = {
            "subjectKind": "nav",
            "subjectId": code,
            "fundCode": code,
            "value": str(point["unitNav"]),
            "asOf": point["date"],
            "retrievedAt": view.get("retrievedAt"),
            "sourceUri": view.get("sourceUri"),
            "priceBasis": "UNIT_NAV",
            "unit": view.get("unit", "CNY/份"),
            "quality": view["state"],
            "usable": view["state"] == "READY",
            "changePct": None,
        }
    return cached
