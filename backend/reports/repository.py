"""Reports repository: four-session report persistence and version audit queries."""

from __future__ import annotations

import json
from urllib.parse import quote
from datetime import date, datetime, timedelta
from decimal import Decimal

import asyncpg

REPORT_SESSIONS = ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET")


async def get_user_watchlist_context(
    conn: asyncpg.Connection, user_id: str
) -> list[dict]:
    """Read one user's watchlist with its latest persisted quote, if present."""
    rows = await conn.fetch(
        """
        SELECT watchlist.symbol, watchlist.asset_name AS name,
               watchlist.asset_type AS type, watchlist.added_at,
               alias.instrument_id, quote.value AS price,
               quote.metadata ->> 'changePct' AS change_pct,
               quote.quality_status AS quality, quote.as_of
          FROM identity.watchlist watchlist
          LEFT JOIN LATERAL (
              SELECT instrument_aliases.instrument_id
                FROM reference.instrument_aliases instrument_aliases
               WHERE instrument_aliases.alias_type = 'TICKER'
                 AND upper(instrument_aliases.alias_value) = watchlist.symbol
                 AND instrument_aliases.effective_from <= CURRENT_DATE
                 AND (
                     instrument_aliases.effective_to IS NULL
                     OR instrument_aliases.effective_to >= CURRENT_DATE
                 )
               ORDER BY instrument_aliases.effective_from DESC
               LIMIT 1
          ) alias ON TRUE
          LEFT JOIN LATERAL (
              SELECT observations.value, observations.metadata,
                     observations.quality_status, observations.as_of
                FROM market.market_observations observations
               WHERE observations.subject_id = alias.instrument_id
                 AND observations.field_code IN ('CLOSE', 'NAV', 'ESTIMATED_NAV')
               ORDER BY observations.as_of DESC, observations.observed_at DESC
               LIMIT 1
          ) quote ON TRUE
         WHERE watchlist.user_id = $1
         ORDER BY watchlist.added_at DESC, watchlist.symbol
        """,
        user_id,
    )
    return [
        {
            "symbol": row["symbol"],
            "name": row["name"],
            "type": row["type"],
            "addedAt": row["added_at"],
            "instrumentId": str(row["instrument_id"])
            if row["instrument_id"] is not None
            else None,
            "price": row["price"],
            "changePct": row["change_pct"],
            "quality": row["quality"],
            "asOf": row["as_of"],
        }
        for row in rows
    ]


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


async def get_pre_market_fund_profiles(
    conn: asyncpg.Connection,
    instrument_ids: list[str],
    trade_date: date,
) -> list[dict]:
    """Read effective OTC-fund subscription/redemption openness.

    Returns the latest effective ``reference.fund_profiles`` row per instrument
    (``purchase_status`` / ``redemption_status`` in ``OPEN``/``SUSPENDED``).
    An absent row means "no verified status" — the caller must render that as
    "no cutoff reminder" and never guess openness.
    """
    if not instrument_ids:
        return []
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (profile.instrument_id)
               profile.instrument_id, COALESCE(alias.ticker, '') AS symbol,
               instrument.canonical_name AS name,
               profile.purchase_status, profile.redemption_status,
               profile.effective_from, profile.effective_to
          FROM reference.fund_profiles profile
          JOIN reference.instruments instrument ON instrument.id = profile.instrument_id
          LEFT JOIN LATERAL (
              SELECT ia.alias_value AS ticker
                FROM reference.instrument_aliases ia
               WHERE ia.instrument_id = profile.instrument_id
                 AND ia.alias_type = 'TICKER'
                 AND ia.effective_from <= $2
                 AND (ia.effective_to IS NULL OR ia.effective_to >= $2)
               ORDER BY ia.effective_from DESC
               LIMIT 1
          ) alias ON TRUE
         WHERE profile.instrument_id = ANY($1::uuid[])
           AND profile.effective_from <= $2
           AND (profile.effective_to IS NULL OR profile.effective_to >= $2)
         ORDER BY profile.instrument_id, profile.effective_from DESC
        """,
        instrument_ids,
        trade_date,
    )
    return [
        {
            "instrumentId": str(row["instrument_id"]),
            "symbol": str(row["symbol"] or ""),
            "name": str(row["name"]),
            "purchaseStatus": str(row["purchase_status"]),
            "redemptionStatus": str(row["redemption_status"]),
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


async def get_shared_pre_market_brief(
    conn: asyncpg.Connection, trade_date: date
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, trade_date, draft, source_context, context_hash,
               quality_status, generated_at
          FROM reporting.shared_pre_market_briefs
         WHERE trade_date = $1
        """,
        trade_date,
    )


async def insert_shared_pre_market_brief(
    conn: asyncpg.Connection,
    *,
    trade_date: date,
    draft: dict,
    source_context: dict,
    context_hash: str,
    quality_status: str,
    generated_at: object,
) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        INSERT INTO reporting.shared_pre_market_briefs
            (trade_date, draft, source_context, context_hash, quality_status,
             generated_at)
        VALUES ($1, $2::jsonb, $3::jsonb, $4, $5, $6)
        ON CONFLICT (trade_date) DO NOTHING
        RETURNING id, trade_date, draft, source_context, context_hash,
                  quality_status, generated_at
        """,
        trade_date,
        json.dumps(draft, ensure_ascii=False, default=str),
        json.dumps(source_context, ensure_ascii=False, default=str),
        context_hash,
        quality_status,
        generated_at,
    )
    if row is not None:
        return row
    existing = await get_shared_pre_market_brief(conn, trade_date)
    if existing is None:
        raise RuntimeError("shared pre-market brief insert lost without a winner")
    return existing


async def list_reports(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date | None = None,
    session: str | None = None,
    actor_user_id: str | None = None,
) -> list[asyncpg.Record]:
    """Latest visible version of every session/variant/audience identity."""
    clauses = ["tenant_id = $1"]
    params: list[object] = [tenant_id]
    if trade_date is not None:
        clauses.append(f"trade_date = ${len(params) + 1}")
        params.append(trade_date)
    if session is not None:
        clauses.append(f"report_session = ${len(params) + 1}")
        params.append(session)
    if actor_user_id is not None:
        clauses.append(
            f"(audience_user_id IS NULL OR audience_user_id = ${len(params) + 1})"
        )
        params.append(actor_user_id)
    where = " AND ".join(clauses)
    return await conn.fetch(
        f"""
        SELECT DISTINCT ON (report_session, report_variant, audience_user_id)
            id, report_session, trade_date, version, status, as_of,
            quality_status, summary, model_run_id, job_run_id,
            policy_check_status, valid_until, supersedes_report_id, created_at,
            report_variant, audience_user_id
        FROM reporting.reports
        WHERE {where}
        ORDER BY report_session, report_variant, audience_user_id, version DESC
        """,
        *params,
    )


async def get_report(
    conn: asyncpg.Connection,
    tenant_id: str,
    report_id: str,
    actor_user_id: str | None = None,
) -> asyncpg.Record | None:
    visibility = ""
    params: list[object] = [tenant_id, report_id]
    if actor_user_id is not None:
        visibility = " AND (audience_user_id IS NULL OR audience_user_id = $3)"
        params.append(actor_user_id)
    return await conn.fetchrow(
        f"""
        SELECT id, report_session, trade_date, version, status, as_of,
               quality_status, summary, model_run_id, job_run_id,
               policy_check_status, valid_until, supersedes_report_id, created_at,
               report_variant, audience_user_id
        FROM reporting.reports
        WHERE tenant_id = $1 AND id = $2{visibility}
        """,
        *params,
    )


async def get_report_items(
    conn: asyncpg.Connection,
    tenant_id: str,
    report_id: str,
    actor_user_id: str | None = None,
) -> list[asyncpg.Record]:
    visibility = ""
    params: list[object] = [tenant_id, report_id]
    if actor_user_id is not None:
        visibility = """
          AND EXISTS (
              SELECT 1
                FROM reporting.reports parent_report
               WHERE parent_report.tenant_id = ri.tenant_id
                 AND parent_report.id = ri.report_id
                 AND (
                     parent_report.audience_user_id IS NULL
                     OR parent_report.audience_user_id = $3
                 )
          )
        """
        params.append(actor_user_id)
    return await conn.fetch(
        f"""
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
        WHERE ri.tenant_id = $1 AND ri.report_id = $2{visibility}
        GROUP BY ri.id
        ORDER BY ri.sort_order ASC, ri.created_at ASC
        """,
        *params,
    )


async def version_history(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date,
    session: str,
    exclude_report_id: str,
    *,
    actor_user_id: str | None = None,
    report_variant: str = "STANDARD",
    audience_user_id: str | None = None,
) -> list[asyncpg.Record]:
    """Other visible versions of one exact report identity."""
    visibility = ""
    params: list[object] = [
        tenant_id,
        trade_date,
        session,
        exclude_report_id,
        report_variant,
        audience_user_id,
    ]
    if actor_user_id is not None:
        visibility = " AND (audience_user_id IS NULL OR audience_user_id = $7)"
        params.append(actor_user_id)
    return await conn.fetch(
        f"""
        SELECT id, version, status, as_of, created_at, supersedes_report_id
        FROM reporting.reports
        WHERE tenant_id = $1 AND trade_date = $2 AND report_session = $3
          AND id <> $4
          AND report_variant = $5
          AND audience_user_id IS NOT DISTINCT FROM $6
          {visibility}
        ORDER BY version ASC
        """,
        *params,
    )


async def latest_report(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date,
    session: str,
    *,
    actor_user_id: str | None = None,
    report_variant: str = "STANDARD",
    audience_user_id: str | None = None,
) -> asyncpg.Record | None:
    visibility = ""
    params: list[object] = [
        tenant_id,
        trade_date,
        session,
        report_variant,
        audience_user_id,
    ]
    if actor_user_id is not None:
        visibility = " AND (audience_user_id IS NULL OR audience_user_id = $6)"
        params.append(actor_user_id)
    return await conn.fetchrow(
        f"""
        SELECT id, version, status, created_at, report_variant, audience_user_id
        FROM reporting.reports
        WHERE tenant_id = $1 AND trade_date = $2 AND report_session = $3
          AND report_variant = $4
          AND audience_user_id IS NOT DISTINCT FROM $5
          {visibility}
        ORDER BY version DESC
        LIMIT 1
        """,
        *params,
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
    report_variant: str = "STANDARD",
    audience_user_id: str | None = None,
) -> str:
    return str(
        await conn.fetchval(
            """
            INSERT INTO reporting.reports
                (tenant_id, report_session, trade_date, version, status, as_of,
                 quality_status, summary, model_run_id, job_run_id,
                 policy_check_status, valid_until, supersedes_report_id,
                 report_variant, audience_user_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15)
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
            report_variant,
            audience_user_id,
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
          JOIN identity.tenant_members membership
            ON membership.tenant_id = binding.tenant_id
           AND membership.user_id = binding.user_id
           AND membership.status = 'ACTIVE'
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
            # The report id MUST be part of the key: the split pre-market phases
            # (08:30 macro / 08:35 personalized) persist two report rows with
            # the same (session, trade_date) and each starts its version at 1,
            # so a session-level key collides and ON CONFLICT DO NOTHING
            # silently drops the second push.
            f"{session}:{trade_date}:{version}:{report_id}:binding:{binding['id']}",
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


async def get_post_market_performance(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date,
    *,
    owner_user_id: str | None = None,
) -> dict:
    """Return complete, cash-flow-aware daily performance or abstain.

    Every active account in the requested asset scope must have both today's
    computed PnL and a previous close.  A same-day external cash flow keeps the
    amount valid (it was removed by the projection) but makes the simple daily
    rate unavailable because the intraday timing of that flow is unknown.
    """
    row = await conn.fetchrow(
        """
        SELECT
            CASE WHEN COUNT(*) > 0
                       AND COUNT(current_nav.id) = COUNT(*)
                       AND COUNT(current_nav.daily_pnl) = COUNT(*)
                 THEN SUM(current_nav.daily_pnl)
            END AS daily_pnl,
            CASE WHEN COUNT(*) > 0
                       AND COUNT(current_nav.id) = COUNT(*)
                       AND COUNT(current_nav.daily_pnl) = COUNT(*)
                       AND COUNT(previous_nav.total_nav) = COUNT(*)
                       AND BOOL_AND(COALESCE(daily_flow.daily_external_flow, 0) = 0)
                       AND SUM(previous_nav.total_nav) <> 0
                 THEN SUM(current_nav.daily_pnl)
                      / SUM(previous_nav.total_nav) * 100
            END AS daily_pnl_rate,
            CASE WHEN COUNT(*) > 0
                       AND COUNT(current_nav.id) = COUNT(*)
                       AND COUNT(current_nav.daily_pnl) = COUNT(*)
                       AND COUNT(previous_nav.total_nav) = COUNT(*)
                 THEN CASE
                     WHEN BOOL_OR(current_nav.data_quality_status = 'UNAVAILABLE')
                              OR BOOL_OR(previous_nav.data_quality_status = 'UNAVAILABLE')
                         THEN 'UNAVAILABLE'
                     WHEN BOOL_OR(current_nav.data_quality_status = 'STALE')
                              OR BOOL_OR(previous_nav.data_quality_status = 'STALE')
                         THEN 'STALE'
                     WHEN BOOL_OR(current_nav.data_quality_status = 'ESTIMATED')
                              OR BOOL_OR(previous_nav.data_quality_status = 'ESTIMATED')
                         THEN 'ESTIMATED'
                     ELSE 'FRESH'
                 END
                 ELSE 'UNAVAILABLE'
            END AS quality,
            COUNT(*) AS account_count
          FROM identity.accounts account
          LEFT JOIN ledger.account_nav_snapshots current_nav
            ON current_nav.tenant_id = account.tenant_id
           AND current_nav.account_id = account.id
           AND current_nav.as_of = $3
          LEFT JOIN LATERAL (
              SELECT prior.total_nav, prior.data_quality_status
                FROM ledger.account_nav_snapshots prior
               WHERE prior.tenant_id = account.tenant_id
                 AND prior.account_id = account.id
                 AND prior.as_of = (
                     SELECT max(calendar.calendar_date)
                       FROM reference.trading_calendars calendar
                      WHERE calendar.market = 'SSE'
                        AND calendar.is_trading_day = TRUE
                        AND calendar.calendar_date < $3
                 )
          ) previous_nav ON TRUE
          LEFT JOIN LATERAL (
              SELECT COALESCE(SUM(leg.amount), 0) AS daily_external_flow
                FROM ledger.ledger_events event
                JOIN ledger.ledger_legs leg
                  ON leg.tenant_id = event.tenant_id
                 AND leg.event_id = event.id
               WHERE event.tenant_id = account.tenant_id
                 AND event.account_id = account.id
                 AND event.trade_date = $3
                 AND event.event_type IN ('CASH_IN', 'CASH_OUT')
                 AND event.status = 'CONFIRMED'
                 AND leg.amount IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1
                       FROM ledger.ledger_events reversal
                      WHERE reversal.tenant_id = event.tenant_id
                        AND reversal.event_type = 'REVERSAL'
                        AND reversal.status = 'CONFIRMED'
                        AND reversal.reverses_event_id = event.id
                 )
          ) daily_flow ON TRUE
         WHERE account.tenant_id = $1
           AND account.status = 'ACTIVE'
           AND (
               $2::uuid IS NULL OR EXISTS (
                   SELECT 1
                     FROM identity.account_members owner
                    WHERE owner.tenant_id = account.tenant_id
                      AND owner.account_id = account.id
                      AND owner.user_id = $2
                      AND owner.effective_from <= $3
                      AND (owner.effective_to IS NULL OR owner.effective_to >= $3)
                      AND owner.relationship IN ('BENEFICIAL_OWNER', 'JOINT_OWNER')
               )
           )
        """,
        tenant_id,
        owner_user_id,
        trade_date,
    )
    row = row or {}
    daily_pnl = row.get("daily_pnl")
    daily_rate = row.get("daily_pnl_rate")
    return {
        "dailyPnl": str(daily_pnl) if daily_pnl is not None else None,
        "dailyPnlRate": str(daily_rate) if daily_rate is not None else None,
        "quality": str(row.get("quality") or "UNAVAILABLE"),
        "asOf": trade_date.isoformat(),
    }


async def get_post_market_contributions(
    conn: asyncpg.Connection,
    tenant_id: str,
    trade_date: date,
    *,
    owner_user_id: str | None = None,
) -> dict:
    """Estimate same-day holding contributions from fresh persisted quotes.

    Positions changed during the day are excluded because a closing quantity
    cannot reveal the intraday exposure.  This deliberate abstention prevents
    buys, sells and transfers from being presented as exact attribution.
    """
    rows = await conn.fetch(
        """
        WITH positions AS (
            SELECT snapshot.instrument_id,
                   CASE WHEN COUNT(snapshot.market_value) = COUNT(*)
                        THEN SUM(snapshot.market_value)
                   END AS market_value,
                   CASE WHEN COUNT(snapshot.market_price) = COUNT(*)
                             AND MIN(snapshot.market_price) = MAX(snapshot.market_price)
                        THEN MIN(snapshot.market_price)
                   END AS market_price
              FROM ledger.position_snapshots snapshot
              JOIN identity.accounts account
                ON account.tenant_id = snapshot.tenant_id
               AND account.id = snapshot.account_id
             WHERE snapshot.tenant_id = $1
               AND snapshot.as_of = $3
               AND snapshot.quantity > 0
               AND account.status = 'ACTIVE'
               AND (
                   $2::uuid IS NULL OR EXISTS (
                       SELECT 1
                         FROM identity.account_members owner
                        WHERE owner.tenant_id = account.tenant_id
                          AND owner.account_id = account.id
                          AND owner.user_id = $2
                          AND owner.effective_from <= $3
                          AND (owner.effective_to IS NULL OR owner.effective_to >= $3)
                          AND owner.relationship IN (
                              'BENEFICIAL_OWNER', 'JOINT_OWNER'
                          )
                   )
               )
             GROUP BY snapshot.instrument_id
        ), scoped_quantity_events AS (
            SELECT event.id, event.account_id
              FROM ledger.ledger_events event
              JOIN identity.accounts changed_account
                ON changed_account.tenant_id = event.tenant_id
               AND changed_account.id = event.account_id
             WHERE event.tenant_id = $1
               AND changed_account.status = 'ACTIVE'
               AND event.trade_date = $3
               AND event.status = 'CONFIRMED'
               AND event.event_type IN (
                   'BUY', 'SELL', 'DIVIDEND_REINVEST', 'SPLIT', 'MERGER',
                   'TRANSFER_IN', 'TRANSFER_OUT', 'ADJUSTMENT'
               )
               AND (
                   $2::uuid IS NULL OR EXISTS (
                       SELECT 1
                         FROM identity.account_members changed_owner
                        WHERE changed_owner.tenant_id = changed_account.tenant_id
                          AND changed_owner.account_id = changed_account.id
                          AND changed_owner.user_id = $2
                          AND changed_owner.effective_from <= $3
                          AND (
                              changed_owner.effective_to IS NULL
                              OR changed_owner.effective_to >= $3
                          )
                          AND changed_owner.relationship IN (
                              'BENEFICIAL_OWNER', 'JOINT_OWNER'
                          )
                   )
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM ledger.ledger_events reversal
                    WHERE reversal.tenant_id = event.tenant_id
                      AND reversal.event_type = 'REVERSAL'
                      AND reversal.status = 'CONFIRMED'
                      AND reversal.reverses_event_id = event.id
               )
            UNION
            SELECT original.id, original.account_id
              FROM ledger.ledger_events reversal
              JOIN ledger.ledger_events original
                ON original.tenant_id = reversal.tenant_id
               AND reversal.reverses_event_id = original.id
              JOIN identity.accounts changed_account
                ON changed_account.tenant_id = original.tenant_id
               AND changed_account.id = original.account_id
             WHERE reversal.tenant_id = $1
               AND reversal.trade_date = $3
               AND reversal.status = 'CONFIRMED'
               AND reversal.event_type = 'REVERSAL'
               AND original.event_type IN (
                   'BUY', 'SELL', 'DIVIDEND_REINVEST', 'SPLIT', 'MERGER',
                   'TRANSFER_IN', 'TRANSFER_OUT', 'ADJUSTMENT'
               )
               AND changed_account.status = 'ACTIVE'
               AND (
                   $2::uuid IS NULL OR EXISTS (
                       SELECT 1
                         FROM identity.account_members changed_owner
                        WHERE changed_owner.tenant_id = changed_account.tenant_id
                          AND changed_owner.account_id = changed_account.id
                          AND changed_owner.user_id = $2
                          AND changed_owner.effective_from <= $3
                          AND (
                              changed_owner.effective_to IS NULL
                              OR changed_owner.effective_to >= $3
                          )
                          AND changed_owner.relationship IN (
                              'BENEFICIAL_OWNER', 'JOINT_OWNER'
                          )
                   )
               )
        ), changed_today AS (
            SELECT DISTINCT leg.instrument_id
              FROM scoped_quantity_events changed_event
              JOIN ledger.ledger_legs leg
                ON leg.tenant_id = $1
               AND leg.event_id = changed_event.id
             WHERE leg.instrument_id IS NOT NULL
        ), quotes AS (
            SELECT DISTINCT ON (o.subject_id)
                   o.subject_id,
                   o.value,
                   (o.metadata ->> 'changePct')::numeric AS change_pct,
                   o.quality_status AS quality
              FROM market.market_observations o
             WHERE (o.as_of AT TIME ZONE 'Asia/Shanghai')::date = $3
               AND o.quality_status IN ('FRESH', 'ESTIMATED')
               AND o.metadata ->> 'changePct'
                   ~ '^[+-]?[0-9]+([.][0-9]+)?$'
             ORDER BY o.subject_id, o.as_of DESC, o.observed_at DESC,
                      o.revision DESC
        )
        SELECT positions.instrument_id,
               COALESCE(alias.ticker, '') AS symbol,
               instrument.canonical_name AS name,
               positions.market_value,
               quotes.change_pct,
               quotes.quality,
               changed_today.instrument_id IS NOT NULL AS changed_today
          FROM positions
          LEFT JOIN quotes ON quotes.subject_id = positions.instrument_id
                          AND quotes.value = positions.market_price
          JOIN reference.instruments instrument
            ON instrument.id = positions.instrument_id
          LEFT JOIN changed_today
            ON changed_today.instrument_id = positions.instrument_id
          LEFT JOIN LATERAL (
              SELECT instrument_alias.alias_value AS ticker
                FROM reference.instrument_aliases instrument_alias
               WHERE instrument_alias.instrument_id = positions.instrument_id
                 AND instrument_alias.alias_type = 'TICKER'
                 AND instrument_alias.effective_from <= $3
                 AND (
                     instrument_alias.effective_to IS NULL
                     OR instrument_alias.effective_to >= $3
                 )
               ORDER BY instrument_alias.effective_from DESC
               LIMIT 1
          ) alias ON TRUE
        """,
        tenant_id,
        owner_user_id,
        trade_date,
    )
    result: list[dict] = []
    position_count = len(rows)
    hundred = Decimal("100")
    for row in rows:
        if bool(row["changed_today"]):
            continue
        try:
            market_value = Decimal(str(row["market_value"]))
            change_pct = Decimal(str(row["change_pct"]))
            denominator = hundred + change_pct
        except (ArithmeticError, TypeError, ValueError):
            continue
        if (
            not market_value.is_finite()
            or not change_pct.is_finite()
            or denominator == 0
        ):
            continue
        contribution = (market_value * change_pct / denominator).quantize(
            Decimal("0.01")
        )
        result.append(
            {
                "instrumentId": str(row["instrument_id"]),
                "symbol": str(row["symbol"] or ""),
                "name": str(row["name"] or row["symbol"] or "标的"),
                "changePct": str(change_pct),
                "contributionAmount": str(contribution),
                "quality": str(row["quality"]),
            }
        )
    items = sorted(
        result,
        key=lambda item: abs(Decimal(item["contributionAmount"])),
        reverse=True,
    )
    return {
        "items": items,
        "complete": position_count > 0 and len(items) == position_count,
        "positionCount": position_count,
        "attributedCount": len(items),
    }


async def latest_day_change_pct(
    conn: asyncpg.Connection, tenant_id: str, instrument_ids: list[str]
) -> dict[str, Decimal]:
    """Latest persisted day-change % (今日涨跌幅) per held instrument.

    The Worker refreshes equities quotes into ``market.market_observations``;
    the intraday alert evaluation reads that already-persisted value instead
    of calling an external quote provider on every 5-minute tick.
    """
    if not instrument_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (o.subject_id)
               o.subject_id,
               o.metadata ->> 'changePct' AS change_pct
          FROM market.market_observations o
         WHERE o.subject_id = ANY($1::uuid[])
           AND o.metadata ? 'changePct'
         ORDER BY o.subject_id, o.as_of DESC, o.observed_at DESC
        """,
        instrument_ids,
    )
    result: dict[str, Decimal] = {}
    for row in rows:
        raw = row["change_pct"]
        if raw is None:
            continue
        try:
            result[str(row["subject_id"])] = Decimal(str(raw))
        except (ValueError, TypeError):
            continue
    return result


async def claim_intraday_alert(
    conn: asyncpg.Connection,
    tenant_id: str,
    user_id: str,
    alert_key: str,
    now: datetime,
    window: timedelta,
) -> bool:
    """Record (and gate) a single intraday alert for one user.

    Returns ``True`` when the alert may be delivered now (it was never sent, or
    was sent longer than ``window`` ago); ``False`` when the same alert fired
    within the cooldown window and must be suppressed. The check-then-upsert
    runs inside the tenant transaction, which the runner's per-member lease
    serializes, so concurrent ticks for the same user cannot double-send.
    """
    since = now - window
    last = await conn.fetchval(
        """
        SELECT last_sent_at
          FROM workflow.intraday_alert_throttle
         WHERE tenant_id = $1 AND user_id = $2 AND alert_key = $3
        """,
        tenant_id,
        user_id,
        alert_key,
    )
    if last is not None and last >= since:
        return False
    await conn.execute(
        """
        INSERT INTO workflow.intraday_alert_throttle
            (tenant_id, user_id, alert_key, last_sent_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tenant_id, user_id, alert_key)
        DO UPDATE SET last_sent_at = EXCLUDED.last_sent_at
        """,
        tenant_id,
        user_id,
        alert_key,
        now,
    )
    return True
