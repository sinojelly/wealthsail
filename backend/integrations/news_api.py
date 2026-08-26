"""新闻资讯适配器（免费源，无 API Key）。

fetch_news(symbol, limit) 按股票名称/代码搜索新闻与公告，降级链：

  1. 东方财富公告 API（np-anotice-stock.eastmoney.com，按代码、稳定）；
  2. 新浪财经 7x24 快讯流 + 滚动新闻（feed.mix，本地关键词过滤）；
  3. 360 资讯搜索（关键词搜索，响应不稳定 → 尽力而为）。

统一返回字段：title / time / source / summary / url（url 可能为 None）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx

from backend.integrations.market_data import _sina_search_code

logger = logging.getLogger("wealthsail.news_api")

DEFAULT_TIMEOUT = 10.0
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 东方财富公告 API（与 push2 行情接口不同域名，本机未被风控）
_EM_ANNOUNCE_URL = (
    "https://np-anotice-stock.eastmoney.com/api/security/ann"
    "?sr=-1&page_size={n}&page_index=1&ann_type=A"
    "&client_source=web&stock_list={code}"
)
_EM_NOTICE_DETAIL = "https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"

# 新浪 7x24 快讯流（zhibo_id=152 财经快讯）
_SINA_7X24_URL = (
    "https://zhibo.sina.com.cn/api/zhibo/feed"
    "?page=1&page_size={n}&zhibo_id=152&tag_id=0&dire=f&dpc=1"
)
# 新浪滚动新闻（lid=2516 财经滚动）
_SINA_ROLL_URL = (
    "https://feed.mix.sina.com.cn/api/roll/get"
    "?pageid=153&lid=2516&k=&num={n}&page=1"
)
_SINA_7X24_PAGE = "https://finance.sina.com.cn/7x24/"

# 360 资讯搜索
_360_NEWS_URL = "https://news.so.com/ns?q={keyword}"


async def fetch_news(symbol: str, limit: int = 5) -> list[dict]:
    """按股票名称或代码搜索新闻与公告，按时间倒序取前 limit 条。"""
    query = (symbol or "").strip()
    if not query:
        return []

    code: str | None = None
    keyword = query
    if re.fullmatch(r"\d{6}", query):
        code = query
    else:
        # 名称 → 代码（东财公告按代码寻址；新浪 suggest 免费可用）
        try:
            code = await _sina_search_code(query)
        except Exception:  # noqa: BLE001
            code = None

    items: list[dict] = []
    seen: set[str] = set()

    def _merge(batch: list[dict]) -> None:
        for item in batch:
            key = item.get("title") or item.get("url") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)

    # 1) 东财公告（最稳定、含真实公告）
    if code:
        try:
            _merge(await _fetch_em_announcements(code, limit))
        except Exception:  # noqa: BLE001
            logger.warning("东财公告抓取失败（继续降级）", exc_info=True)

    # 2) 新浪快讯 + 滚动（本地关键词过滤）
    try:
        _merge(await _fetch_sina_news(keyword, limit * 4))
    except Exception:  # noqa: BLE001
        logger.warning("新浪新闻抓取失败（继续降级）", exc_info=True)

    # 3) 360 资讯搜索（尽力而为，失败静默）
    if len(items) < limit:
        try:
            _merge(await _fetch_360_news(keyword, limit))
        except Exception:  # noqa: BLE001
            logger.debug("360 新闻不可用（忽略）", exc_info=True)

    items.sort(key=lambda r: r.get("time") or "", reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------
# 源 1：东方财富公告
# ---------------------------------------------------------------------------
async def _fetch_em_announcements(code: str, limit: int) -> list[dict]:
    url = _EM_ANNOUNCE_URL.format(n=limit, code=code)
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(
            url,
            headers={"User-Agent": _UA, "Referer": "https://data.eastmoney.com/"},
        )
        response.raise_for_status()
        payload = response.json()
    rows = payload.get("data", {}).get("list") or []
    items: list[dict] = []
    for row in rows:
        art_code = row.get("art_code") or ""
        title = (row.get("title") or "").strip()
        if not title:
            continue
        date_str = (row.get("notice_date") or "")[:10]
        items.append(
            {
                "title": title,
                "time": date_str,
                "source": "东方财富公告",
                "summary": title,  # 公告无独立摘要，标题即摘要
                "url": _EM_NOTICE_DETAIL.format(code=code, art_code=art_code),
            }
        )
    if items:
        logger.info("东财公告: %s 获得 %d 条", code, len(items))
    return items


# ---------------------------------------------------------------------------
# 源 2：新浪 7x24 快讯 + 滚动新闻（本地关键词过滤）
# ---------------------------------------------------------------------------
async def _fetch_sina_news(keyword: str, num: int) -> list[dict]:
    items: list[dict] = []
    headers = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"}
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        # 7x24 快讯
        try:
            response = await client.get(_SINA_7X24_URL.format(n=num), headers=headers)
            response.raise_for_status()
            feed = (
                response.json()
                .get("result", {})
                .get("data", {})
                .get("feed", {})
                .get("list", [])
            )
            for item in feed:
                text = (item.get("rich_text") or "").strip()
                if keyword not in text:
                    continue
                items.append(
                    {
                        "title": text[:80],
                        "time": (item.get("create_time") or "")[:16],
                        "source": "新浪财经7x24",
                        "summary": text[:200],
                        "url": None,
                    }
                )
        except (httpx.HTTPError, ValueError):
            logger.debug("新浪 7x24 快讯不可用", exc_info=True)
        # 滚动新闻
        try:
            response = await client.get(_SINA_ROLL_URL.format(n=num), headers=headers)
            response.raise_for_status()
            rows = response.json().get("result", {}).get("data", [])
            for row in rows:
                haystack = f"{row.get('title') or ''}{row.get('intro') or ''}"
                if keyword not in haystack:
                    continue
                items.append(
                    {
                        "title": (row.get("title") or "").strip(),
                        "time": _ts_to_str(row.get("ctime")),
                        "source": row.get("media_name") or "新浪财经",
                        "summary": (row.get("intro") or "")[:200],
                        "url": row.get("url"),
                    }
                )
        except (httpx.HTTPError, ValueError):
            logger.debug("新浪滚动新闻不可用", exc_info=True)
    return items


def _ts_to_str(value) -> str:
    """Unix 时间戳 → 'YYYY-MM-DD HH:MM'。"""
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


# ---------------------------------------------------------------------------
# 源 3：360 资讯搜索（尽力而为）
# ---------------------------------------------------------------------------
async def _fetch_360_news(keyword: str, limit: int) -> list[dict]:
    url = _360_NEWS_URL.format(keyword=keyword)
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            url, headers={"User-Agent": _UA, "Referer": "https://news.so.com/"}
        )
        response.raise_for_status()
        text = response.text
    items: list[dict] = []
    # 标题 + 链接
    for href, title in re.findall(
        r'<a class="news-title" href="([^"]+)"[^>]*>(.*?)</a>', text, re.S
    ):
        title = re.sub(r"<[^>]+>|\s+", "", title)
        if not title:
            continue
        items.append(
            {
                "title": title[:100],
                "time": "",
                "source": "360资讯",
                "summary": title[:200],
                "url": href,
            }
        )
    return items[:limit]


async def _demo() -> None:
    """快速自检：python -m backend.integrations.news_api"""
    rows = await fetch_news("中际旭创", 5)
    for r in rows:
        print(r)


if __name__ == "__main__":
    import asyncio

    asyncio.run(_demo())
