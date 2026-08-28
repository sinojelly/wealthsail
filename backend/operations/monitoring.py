"""Independent runtime-health signal collection and alert de-duplication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.common.database import Pool, system_connection


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
    failed = kinds.count("JOB_FAILED")
    lines = ["WealthSail 运行告警"]
    if worker:
        lines.append("- Worker 服务已停止")
    if dead:
        lines.append(f"- 推送死信：{dead} 条")
    if failed:
        lines.append(f"- 后台任务失败：{failed} 条")
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
            SELECT 'JOB_FAILED' AS kind, id::text AS object_id, tenant_id::text
              FROM workflow.job_runs
             WHERE status = 'FAILED'
               AND COALESCE(finished_at, started_at) >=
                   now() - make_interval(mins => $2)
            """,
            dead_outbox_window_hours,
            failed_job_window_minutes,
        )
    return [
        RuntimeSignal(
            kind=str(row["kind"]),
            object_id=str(row["object_id"]),
            tenant_id=str(row["tenant_id"]),
        )
        for row in rows
    ]


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
