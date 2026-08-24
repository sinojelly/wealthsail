"""Outbox consumer: transactional message scanning with exponential backoff.

State machine (docs/10 §6.2): PENDING → SENDING → SENT, with retry via
available_at = now + base·2^(attempt-1) seconds and terminal DEAD after
max attempts. Delivery uses the WeCom adapter; without channel credentials
messages remain PENDING with backoff (never silently dropped, never faked as
sent). Rows are claimed with FOR UPDATE SKIP LOCKED so multiple workers can
poll concurrently without double-delivery.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from backend.common.database import Pool, system_connection
from backend.config import Settings

logger = logging.getLogger("wealthsail.outbox")

LOCK_STALE_SECONDS = 300


async def deliver_batch(pool: Pool, settings: Settings, limit: int = 20) -> int:
    """Deliver one batch of due outbox rows; returns the processed count."""
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT id, tenant_id, topic, payload
            FROM workflow.outbox
            WHERE status IN ('PENDING', 'SENDING')
              AND available_at <= now()
              AND (locked_at IS NULL OR locked_at < now() - make_interval(secs => $2))
            ORDER BY created_at ASC
            LIMIT $1
            FOR UPDATE SKIP LOCKED
            """,
            limit,
            LOCK_STALE_SECONDS,
        )
        processed = 0
        for row in rows:
            outbox_id = str(row["id"])
            await conn.execute(
                """
                UPDATE workflow.outbox
                   SET status = 'SENDING', locked_at = now(), locked_by = $2,
                       attempt_count = attempt_count + 1
                 WHERE id = $1
                """,
                outbox_id,
                "worker:outbox",
            )
            try:
                external_id = await _deliver(row, settings)
            except Exception as exc:  # noqa: BLE001 — per-message failure handling
                await _mark_retry_or_dead(conn, outbox_id, settings, exc)
                processed += 1
                continue

            await conn.execute(
                """
                UPDATE workflow.outbox
                   SET status = 'SENT', sent_at = now(),
                       external_message_id = $2, locked_at = NULL, locked_by = NULL
                 WHERE id = $1
                """,
                outbox_id,
                external_id,
            )
            processed += 1
        return processed


async def _deliver(row, settings: Settings) -> str:
    """Deliver one message. Returns the external channel receipt id.

    Raises NotImplementedError when no delivery channel is configured — the
    row then follows the backoff/DEAD path instead of a fabricated success.
    """
    if not settings.wecom_corp_secret:
        raise NotImplementedError("WECOM_CORP_SECRET 未配置，无可用投递通道")
    # TODO(integration): route by topic to the WeCom active-message adapter
    # (docs/10 §3.3 envelope-decrypted routing). Until credentials exist, rows
    # retry with backoff and eventually move to DEAD with a clear error class.
    raise NotImplementedError("WeCom active-message adapter not configured")


async def _mark_retry_or_dead(conn, outbox_id: str, settings: Settings, exc: Exception) -> None:
    attempt = await conn.fetchval(
        "SELECT attempt_count FROM workflow.outbox WHERE id = $1", outbox_id
    )
    error_class = exc.__class__.__name__
    if attempt >= settings.outbox_max_attempts:
        await conn.execute(
            """
            UPDATE workflow.outbox
               SET status = 'DEAD', last_error_class = $2,
                   locked_at = NULL, locked_by = NULL
             WHERE id = $1
            """,
            outbox_id,
            error_class,
        )
        logger.warning("outbox %s moved to DEAD (%s)", outbox_id, error_class)
        return
    backoff = settings.outbox_base_backoff_seconds * (2 ** (attempt - 1))
    await conn.execute(
        """
        UPDATE workflow.outbox
           SET status = 'PENDING', last_error_class = $2,
               available_at = now() + make_interval(secs => $3),
               locked_at = NULL, locked_by = NULL
         WHERE id = $1
        """,
        outbox_id,
        error_class,
        backoff,
    )
    logger.info(
        "outbox %s retry in %ss after %s (attempt %d)",
        outbox_id, backoff, error_class, attempt,
    )


class OutboxConsumer:
    """Background poll loop for the outbox table."""

    def __init__(self, pool: Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="wealthsail-outbox")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        logger.info(
            "outbox consumer started (poll=%.1fs)", self._settings.outbox_poll_interval_seconds
        )
        while True:
            try:
                processed = await deliver_batch(self._pool, self._settings)
                if processed:
                    logger.info("outbox delivered %d messages", processed)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — consumer must survive poll failures
                logger.exception("outbox poll failed")
            await asyncio.sleep(self._settings.outbox_poll_interval_seconds)
