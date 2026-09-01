"""Reports repository: four-session report persistence and version audit queries."""

from __future__ import annotations

import json
from urllib.parse import quote
from datetime import date

import asyncpg

REPORT_SESSIONS = ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET")


async def get_pre_market_corporate_actions(
    conn: asyncpg.Connection,
    instrument_ids: list[str],
    trade_date: date,
) -> list[dict]:
    """Read the latest revision of today's held-instrument corporate actions."""
    if not instrument_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (ca.instrument_id, ca.action_type, ca.ex_date)
               ca.instrument_id, COALESCE(alias.ticker, '') AS symbol,
               instrument.canonical_name AS name, ca.action_type, ca.ex_date,
               ca.record_date, ca.pay_date, ca.params, ca.revision
          FROM market.corporate_actions ca
          JOIN reference.instruments instrument ON instrument.id = ca.instrument_id
          LEFT JOIN LATERAL (
              SELECT ia.alias_value AS ticker
                FROM reference.instrument_aliases ia
               WHERE ia.instrument_id = ca.instrument_id
                 AND ia.alias_type = 'TICKER'
                 AND ia.effective_from <= $2
                 AND (ia.effective_to IS NULL OR ia.effective_to >= $2)
               ORDER BY ia.effective_from DESC
               LIMIT 1
          ) alias ON TRUE
         WHERE ca.instrument_id = ANY($1::uuid[])
           AND ca.ex_date = $2
         ORDER BY ca.instrument_id, ca.action_type, ca.ex_date, ca.revision DESC
        """,
        instrument_ids,
        trade_date,
    )
    result: list[dict] = []
    for row in rows:
        params = row["params"]
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                params = {}
        result.append(
            {
                "instrumentId": str(row["instrument_id"]),
                "symbol": str(row["symbol"] or ""),
                "name": str(row["name"]),
                "actionType": str(row["action_type"]),
                "exDate": row["ex_date"].isoformat(),
                "recordDate": row["record_date"].isoformat()
                if row["record_date"]
                else None,
                "payDate": row["pay_date"].isoformat() if row["pay_date"] else None,
                "params": params if isinstance(params, dict) else {},
                "revision": int(row["revision"]),
            }
        )
    return result


async def get_pre_market_cutoff_rules(
    conn: asyncpg.Connection,
    instrument_ids: list[str],
    trade_date: date,
) -> list[dict]:
    """Read effective held-fund deadlines without asserting channel availability."""
    if not instrument_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (rule.instrument_id, rule.sales_channel)
               rule.instrument_id, COALESCE(alias.ticker, '') AS symbol,
               instrument.canonical_name AS name, rule.sales_channel,
               rule.cutoff_time, rule.timezone, rule.rule_version
          FROM reference.instrument_cutoff_rules rule
          JOIN reference.instruments instrument ON instrument.id = rule.instrument_id
          LEFT JOIN LATERAL (
              SELECT ia.alias_value AS ticker
                FROM reference.instrument_aliases ia
               WHERE ia.instrument_id = rule.instrument_id
                 AND ia.alias_type = 'TICKER'
                 AND ia.effective_from <= $2
                 AND (ia.effective_to IS NULL OR ia.effective_to >= $2)
               ORDER BY ia.effective_from DESC
               LIMIT 1
          ) alias ON TRUE
         WHERE rule.instrument_id = ANY($1::uuid[])
           AND rule.effective_from <= $2
           AND (rule.effective_to IS NULL OR rule.effective_to >= $2)
           AND rule.cutoff_time <= TIME '15:00'
         ORDER BY rule.instrument_id, rule.sales_channel,
                  rule.effective_from DESC, rule.rule_version DESC
        """,
        instrument_ids,
        trade_date,
    )
    return [
        {
            "instrumentId": str(row["instrument_id"]),
            "symbol": str(row["symbol"] or ""),
            "name": str(row["name"]),
            "salesChannel": str(row["sales_channel"]),
            "cutoffTime": row["cutoff_time"].isoformat(),
            "timezone": str(row["timezone"]),
            "ruleVersion": int(row["rule_version"]),
        }
        for row in rows
    ]


async def get_shared_market_brief(
    conn: asyncpg.Connection, trade_date: date
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, trade_date, draft, source_context, context_hash, provider,
               model, generation_status, token_usage, cost_cny,
               quality_status, generated_at
          FROM reporting.shared_market_briefs
         WHERE trade_date = $1
        """,
        trade_date,
    )


async def insert_shared_market_brief(
    conn: asyncpg.Connection,
    *,
    trade_date: date,
    draft: dict,
    source_context: dict,
    context_hash: str,
    provider: str,
    model: str,
    generation_status: str,
    token_usage: dict,
    cost_cny: object,
    quality_status: str,
    generated_at: object,
) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        INSERT INTO reporting.shared_market_briefs
            (trade_date, draft, source_context, context_hash, provider, model,
             generation_status, token_usage, cost_cny, quality_status,
             generated_at)
        VALUES ($1, $2::jsonb, $3::jsonb, $4, $5, $6, $7, $8::jsonb,
                $9, $10, $11)
        ON CONFLICT (trade_date) DO NOTHING
        RETURNING id, trade_date, draft, source_context, context_hash, provider,
                  model, generation_status, token_usage, cost_cny,
                  quality_status, generated_at
        """,
        trade_date,
        json.dumps(draft, ensure_ascii=False, default=str),
        json.dumps(source_context, ensure_ascii=False, default=str),
        context_hash,
        provider,
        model,
        generation_status,
        json.dumps(token_usage, ensure_ascii=False),
        cost_cny,
        quality_status,
        generated_at,
    )
    if row is not None:
        return row
    existing = await get_shared_market_brief(conn, trade_date)
    if existing is None:
        raise RuntimeError("shared market brief insert lost without a winner")
    return existing


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
            json.dumps(item["action_plan"], ensure_ascii=False)
            if item.get("action_plan")
            else None,
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
    content: str | None = None,
    target_user_id: str | None = None,
) -> int:
    """Expand one tenant report into one delivery per ACTIVE member binding.

    Each binding gets its own idempotency key and retry state, so one unavailable
    recipient cannot block or duplicate another recipient's message. Migration
    020 guarantees at most one ACTIVE binding per tenant/user/channel.
    """
    bindings = await conn.fetch(
        """
        SELECT binding.id, binding.user_id
          FROM identity.channel_bindings binding
          LEFT JOIN identity.user_preferences preference
            ON preference.user_id = binding.user_id
         WHERE binding.tenant_id = $1
           AND binding.channel_type = 'wecom'
           AND binding.status = 'ACTIVE'
           AND ($3::uuid IS NULL OR binding.user_id = $3::uuid)
           AND CASE $2
               WHEN 'PRE_MARKET' THEN
                   COALESCE(preference.pre_market_report_enabled, TRUE)
               WHEN 'INTRADAY' THEN
                   COALESCE(preference.intraday_report_enabled, TRUE)
               WHEN 'FUND_1430' THEN
                   COALESCE(preference.fund_1430_report_enabled, TRUE)
               WHEN 'POST_MARKET' THEN
                   COALESCE(preference.post_market_report_enabled, TRUE)
               ELSE FALSE
           END
         ORDER BY binding.user_id, binding.id
        """,
        tenant_id,
        session,
        target_user_id,
    )
    payload = {
        "report_id": report_id,
        "session": session,
        "version": version,
        "message_type": "markdown",
        "link_path": f"/reports?reportId={quote(report_id, safe='')}",
    }
    if content:
        payload["content"] = content
    rows = [
        (
            tenant_id,
            str(binding["id"]),
            report_id,
            "REPORT_PUBLISHED",
            "reporting.reports",
            json.dumps(payload, ensure_ascii=False),
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


async def get_report_outbox_id(
    conn: asyncpg.Connection,
    tenant_id: str,
    report_id: str,
    user_id: str,
) -> str | None:
    """Return the queued delivery for one report recipient, if eligible."""
    value = await conn.fetchval(
        """
        SELECT outbox.id
          FROM workflow.outbox outbox
          JOIN identity.channel_bindings binding
            ON binding.id = outbox.channel_binding_id
           AND binding.tenant_id = outbox.tenant_id
         WHERE outbox.tenant_id = $1
           AND outbox.aggregate_id = $2
           AND outbox.topic = 'REPORT_PUBLISHED'
           AND binding.user_id = $3
         ORDER BY outbox.created_at DESC, outbox.id DESC
         LIMIT 1
        """,
        tenant_id,
        report_id,
        user_id,
    )
    return str(value) if value is not None else None
