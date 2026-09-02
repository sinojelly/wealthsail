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
