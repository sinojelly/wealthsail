"""Personal fund watch preferences and the actor's current owned fund universe."""

from backend.investment import study_repository as repository
from backend.investment.decision import semantic_hash


async def list_watch_context(conn, tenant_id, user_id, now):
    """Read every held fund once, independently of study-history pagination."""
    from backend.investment.discovery import _cached_histories
    from backend.investment.theme_graph import nav_plan

    rows = await conn.fetch(
        """WITH latest AS (
          SELECT DISTINCT ON(p.account_id,p.instrument_id)
            p.instrument_id,p.account_id,p.quantity
          FROM ledger.position_snapshots p
          JOIN identity.accounts a ON a.id=p.account_id AND a.tenant_id=p.tenant_id AND a.status='ACTIVE'
          WHERE p.tenant_id=$1 AND p.created_at<=$3
            AND p.as_of<=($3 AT TIME ZONE 'Asia/Shanghai')::date
            AND EXISTS(SELECT 1 FROM identity.account_members owner
              WHERE owner.tenant_id=p.tenant_id AND owner.account_id=p.account_id AND owner.user_id=$2
                AND owner.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
                AND owner.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
                AND (owner.effective_to IS NULL OR owner.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date))
          ORDER BY p.account_id,p.instrument_id,p.as_of DESC,p.created_at DESC
        ) SELECT DISTINCT i.id AS instrument_id,ticker.fund_code,
            i.primary_market AS market,i.canonical_name AS fund_name
          FROM latest p JOIN reference.instruments i ON i.id=p.instrument_id
          LEFT JOIN LATERAL (
            SELECT alias.alias_value AS fund_code FROM reference.instrument_aliases alias
            WHERE alias.instrument_id=i.id AND alias.alias_type='TICKER'
              AND alias.alias_value ~ '^[0-9]{6}$'
              AND alias.effective_from<=($3 AT TIME ZONE 'Asia/Shanghai')::date
            ORDER BY (alias.effective_to IS NULL OR alias.effective_to>=($3 AT TIME ZONE 'Asia/Shanghai')::date) DESC,
              alias.effective_from DESC,alias.alias_value LIMIT 1
          ) ticker ON TRUE
          WHERE p.quantity>0 AND p.quantity::text NOT IN ('NaN','Infinity','-Infinity')
            AND i.asset_type IN ('OFF_EXCHANGE_FUND','EXCHANGE_FUND')
          ORDER BY market,fund_code,instrument_id""",
        tenant_id,
        user_id,
        now,
    )
    preferences = await conn.fetch(
        """SELECT fund_key,fund_market FROM research.fund_watch_preferences
        WHERE tenant_id=$1 AND user_id=$2 AND NOT enabled""",
        tenant_id,
        user_id,
    )
    histories = await _cached_histories(
        conn,
        sorted(
            {
                row["fund_code"]
                for row in rows
                if row["market"] == "FUND_CN" and row["fund_code"]
            }
        ),
        now,
    )
    funds = []
    for row in rows:
        code, market = row["fund_code"], row["market"]
        plan = (
            nav_plan(histories[code], now=now, label=row["fund_name"])
            if market == "FUND_CN" and code in histories
            else None
        )
        confirmed = (
            {
                "value": plan["currentPrice"],
                "asOf": plan["asOf"],
                "changePct": plan.get("currentChangePct"),
                "retrievedAt": plan["retrievedAt"],
                "sourceUri": plan.get("sourceUri"),
                "fundCode": code,
                "subjectKind": "nav",
                "subjectId": code,
                "priceBasis": "UNIT_NAV",
                "unit": plan["unit"],
                "quality": plan.get("historyQuality"),
                "usable": plan.get("historyQuality") == "READY",
            }
            if plan is not None
            else None
        )
        funds.append(
            {
                "instrumentId": str(row["instrument_id"]),
                "fundCode": code,
                "fundMarket": market,
                "label": row["fund_name"],
                "confirmedNav": confirmed,
            }
        )
    return {
        "heldFunds": funds,
        "suppressedFundKeys": [
            f"{row['fund_market']}:{row['fund_key']}" for row in preferences
        ],
    }


async def _preference(
    conn, tenant_id, user_id, code, market, enabled, now, instrument_id=None
):
    key = code or f"instrument:{instrument_id}"
    await conn.execute(
        """INSERT INTO research.fund_watch_preferences(tenant_id,user_id,fund_code,fund_market,enabled,updated_at,fund_key,instrument_id)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT(tenant_id,user_id,fund_market,fund_key)
        DO UPDATE SET enabled=EXCLUDED.enabled,updated_at=EXCLUDED.updated_at""",
        tenant_id,
        user_id,
        code,
        market,
        enabled,
        now,
        key,
        instrument_id,
    )


async def _change_track(conn, track, enabled, now):
    if track["enabled"] == enabled:
        return track
    track = await repository.advance(conn, track, now, enabled=enabled)
    version = await repository.latest_version(
        conn, track["tenant_id"], track["user_id"], track["id"]
    )
    payload = {"operation": "RESUMED" if enabled else "STOPPED", "source": "FUND_WATCH"}
    await repository.add_note(
        conn,
        track,
        version["id"],
        "LIFECYCLE",
        payload,
        f"fund-watch:{track['generation']}",
        semantic_hash(payload),
        now,
    )
    return track


async def set_watch_status(
    conn, tenant_id, user_id, code, market, enabled, now, *, instrument_id=None
):
    """Stop/resume matching journals and persist visibility without ledger writes."""
    # Every lifecycle path locks study rows before writing fund preferences.
    # Reversing these locks would deadlock against explicit study resume.
    tracks = await conn.fetch(
        """SELECT t.* FROM research.study_tracks t
        WHERE t.tenant_id=$1 AND t.user_id=$2 AND (
          (t.scope->>'fundCode'=$3 AND COALESCE(t.scope->>'fundMarket','FUND_CN')=$4)
          OR t.scope->>'instrumentId'=$5
          OR (NULLIF(t.scope->>'fundCode','') IS NULL AND EXISTS(
            SELECT 1 FROM reference.instrument_aliases alias
            JOIN reference.instruments i ON i.id=alias.instrument_id
            WHERE alias.instrument_id::text=t.scope->>'instrumentId'
              AND alias.alias_type='TICKER' AND alias.alias_value=$3 AND i.primary_market=$4)))
        ORDER BY t.id FOR UPDATE""",
        tenant_id,
        user_id,
        code,
        market,
        str(instrument_id) if instrument_id is not None else None,
    )
    for track in tracks:
        await _change_track(conn, dict(track), enabled, now)
    await _preference(
        conn, tenant_id, user_id, code, market, enabled, now, instrument_id
    )
    identity = {"fundCode": code} if code else {"instrumentId": str(instrument_id)}
    if enabled:
        await clear_suppression(
            conn, tenant_id, user_id, {**identity, "fundMarket": market}, now
        )
    return {**identity, "fundMarket": market, "enabled": enabled}


async def clear_suppression(conn, tenant_id, user_id, scope, now):
    code, market = scope.get("fundCode"), scope.get("fundMarket") or "FUND_CN"
    if code or scope.get("instrumentId"):
        # Resolve both persisted identities so ticker registration after an
        # instrument-only cancellation does not make explicit re-track ineffective.
        await conn.execute(
            """UPDATE research.fund_watch_preferences p SET enabled=TRUE,updated_at=$6
            WHERE p.tenant_id=$1 AND p.user_id=$2 AND NOT p.enabled AND (
              (p.fund_code=$3 AND p.fund_market=$4) OR p.instrument_id::text=$5
              OR EXISTS(SELECT 1 FROM reference.instrument_aliases alias
                JOIN reference.instruments i ON i.id=alias.instrument_id
                WHERE alias.alias_type='TICKER' AND (
                  (alias.instrument_id=p.instrument_id AND alias.alias_value=$3 AND i.primary_market=$4)
                  OR (alias.instrument_id::text=$5 AND alias.alias_value=p.fund_code AND i.primary_market=p.fund_market))))""",
            tenant_id,
            user_id,
            code,
            market,
            str(scope["instrumentId"]) if scope.get("instrumentId") else None,
            now,
        )


async def resume_existing_study(conn, tenant_id, user_id, track_id, now):
    track = await repository.lock_track(conn, tenant_id, user_id, track_id)
    await _change_track(conn, track, True, now)
    await clear_suppression(
        conn, tenant_id, user_id, repository.decode(track["scope"]), now
    )
