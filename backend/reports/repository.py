"""Reports repository: four-session report persistence and version audit queries."""

from __future__ import annotations

import json
from datetime import date

import asyncpg

REPORT_SESSIONS = ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET")


async def list_reports(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date | None = None,
    session: str | None = None,
) -> list[asyncpg.Record]:
    """One row per session — the latest version — for the report timeline."""
    clauses = ["tenant_id = $1"]
    params: list[object] = [tenant_id]
    if trade_date is not None:
        clauses.append(f"trade_date = ${len(params) + 1}")
        params.append(trade_date)
    if session is not None:
        clauses.append(f"report_session = ${len(params) + 1}")
        params.append(session)
    where = " AND ".join(clauses)
    return await conn.fetch(
        f"""
        SELECT DISTINCT ON (report_session)
            id, report_session, trade_date, version, status, as_of,
            quality_status, summary, model_run_id, job_run_id,
            policy_check_status, valid_until, supersedes_report_id, created_at
        FROM reporting.reports
        WHERE {where}
        ORDER BY report_session, version DESC
        """,
        *params,
    )


async def get_report(
    conn: asyncpg.Connection, tenant_id: str, report_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, report_session, trade_date, version, status, as_of,
               quality_status, summary, model_run_id, job_run_id,
               policy_check_status, valid_until, supersedes_report_id, created_at
        FROM reporting.reports
        WHERE tenant_id = $1 AND id = $2
        """,
        tenant_id,
        report_id,
    )


async def get_report_items(
    conn: asyncpg.Connection, tenant_id: str, report_id: str
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, item_type, subject_id, severity, title, body, action_plan,
               data_quality_status, sort_order
        FROM reporting.report_items
        WHERE tenant_id = $1 AND report_id = $2
        ORDER BY sort_order ASC, created_at ASC
        """,
        tenant_id,
        report_id,
    )


async def version_history(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date,
    session: str,
    exclude_report_id: str,
) -> list[asyncpg.Record]:
    """All versions of the same (trade_date, session) — the version-audit chain."""
    return await conn.fetch(
        """
        SELECT id, version, status, as_of, created_at, supersedes_report_id
        FROM reporting.reports
        WHERE tenant_id = $1 AND trade_date = $2 AND report_session = $3
          AND id <> $4
        ORDER BY version ASC
        """,
        tenant_id,
        trade_date,
        session,
        exclude_report_id,
    )


async def latest_report(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date,
    session: str,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, version, status, created_at
        FROM reporting.reports
        WHERE tenant_id = $1 AND trade_date = $2 AND report_session = $3
        ORDER BY version DESC
        LIMIT 1
        """,
        tenant_id,
        trade_date,
        session,
    )


async def insert_model_run(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    provider: str,
    model: str,
    prompt_template_version: str,
    input_snapshot_hashes: dict,
    token_usage: dict,
    cost_cny: object,
    started_at: object,
    finished_at: object,
    status: str,
    error_class: str | None,
    correlation_id: str | None,
) -> str:
    return str(
        await conn.fetchval(
            """
            INSERT INTO research.model_runs
                (tenant_id, provider, model, prompt_template_version,
                 input_snapshot_hashes, token_usage, cost_cny,
                 started_at, finished_at, status, error_class, correlation_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
            """,
            tenant_id,
            provider,
            model,
            prompt_template_version,
            json.dumps(input_snapshot_hashes, ensure_ascii=False),
            json.dumps(token_usage, ensure_ascii=False),
            cost_cny,
            started_at,
            finished_at,
            status,
            error_class,
            correlation_id,
        )
    )


async def insert_report(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    session: str,
    trade_date: object,
    version: int,
    status: str,
    as_of: object,
    quality_status: str,
    summary: str,
    model_run_id: str | None,
    job_run_id: str | None,
    policy_check_status: str,
    valid_until: object,
    supersedes_report_id: str | None,
) -> str:
    return str(
        await conn.fetchval(
            """
            INSERT INTO reporting.reports
                (tenant_id, report_session, trade_date, version, status, as_of,
                 quality_status, summary, model_run_id, job_run_id,
                 policy_check_status, valid_until, supersedes_report_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING id
            """,
            tenant_id,
            session,
            trade_date,
            version,
            status,
            as_of,
            quality_status,
            summary,
            model_run_id,
            job_run_id,
            policy_check_status,
            valid_until,
            supersedes_report_id,
        )
    )


async def insert_report_items(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    report_id: str,
    items: list[dict],
) -> None:
    for index, item in enumerate(items, start=1):
        await conn.execute(
            """
            INSERT INTO reporting.report_items
                (report_id, tenant_id, item_type, subject_id, severity,
                 title, body, action_plan, data_quality_status, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            report_id,
            tenant_id,
            item["item_type"],
            item.get("subject_id"),
            item.get("severity", "INFO"),
            item["title"],
            item["body"],
            json.dumps(item["action_plan"], ensure_ascii=False) if item.get("action_plan") else None,
            item.get("quality", "FRESH"),
            index * 10,
        )


async def insert_outbox(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    topic: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    idempotency_key: str,
) -> str:
    return str(
        await conn.fetchval(
            """
            INSERT INTO workflow.outbox
                (tenant_id, topic, aggregate_type, aggregate_id, payload,
                 schema_version, idempotency_key)
            VALUES ($1, $2, $3, $4, $5, 1, $6)
            ON CONFLICT (tenant_id, topic, idempotency_key) DO NOTHING
            RETURNING id
            """,
            tenant_id,
            topic,
            aggregate_type,
            aggregate_id,
            json.dumps(payload, ensure_ascii=False),
            idempotency_key,
        )
    )
