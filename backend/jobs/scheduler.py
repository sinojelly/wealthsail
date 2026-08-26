"""Four-session scheduler (PRE_MARKET / INTRADAY / FUND_1430 / POST_MARKET).

A lightweight asyncio tick loop (no APScheduler dependency, per the pinned
requirements) matches each session's cron semantics and only fires on trading
days (reference.trading_calendars, falling back to Mon–Fri). Job execution
itself is delegated to the lease runner, so a crashed worker is recovered by
the next tick.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from backend.config import Settings

logger = logging.getLogger("wealthsail.scheduler")

SESSION_ORDER = ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET")

SESSION_LABELS = {
    "PRE_MARKET": "盘前简报",
    "INTRADAY": "盘中跟踪",
    "FUND_1430": "基金 2:30 报告",
    "POST_MARKET": "盘后交互复盘",
}


@dataclass(frozen=True)
class SessionSchedule:
    session: str
    label: str
    # cron-ish: minute, hour, dow list; INTRADAY uses every-N-minutes over a range.
    minute: int
    hour: int
    dow: tuple[int, ...] = (1, 2, 3, 4, 5)
    every_minutes: int = 0  # >0 → fire every N minutes while inside the hour range


SCHEDULES: tuple[SessionSchedule, ...] = (
    SessionSchedule("PRE_MARKET", "盘前简报", minute=30, hour=8),
    SessionSchedule("INTRADAY", "盘中跟踪", minute=0, hour=9, every_minutes=5),
    SessionSchedule("FUND_1430", "基金 2:30 报告", minute=30, hour=14),
    SessionSchedule("POST_MARKET", "盘后复盘", minute=30, hour=15),
)


def is_due(schedule: SessionSchedule, now: datetime) -> bool:
    if now.weekday() + 1 not in schedule.dow:
        return False
    if schedule.every_minutes > 0:
        # INTRADAY: 09:30 – 15:00 window, every N minutes.
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        end = now.replace(hour=15, minute=0, second=0, microsecond=0)
        if not (start <= now < end):
            return False
        return now.minute % schedule.every_minutes == 0
    return now.hour == schedule.hour and now.minute == schedule.minute


async def is_trading_day(conn, day: datetime) -> bool:
    """True unless the trading calendar explicitly marks the day non-trading."""
    row = await conn.fetchrow(
        """
        SELECT is_trading_day
        FROM reference.trading_calendars
        WHERE market = 'SSE' AND calendar_date = $1
        """,
        day.date(),
    )
    if row is not None:
        return bool(row["is_trading_day"])
    return day.weekday() < 5  # calendar fallback: Mon–Fri


class Scheduler:
    """Tick loop that dispatches due sessions to an execution callback."""

    def __init__(
        self,
        settings: Settings,
        execute: Callable[[str, datetime], Awaitable[None]],
        *,
        trading_day_check: Callable[[datetime], Awaitable[bool]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._execute = execute
        self._trading_day_check = trading_day_check
        timezone = ZoneInfo(settings.timezone)
        self._now_provider = now_provider or (lambda: datetime.now(timezone))
        self._task: asyncio.Task | None = None
        self._last_fired: set[tuple[str, str]] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._loop(), name="wealthsail-scheduler"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        logger.info("scheduler started (tick=%.1fs)", self._settings.scheduler_tick_seconds)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — scheduler must survive tick failures
                logger.exception("scheduler tick failed")
            await asyncio.sleep(self._settings.scheduler_tick_seconds)

    async def _tick(self) -> None:
        now = self._now_provider()
        if now.tzinfo is None:
            raise ValueError("scheduler clock must return a timezone-aware datetime")
        due = [schedule for schedule in SCHEDULES if is_due(schedule, now)]
        if not due:
            return
        if self._trading_day_check is not None and not await self._trading_day_check(now):
            logger.info("calendar marks %s as non-trading; sessions skipped", now.date())
            return
        for schedule in due:
            key = (schedule.session, now.strftime("%Y%m%d%H%M"))
            if key in self._last_fired:
                continue
            self._last_fired.add(key)
            # Bound the memory of fired keys.
            if len(self._last_fired) > 512:
                self._last_fired.clear()
            logger.info("dispatching %s at %s", schedule.session, now.isoformat())
            try:
                await self._execute(schedule.session, now)
            except Exception:  # noqa: BLE001
                logger.exception("scheduler execution failed for %s", schedule.session)
