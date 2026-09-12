"""AkShare market-wide fund identity list for the fund-screener master table.

One provider call returns every open-ended mainland mutual fund; the Worker
persists it into ``reference.fund_master`` on a weekly schedule.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger("wealthsail.fund_master")


@dataclass(frozen=True, slots=True)
class FundMasterEntry:
    fund_code: str
    fund_name: str
    fund_type: str
    pinyin_abbr: str | None
    pinyin_full: str | None


def _cell(row, column: str) -> str | None:
    value = row.get(column) if hasattr(row, "get") else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _entries_from_frame(frame) -> list[FundMasterEntry]:
    if frame is None or getattr(frame, "empty", True) or not hasattr(frame, "iterrows"):
        raise RuntimeError("AkShare fund list response is empty or malformed")

    entries: list[FundMasterEntry] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        raw_code = _cell(row, "基金代码")
        fund_name = _cell(row, "基金简称")
        fund_type = _cell(row, "基金类型")
        if raw_code is None or fund_name is None or fund_type is None:
            continue
        # eastmoney 始终返回 6 位代码；任何短代码都是坏行，不能用补零伪造
        if len(raw_code) != 6 or not raw_code.isdigit() or raw_code in seen:
            continue
        fund_code = raw_code
        seen.add(fund_code)
        entries.append(
            FundMasterEntry(
                fund_code=fund_code,
                fund_name=fund_name,
                fund_type=fund_type,
                pinyin_abbr=_cell(row, "拼音缩写"),
                pinyin_full=_cell(row, "拼音全称"),
            )
        )
    if not entries:
        raise RuntimeError("AkShare fund list response has no usable rows")
    return entries


async def fetch_fund_master_list() -> list[FundMasterEntry]:
    """Fetch the full open-ended fund list; one provider call, all rows."""

    def _fetch():
        import akshare as provider  # noqa: PLC0415

        return provider.fund_name_em()

    try:
        frame = await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.warning(
            "fund_master_fetch_failed error_class=%s", type(exc).__name__
        )
        raise
    return _entries_from_frame(frame)
