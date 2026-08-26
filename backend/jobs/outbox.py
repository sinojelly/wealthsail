"""Reliable Outbox delivery with short atomic leases and fail-closed routing.

The database transaction only claims rows. Network I/O happens after the
connection is released, and every final state transition is a compare-and-set
on ``claim_token``. Recipients are resolved from an ACTIVE tenant-owned
channel binding; a generic event payload can never choose an external user.

An ambiguous transport result is marked ``UNKNOWN`` and is not retried
automatically. This deliberately prefers a visible reconciliation task over a
duplicate financial notification.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from backend.common.database import Pool, system_connection
from backend.common.security import envelope_decrypt
from backend.config import Settings
from backend.integrations.wecom import DeliveryRejectedError, DeliveryUnknownError

logger = logging.getLogger("wealthsail.outbox")


@dataclass(frozen=True)
class DeliveryCommand:
    channel_type: str
    recipient: str
    content: str


def _payload_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row["payload"]
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _default_content(row: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    topic = row["topic"]
    if topic == "REPORT_PUBLISHED":
        return f"📊 您的研报已发布（{payload.get('session', '?')}），请前往 WealthSail 查看。"
    if topic == "ALERT_EVENT":
        return "⚠️ WealthSail 风控告警触发，请及时查看。"
    return f"🔔 WealthSail 新消息（{topic}）"


def build_delivery_command(
    row: Mapping[str, Any],
    binding: Mapping[str, Any],
    *,
    routing_key: str,
) -> DeliveryCommand:
    """Validate a claimed row against its binding and decrypt its route."""
    if str(binding["id"]) != str(row["channel_binding_id"]):
        raise ValueError("channel binding id mismatch")
    if str(binding["tenant_id"]) != str(row["tenant_id"]):
        raise ValueError("channel binding tenant mismatch")
    if binding["status"] != "ACTIVE":
        raise ValueError("channel binding is not ACTIVE")
    ciphertext = binding.get("external_user_ciphertext")
    if not ciphertext:
        raise ValueError("channel binding has no encrypted route")
    payload = _payload_dict(row)
    return DeliveryCommand(
        channel_type=str(binding["channel_type"]),
        recipient=envelope_decrypt(routing_key, str(ciphertext)),
        content=str(payload.get("content") or _default_content(row, payload)),
    )


async def claim_batch(
    pool: Pool,
    *,
    worker_id: str,
    limit: int,
    lease_seconds: int,
) -> list[Any]:
    """Atomically lease due PENDING rows in one short transaction."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            return list(
                await conn.fetch(
                    """
                    WITH candidates AS (
                        SELECT id
                        FROM workflow.outbox
                        WHERE status = 'PENDING'
                          AND available_at <= now()
                          AND (lease_until IS NULL OR lease_until < now())
                        ORDER BY created_at ASC
                        LIMIT $1
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE workflow.outbox AS o
                       SET status = 'SENDING',
                           locked_at = now(),
                           locked_by = $2,
                           claim_token = gen_random_uuid(),
                           lease_until = now() + make_interval(secs => $3),
                           attempt_count = attempt_count + 1
                      FROM candidates c
                     WHERE o.id = c.id
                    RETURNING o.id, o.tenant_id, o.topic, o.payload,
                              o.channel_binding_id, o.attempt_count,
                              o.claim_token
                    """,
                    limit,
                    worker_id,
                    lease_seconds,
                )
            )


async def mark_stale_sending_unknown(pool: Pool) -> int:
    """Quarantine expired in-flight sends instead of blindly replaying them."""
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.outbox
               SET status = 'UNKNOWN', last_error_class = 'LeaseExpiredAfterClaim',
                   locked_at = NULL, locked_by = NULL, claim_token = NULL,
                   lease_until = NULL
             WHERE status = 'SENDING' AND lease_until < now()
            """
        )
    return int(result.split()[-1]) if result else 0


async def _load_binding(pool: Pool, row: Mapping[str, Any]):
    binding_id = row.get("channel_binding_id")
    if not binding_id:
        raise DeliveryRejectedError("outbox row has no channel binding")
    async with system_connection(pool) as conn:
        binding = await conn.fetchrow(
            """
            SELECT id, tenant_id, channel_type, status, external_user_ciphertext
            FROM identity.channel_bindings
            WHERE id = $1 AND tenant_id = $2
            """,
            binding_id,
            row["tenant_id"],
        )
    if binding is None:
        raise DeliveryRejectedError("channel binding is unavailable")
    return binding


async def _send(command: DeliveryCommand, settings: Settings) -> str:
    if command.channel_type != "wecom":
        raise DeliveryRejectedError(f"unsupported channel: {command.channel_type}")
    if not settings.wecom_corp_secret:
        raise DeliveryRejectedError("WECOM_CORP_SECRET is not configured")
    from backend.integrations.wecom import send_message

    return await send_message(settings, command.recipient, command.content)


async def _cas_sent(pool: Pool, row: Mapping[str, Any], receipt: str) -> None:
    async with system_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE workflow.outbox
               SET status = 'SENT', sent_at = now(), external_message_id = $3,
                   locked_at = NULL, locked_by = NULL, claim_token = NULL,
                   lease_until = NULL, last_error_class = NULL
             WHERE id = $1 AND claim_token = $2 AND status = 'SENDING'
            """,
            row["id"],
            row["claim_token"],
            receipt,
        )
    if not result.endswith(" 1"):
        raise RuntimeError("outbox lease lost before SENT finalization")


async def _cas_unknown(pool: Pool, row: Mapping[str, Any], exc: Exception) -> None:
    async with system_connection(pool) as conn:
        await conn.execute(
            """
            UPDATE workflow.outbox
               SET status = 'UNKNOWN', last_error_class = $3,
                   locked_at = NULL, locked_by = NULL, claim_token = NULL,
                   lease_until = NULL
             WHERE id = $1 AND claim_token = $2 AND status = 'SENDING'
            """,
            row["id"],
            row["claim_token"],
            exc.__class__.__name__,
        )


async def _cas_retry_or_dead(
    pool: Pool, row: Mapping[str, Any], settings: Settings, exc: Exception
) -> None:
    attempt = int(row["attempt_count"])
    terminal = attempt >= settings.outbox_max_attempts
    backoff = settings.outbox_base_backoff_seconds * (2 ** max(attempt - 1, 0))
    async with system_connection(pool) as conn:
        await conn.execute(
            """
            UPDATE workflow.outbox
               SET status = $3, last_error_class = $4,
                   available_at = CASE WHEN $3 = 'PENDING'
                       THEN now() + make_interval(secs => $5) ELSE available_at END,
                   locked_at = NULL, locked_by = NULL, claim_token = NULL,
                   lease_until = NULL
             WHERE id = $1 AND claim_token = $2 AND status = 'SENDING'
            """,
            row["id"],
            row["claim_token"],
            "DEAD" if terminal else "PENDING",
            exc.__class__.__name__,
            backoff,
        )


async def deliver_batch(pool: Pool, settings: Settings, limit: int = 20) -> int:
    """Claim, release the DB connection, then deliver a bounded batch."""
    await mark_stale_sending_unknown(pool)
    rows = await claim_batch(
        pool,
        worker_id="worker:outbox",
        limit=limit,
        lease_seconds=settings.outbox_lease_seconds,
    )
    semaphore = asyncio.Semaphore(settings.outbox_delivery_concurrency)

    async def deliver_one(row: Mapping[str, Any]) -> None:
        async with semaphore:
            try:
                binding = await _load_binding(pool, row)
                command = build_delivery_command(
                    row, binding, routing_key=settings.routing_encryption_key
                )
                receipt = await _send(command, settings)
                await _cas_sent(pool, row, receipt)
            except DeliveryUnknownError as exc:
                await _cas_unknown(pool, row, exc)
                logger.warning("outbox %s delivery result is UNKNOWN", row["id"])
            except Exception as exc:  # definitive validation/provider rejection
                await _cas_retry_or_dead(pool, row, settings, exc)
                logger.warning(
                    "outbox %s delivery rejected (%s)",
                    row["id"],
                    exc.__class__.__name__,
                )

    await asyncio.gather(*(deliver_one(row) for row in rows))
    return len(rows)


class OutboxConsumer:
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
        while True:
            try:
                await deliver_batch(self._pool, self._settings)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("outbox poll failed")
            await asyncio.sleep(self._settings.outbox_poll_interval_seconds)
