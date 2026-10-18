"""One durable public collection per Shanghai day, with no model execution."""

import asyncio
from datetime import datetime, time, timedelta
import json
import logging
import re

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.integrations.alpha_collectors import collect_alpha_public
from backend.macro.alpha_repository import (
    ARRAYS,
    claim_alpha_batch,
    empty_public,
    finish_alpha_batch,
    local,
)

logger = logging.getLogger("wealthsail.alpha_public")


def _safe_error(error):
    name = type(error).__name__
    return (
        name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name) else "CollectionError"
    )


async def sync_alpha_public(
    pool,
    now=None,
    *,
    collector=collect_alpha_public,
    now_provider=now_shanghai,
    nav_collector=None,
    debug_request_id=None,
):
    stamp = local(now if now is not None else now_provider())
    async with system_connection(pool) as conn:
        async with conn.transaction():
            claim = await claim_alpha_batch(
                conn,
                stamp,
                **(
                    {"debug_request_id": debug_request_id}
                    if debug_request_id is not None
                    else {}
                ),
            )
    if claim is None:
        return {"status": "SKIPPED", "runDate": stamp.date().isoformat()}
    # The UNIQUE run_date has committed. Neither public collector owns this conn.
    status, payload = "FAILED", None
    try:
        raw = await collector(stamp)
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("sourceResults"), dict)
            or any(
                not isinstance(raw.get(key), list)
                for key in ("observations", "catalysts", "odds", "launches")
            )
        ):
            raise ValueError("invalid public collection contract")
        payload = empty_public()
        payload.update(
            {key: raw[key] for key in ("methodVersion", "asOf") if key in raw}
        )
        for key in ARRAYS:
            payload[key] = raw.get(key, [])
        payload["sourceResults"] = raw["sourceResults"]
        if nav_collector is None:
            from backend.investment.discovery import collect_discovery_nav_histories

            nav_collector = collect_discovery_nav_histories
        try:
            nav = await nav_collector(pool, stamp)
            if not isinstance(nav.get("fundHistories"), list) or not isinstance(
                nav.get("themeNavSourceResults"), dict
            ):
                raise ValueError("invalid theme NAV public contract")
            payload.update(
                fundHistories=nav["fundHistories"],
                themeNavSourceResults=nav["themeNavSourceResults"],
            )
        except Exception as error:  # noqa: BLE001 -- NAV failure keeps usable macro evidence
            payload["themeNavSourceResults"] = {
                "THEME_NAV": {"status": "FAILED", "errorClass": _safe_error(error)}
            }
        results = list(payload["sourceResults"].values()) + list(
            payload["themeNavSourceResults"].values()
        )
        count = sum(len(payload.get(key, [])) for key in ARRAYS)
        if not count:
            raise ValueError("public collection contains no usable records")
        nav_complete = all(
            isinstance(row, dict)
            and row.get("status") == "SUCCESS"
            and type(row.get("records")) is int
            and row["records"] > 0
            for row in payload["themeNavSourceResults"].values()
        )
        status = (
            "READY"
            if results
            and nav_complete
            and all(
                isinstance(row, dict) and row.get("status") == "SUCCESS"
                for row in results
            )
            else "PARTIAL"
        )
        # Reject nonfinite/unserializable data before any persistence; preserve
        # the provider's original observation and retrieval times verbatim.
        payload = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except asyncio.CancelledError:
        payload = {"errorClass": "CancelledError"}
        raise
    except Exception as error:  # noqa: BLE001 -- never persist source bodies or exception messages
        payload = {"errorClass": _safe_error(error)}
        status = "FAILED"
    finally:
        completed = local(now_provider())
        if completed < stamp:
            # A regressed clock cannot manufacture a future completion instant.
            # Keep the already-consumed claim, so a restart still cannot retry.
            logger.warning("alpha_public_clock_regressed")
        else:
            async with system_connection(pool) as conn:
                async with conn.transaction():
                    await finish_alpha_batch(
                        conn,
                        claim["id"],
                        completed,
                        status,
                        payload or {"errorClass": "CollectionError"},
                    )
    if completed < stamp:
        return {
            "status": "CLAIMED",
            "runDate": stamp.date().isoformat(),
            "errorClass": "ClockRegression",
        }
    return {
        "status": status,
        "runDate": stamp.date().isoformat(),
        "batchId": str(claim["id"]),
        "completedAt": completed.isoformat(),
        **({"errorClass": payload["errorClass"]} if status == "FAILED" else {}),
    }


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_alpha_public_service(
    pool, *, stop_event=None, wait=_wait, now_provider=now_shanghai
):
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        now = local(now_provider())
        due = datetime.combine(now.date(), time(8, 30), now.tzinfo)
        if now >= due:
            try:
                await sync_alpha_public(pool, now, now_provider=now_provider)
            except Exception as error:  # noqa: BLE001 -- independent public service
                logger.warning(
                    "alpha_public_service_failed error_class=%s", _safe_error(error)
                )
            due += timedelta(days=1)
        if not stop_event.is_set():
            await wait(
                stop_event,
                min(3600, max(1, (due - local(now_provider())).total_seconds())),
            )
