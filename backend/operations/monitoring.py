"""Independent runtime-health signal collection and alert de-duplication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.common.database import Pool, api_system_transaction, system_connection
from scripts.standard_report_jobs import is_complete_canonical_schedule


@dataclass(frozen=True)
class RuntimeSignal:
    kind: str
    object_id: str
    tenant_id: str | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.kind}:{self.tenant_id or 'system'}:{self.object_id}"


def load_alert_state(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_alert_state(path: Path, state: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(state), ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def due_signals(
    signals: Iterable[RuntimeSignal],
    state: Mapping[str, str],
    *,
    now: datetime,
    cooldown: timedelta,
) -> list[RuntimeSignal]:
    due: list[RuntimeSignal] = []
    for signal in signals:
        raw = state.get(signal.fingerprint)
        if raw:
            try:
                sent_at = datetime.fromisoformat(raw)
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)
                if now - sent_at.astimezone(timezone.utc) < cooldown:
                    continue
            except ValueError:
                pass
        due.append(signal)
    return due


def mark_sent(
    state: dict[str, str], signals: Iterable[RuntimeSignal], *, now: datetime
) -> None:
    timestamp = now.astimezone(timezone.utc).isoformat()
    for signal in signals:
        state[signal.fingerprint] = timestamp


def alert_content(signals: Iterable[RuntimeSignal]) -> str:
    kinds = [signal.kind for signal in signals]
    worker = kinds.count("WORKER_STOPPED")
    dead = kinds.count("OUTBOX_DEAD")
    unknown = kinds.count("OUTBOX_UNKNOWN")
    failed = kinds.count("JOB_FAILED")
    tenants_without_jobs = kinds.count("TENANT_NO_ENABLED_JOBS")
    tenants_with_incomplete_jobs = kinds.count("TENANT_INCOMPLETE_REPORT_JOBS")
    lines = ["WealthSail 运行告警"]
    if worker:
        lines.append("- Worker 服务已停止")
    if dead:
        lines.append(f"- 推送死信：{dead} 条")
    if unknown:
        lines.append(f"- 推送结果未知：{unknown} 条（需要人工核对）")
    if failed:
        lines.append(f"- 后台任务失败：{failed} 条")
    if tenants_without_jobs:
        lines.append(f"- 无启用报告任务：{tenants_without_jobs} 个租户")
    if tenants_with_incomplete_jobs:
        lines.append(f"- 四时段报告任务不完整：{tenants_with_incomplete_jobs} 个租户")
    lines.append("请登录运维页面或查看服务器日志。")
    return "\n".join(lines)


async def collect_database_signals(
    pool: Pool,
    *,
    failed_job_window_minutes: int,
    dead_outbox_window_hours: int,
) -> list[RuntimeSignal]:
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT 'OUTBOX_DEAD' AS kind, id::text AS object_id, tenant_id::text
              FROM workflow.outbox
             WHERE status = 'DEAD'
               AND created_at >= now() - make_interval(hours => $1)
            UNION ALL
            SELECT 'OUTBOX_UNKNOWN' AS kind, id::text AS object_id, tenant_id::text
              FROM workflow.outbox
             WHERE status = 'UNKNOWN'
               AND created_at >= now() - make_interval(hours => $1)
            UNION ALL
            SELECT 'JOB_FAILED' AS kind, id::text AS object_id, tenant_id::text
              FROM workflow.job_runs
             WHERE status = 'FAILED'
               AND COALESCE(finished_at, started_at) >=
                   now() - make_interval(mins => $2)
            """,
            dead_outbox_window_hours,
            failed_job_window_minutes,
        )
    signals = [
        RuntimeSignal(
            kind=str(row["kind"]),
            object_id=str(row["object_id"]),
            tenant_id=str(row["tenant_id"]),
        )
        for row in rows
    ]

    # The Worker role intentionally cannot read identity.tenants directly.
    # Downgrade through the sanctioned API role for the cross-tenant ACTIVE
    # tenant inventory, then return to the Worker role for workflow metadata.
    # Keeping the two reads in separate role-scoped acquisitions preserves the
    # least-privilege boundary without adding a broader database grant.
    async with api_system_transaction(pool) as conn:
        active_tenant_rows = await conn.fetch(
            """
            SELECT id::text AS tenant_id
              FROM identity.tenants
             WHERE status = 'ACTIVE'
            """
        )
    async with system_connection(pool) as conn:
        report_job_rows = await conn.fetch(
            """
            SELECT tenant_id::text AS tenant_id, job_name, cron_expr,
                   report_session, is_enabled
              FROM workflow.jobs
             WHERE report_session IN (
                 'PRE_MARKET', 'INTRADAY', 'FUND_1430', 'POST_MARKET'
             )
            """
        )
        active_binding_rows = await conn.fetch(
            """
            SELECT DISTINCT tenant_id::text AS tenant_id
              FROM identity.channel_bindings
             WHERE status = 'ACTIVE'
               AND channel_type = 'wecom'
               AND external_user_ciphertext IS NOT NULL
            """
        )

    active_tenant_ids = {str(row["tenant_id"]) for row in active_tenant_rows}
    bound_tenant_ids = {str(row["tenant_id"]) for row in active_binding_rows}
    monitored_tenant_ids = active_tenant_ids & bound_tenant_ids
    jobs_by_tenant: dict[str, list[Any]] = {}
    for row in report_job_rows:
        jobs_by_tenant.setdefault(str(row["tenant_id"]), []).append(row)

    for tenant_id in sorted(monitored_tenant_ids):
        tenant_jobs = jobs_by_tenant.get(tenant_id, [])
        if is_complete_canonical_schedule(tenant_jobs):
            continue

        # The object ID is deliberately constant. The fingerprint already
        # includes tenant_id, while user-facing content never exposes either
        # value; this gives stable cooldown de-duplication per affected tenant.
        if not any(row["is_enabled"] is True for row in tenant_jobs):
            signals.append(
                RuntimeSignal(
                    "TENANT_NO_ENABLED_JOBS",
                    "enabled-report-jobs",
                    tenant_id,
                )
            )
        else:
            signals.append(
                RuntimeSignal(
                    "TENANT_INCOMPLETE_REPORT_JOBS",
                    "standard-report-jobs",
                    tenant_id,
                )
            )
    return signals


async def load_active_recipients(pool: Pool) -> list[dict[str, Any]]:
    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT tenant_id::text AS tenant_id, external_user_ciphertext
              FROM identity.channel_bindings
             WHERE status = 'ACTIVE'
               AND channel_type = 'wecom'
               AND external_user_ciphertext IS NOT NULL
            """
        )
    return [dict(row) for row in rows]
