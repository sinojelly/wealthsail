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
        SELECT ri.id, ri.item_type, ri.subject_id, ri.severity, ri.title,
               ri.body, ri.action_plan, ri.data_quality_status, ri.sort_order,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object(
                           'id', er.id,
                           'claimType', rie.claim_type,
                           'sourceSnapshotId', er.source_snapshot_id,
                           'sourceName', ds.name,
                           'originUri', ss.origin_uri,
                           'asOf', er.as_of,
                           'excerpt', er.excerpt,
                           'quality', er.quality_status,
                           'contentHash', ss.content_hash
                       ) ORDER BY er.created_at
                   ) FILTER (WHERE er.id IS NOT NULL),
                   '[]'::jsonb
               ) AS evidence
        FROM reporting.report_items ri
        LEFT JOIN reporting.report_item_evidence rie
          ON rie.tenant_id=ri.tenant_id AND rie.report_item_id=ri.id
        LEFT JOIN research.evidence_refs er
          ON er.tenant_id=rie.tenant_id AND er.id=rie.evidence_ref_id
        LEFT JOIN market.source_snapshots ss ON ss.id=er.source_snapshot_id
        LEFT JOIN reference.data_sources ds ON ds.id=ss.source_id
        WHERE ri.tenant_id = $1 AND ri.report_id = $2
        GROUP BY ri.id
        ORDER BY ri.sort_order ASC, ri.created_at ASC
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
        report_item_id = await conn.fetchval(
            """
            INSERT INTO reporting.report_items
                (report_id, tenant_id, item_type, subject_id, severity,
                 title, body, action_plan, data_quality_status, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
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
        evidence = item.get("_evidence")
        if not evidence:
            continue
        evidence_ref_id = await conn.fetchval(
            """
            INSERT INTO research.evidence_refs
                (tenant_id, source_snapshot_id, claim_type, as_of, excerpt,
                 quality_status, relevance_score)
            VALUES ($1, $2, $3, $4, $5, $6, 1.0000)
            RETURNING id
            """,
            tenant_id,
            evidence["source_snapshot_id"],
            evidence["claim_type"],
            evidence["as_of"],
            evidence["excerpt"],
            evidence.get("quality", "UNAVAILABLE"),
        )
        await conn.execute(
            """
            INSERT INTO reporting.report_item_evidence
                (tenant_id, report_item_id, evidence_ref_id, claim_type)
            VALUES ($1, $2, $3, $4)
            """,
            tenant_id,
            report_item_id,
            evidence_ref_id,
            evidence.get("link_claim_type", "FACT"),
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


async def insert_report_outbox_deliveries(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    report_id: str,
    session: str,
    version: int,
    trade_date: object,
) -> int:
    """Expand one tenant report into one independent delivery per active binding.

    Delivery status and idempotency remain binding-scoped, so one unavailable
    recipient cannot block or duplicate another recipient's message.
    """
    bindings = await conn.fetch(
        """
        SELECT id, user_id
        FROM identity.channel_bindings
        WHERE tenant_id = $1
          AND channel_type = 'wecom' AND status = 'ACTIVE'
        ORDER BY user_id, id
        """,
        tenant_id,
    )
    rows = [
        (
            tenant_id,
            str(binding["id"]),
            report_id,
            "REPORT_PUBLISHED",
            "reporting.reports",
            json.dumps(
                {"report_id": report_id, "session": session, "version": version},
                ensure_ascii=False,
            ),
            f"{session}:{trade_date}:{version}:binding:{binding['id']}",
        )
        for binding in bindings
    ]
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO workflow.outbox
            (tenant_id, channel_binding_id, aggregate_id, topic,
             aggregate_type, payload, schema_version, idempotency_key)
        VALUES ($1, $2, $3, $4, $5, $6, 2, $7)
        ON CONFLICT (tenant_id, topic, idempotency_key) DO NOTHING
        """,
        rows,
    )
    return len(rows)
