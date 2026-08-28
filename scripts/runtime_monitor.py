"""Check Worker/database delivery health and send de-duplicated WeCom alerts."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.common.database import create_pool
from backend.common.security import envelope_decrypt
from backend.config import get_settings, validate_runtime_settings
from backend.integrations.wecom import close_http_client, send_message
from backend.operations.monitoring import (
    RuntimeSignal,
    alert_content,
    collect_database_signals,
    due_signals,
    load_active_recipients,
    load_alert_state,
    mark_sent,
    save_alert_state,
)

logger = logging.getLogger("wealthsail.monitor")


def worker_is_active(service: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        check=False,
        timeout=10,
    )
    return result.returncode == 0


async def main() -> int:
    settings = validate_runtime_settings(get_settings(), process="worker")
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state_path = Path(
        os.environ.get(
            "WEALTHSAIL_MONITOR_STATE",
            "/var/lib/wealthsail-monitor/state.json",
        )
    )
    now = datetime.now(timezone.utc)
    signals: list[RuntimeSignal] = []
    if not worker_is_active(settings.monitor_worker_service):
        signals.append(RuntimeSignal("WORKER_STOPPED", settings.monitor_worker_service))

    pool = await create_pool(
        settings.worker_database_url,
        min_size=1,
        max_size=2,
        statement_cache_size=settings.db_statement_cache_size,
        runtime_role="wealthsail_worker",
    )
    try:
        signals.extend(
            await collect_database_signals(
                pool,
                failed_job_window_minutes=settings.monitor_failed_job_window_minutes,
                dead_outbox_window_hours=settings.monitor_dead_outbox_window_hours,
            )
        )
        state = load_alert_state(state_path)
        due = due_signals(
            signals,
            state,
            now=now,
            cooldown=timedelta(minutes=settings.monitor_alert_cooldown_minutes),
        )
        if not due:
            logger.info("runtime health check passed; no new alert")
            return 0

        recipients = await load_active_recipients(pool)
        by_tenant: dict[str, list[str]] = defaultdict(list)
        for row in recipients:
            try:
                by_tenant[str(row["tenant_id"])].append(
                    envelope_decrypt(
                        settings.routing_encryption_key,
                        str(row["external_user_ciphertext"]),
                    )
                )
            except Exception:
                logger.exception("could not decrypt an alert recipient")

        system_signals = [signal for signal in due if signal.tenant_id is None]
        tenant_signals: dict[str, list[RuntimeSignal]] = defaultdict(list)
        for signal in due:
            if signal.tenant_id is not None:
                tenant_signals[signal.tenant_id].append(signal)

        delivered: list[RuntimeSignal] = []
        failed = False
        if system_signals:
            all_recipients = sorted({item for values in by_tenant.values() for item in values})
            if not all_recipients:
                logger.error("system alert has no active WeCom recipient")
                failed = True
            for recipient in all_recipients:
                try:
                    await send_message(settings, recipient, alert_content(system_signals))
                except Exception:
                    failed = True
                    logger.exception("system alert delivery failed")
            if all_recipients and not failed:
                delivered.extend(system_signals)

        for tenant_id, tenant_due in tenant_signals.items():
            tenant_failed = False
            targets = sorted(set(by_tenant.get(tenant_id, [])))
            if not targets:
                logger.error("tenant alert has no active WeCom recipient")
                failed = True
                continue
            for recipient in targets:
                try:
                    await send_message(settings, recipient, alert_content(tenant_due))
                except Exception:
                    tenant_failed = True
                    failed = True
                    logger.exception("tenant alert delivery failed")
            if not tenant_failed:
                delivered.extend(tenant_due)

        if delivered:
            mark_sent(state, delivered, now=now)
            save_alert_state(state_path, state)
        logger.warning(
            "runtime alerts evaluated: signals=%s delivered=%s",
            len(due),
            len(delivered),
        )
        return 1 if failed else 0
    finally:
        await pool.close()
        await close_http_client()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
