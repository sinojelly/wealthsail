"""079 real roles/SQL in an explicitly selected disposable validation database."""

import asyncio
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from backend.common.database import create_pool, tenant_transaction
from backend.errors import NotFoundError
from backend.investment import decision_repository as repo
from test_opportunity_monitors import NOW, result
from test_opportunity_monitors import board_result, research_result

DSN = os.environ.get("INVESTMENT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="isolated validation database required"),
]


async def seed(pool):
    tenant, actor, other = uuid4(), uuid4(), uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO identity.tenants(id,name) VALUES($1,'opportunity isolated fixture')",
            tenant,
        )
        for user in (actor, other):
            await conn.execute(
                "INSERT INTO identity.profiles(id,nickname) VALUES($1,'isolated actor')",
                user,
            )
            await conn.execute(
                "INSERT INTO identity.tenant_members(tenant_id,user_id,role) VALUES($1,$2,'OWNER')",
                tenant,
                user,
            )
    return tenant, actor, other


async def test_actor_rls_monitor_baseline_notice_ack_and_precise_scope():
    pool = await create_pool(DSN, min_size=1, max_size=2)
    worker = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    try:
        tenant, actor, other = await seed(pool)
        scope = {"direction_id": "a-share:broad", "index_id": "hs300"}
        async with tenant_transaction(pool, actor, tenant) as conn:
            saved = await repo.set_monitor(
                conn, tenant, actor, scope, True, NOW - timedelta(days=1)
            )
        async with tenant_transaction(pool, other, tenant) as conn:
            await repo.set_monitor(
                conn, tenant, other, scope, True, NOW - timedelta(days=1)
            )
            rows = await conn.fetch(
                "SELECT user_id FROM risk.opportunity_condition_monitors WHERE tenant_id=$1",
                tenant,
            )
            assert {row["user_id"] for row in rows} == {other}
            assert all(
                row["id"] != saved["id"]
                for row in await repo.list_monitors(conn, tenant, other)
            )
        async with worker.acquire() as conn:
            monitor = dict(
                await conn.fetchrow(
                    "SELECT * FROM risk.opportunity_condition_monitors WHERE id=$1",
                    UUID(saved["id"]),
                )
            )
            async with conn.transaction():
                assert (await repo.record_monitor_result(conn, monitor, result(), NOW))[
                    "baseline"
                ]
            async with conn.transaction():
                assert (
                    await repo.record_monitor_result(
                        conn,
                        monitor,
                        result("POSITION_REVIEW"),
                        NOW + timedelta(minutes=15),
                    )
                )["notice"]
        async with tenant_transaction(pool, actor, tenant) as conn:
            notices = await repo.list_notices(
                conn, tenant, actor, **scope, exact_scope=True
            )
            assert len(notices) == 1 and "持仓风险复核" in notices[0]["summary"]
            assert (
                await repo.list_notices(
                    conn, tenant, actor, **scope, fund_market="SSE", exact_scope=True
                )
                == []
            )
            notice_id = UUID(notices[0]["id"])
            assert (await repo.ack_notice(conn, tenant, actor, notice_id, NOW))[
                "readAt"
            ] == NOW
        async with tenant_transaction(pool, other, tenant) as conn:
            assert await repo.list_notices(conn, tenant, other) == []
            with pytest.raises(NotFoundError):
                await repo.ack_notice(conn, tenant, actor, notice_id, NOW)
        async with worker.acquire() as conn:
            privileges = await conn.fetchrow("""SELECT has_table_privilege('wealthsail_api','research.opportunity_strategy_validations','INSERT') AS api_validate,
              has_table_privilege('wealthsail_worker','risk.opportunity_condition_notices','UPDATE') AS worker_ack,
              has_table_privilege('authenticated','risk.opportunity_condition_notices','SELECT') AS browser_read,
              has_table_privilege('wealthsail_worker','reference.opportunity_source_permissions','INSERT') AS worker_approve""")
            assert not any(privileges.values())
    finally:
        await worker.close()
        await pool.close()


async def test_concurrent_monitor_restarts_and_repeated_recovery_append_episodes():
    pool = await create_pool(DSN, min_size=1, max_size=3)
    worker = await create_pool(
        DSN, min_size=1, max_size=3, runtime_role="wealthsail_worker"
    )
    try:
        tenant, actor, _ = await seed(pool)
        async with tenant_transaction(pool, actor, tenant) as conn:
            saved = await repo.set_monitor(
                conn,
                tenant,
                actor,
                {"direction_id": "gold:physical"},
                True,
                NOW - timedelta(days=1),
            )
        async with worker.acquire() as conn:
            monitor = dict(
                await conn.fetchrow(
                    "SELECT * FROM risk.opportunity_condition_monitors WHERE id::text=$1",
                    saved["id"],
                )
            )

        async def persist(state, at):
            async with worker.acquire() as conn:
                async with conn.transaction():
                    return await repo.record_monitor_result(
                        conn, monitor, result(state), at
                    )

        initial = await asyncio.gather(persist("WAIT", NOW), persist("WAIT", NOW))
        assert sum(row["baseline"] for row in initial) == 1
        for i, state in enumerate(("HOLD_REVIEW", "WAIT", "HOLD_REVIEW"), 1):
            assert (await persist(state, NOW + timedelta(minutes=15 * i)))["notice"]
        assert not (await persist("HOLD_REVIEW", NOW + timedelta(hours=1)))["notice"]
        async with worker.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM risk.opportunity_decision_snapshots WHERE monitor_id=$1",
                    monitor["id"],
                )
                == 4
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM risk.opportunity_condition_notices WHERE monitor_id=$1",
                    monitor["id"],
                )
                == 3
            )
        async with tenant_transaction(pool, actor, tenant) as conn:
            same = await repo.set_monitor(
                conn,
                tenant,
                actor,
                {"direction_id": "gold:physical"},
                True,
                NOW + timedelta(hours=2),
            )
            assert same["enabledAt"] == monitor["enabled_at"]
            await repo.set_monitor(
                conn,
                tenant,
                actor,
                {"direction_id": "gold:physical"},
                False,
                NOW + timedelta(hours=2),
            )
            fresh = await repo.set_monitor(
                conn,
                tenant,
                actor,
                {"direction_id": "gold:physical"},
                True,
                NOW + timedelta(hours=3),
            )
        assert fresh["enabledAt"] != monitor["enabled_at"]
        assert (await persist("WAIT", NOW + timedelta(hours=4))).get("skipped") is True
    finally:
        await worker.close()
        await pool.close()


async def test_mine_sql_excludes_other_collaborator_and_expired_ownership_and_future_nav():
    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, other = await seed(pool)
        fund, peer = uuid4(), uuid4()
        expected = set()
        async with pool.acquire() as conn:
            index = "isolated-" + uuid4().hex
            await conn.execute(
                "INSERT INTO reference.research_indices(id,code,name,market,verified_at) VALUES($1,$1,'isolated index','SSE',$2)",
                index,
                NOW - timedelta(days=1),
            )
            for instrument in (fund, peer):
                await conn.execute(
                    "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'EXCHANGE_FUND','SSE','isolated fund')",
                    instrument,
                )
                await conn.execute(
                    "INSERT INTO reference.fund_index_links(instrument_id,index_id,effective_from,source_uri,verified_at) VALUES($1,$2,$3,'https://isolated.test/mapping',$4)",
                    instrument,
                    index,
                    (NOW - timedelta(days=100)).date(),
                    NOW - timedelta(days=1),
                )
            for kind in ("own", "joint", "other", "collaborator", "expired", "future"):
                account = uuid4()
                await conn.execute(
                    "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL',$3)",
                    account,
                    tenant,
                    kind,
                )
                await conn.execute(
                    """INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,effective_from,effective_to,permission_role,created_by)
                  VALUES($1,$2,$3,$4,$5,$6,'OWNER',$3)""",
                    tenant,
                    account,
                    other if kind == "other" else actor,
                    "JOINT_OWNER"
                    if kind == "joint"
                    else "COLLABORATOR"
                    if kind == "collaborator"
                    else "BENEFICIAL_OWNER",
                    (NOW - timedelta(days=100)).date(),
                    (NOW - timedelta(days=1)).date() if kind == "expired" else None,
                )
                await conn.execute(
                    """INSERT INTO ledger.account_nav_snapshots(tenant_id,account_id,as_of,cash_balance,positions_market_value,total_nav,data_quality_status,valuation_coverage_pct,unpriced_position_count,created_at)
                  VALUES($1,$2,$3,50,50,100,'FRESH',100,0,$4)""",
                    tenant,
                    account,
                    NOW.date(),
                    NOW + timedelta(days=1)
                    if kind == "future"
                    else NOW - timedelta(minutes=1),
                )
                await conn.execute(
                    """INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,quantity,avg_cost,market_price,market_value,data_quality_status,created_at)
                  VALUES($1,$2,$3,$4,1,50,50,50,'FRESH',$5)""",
                    tenant,
                    account,
                    peer if kind == "joint" else fund,
                    NOW.date(),
                    NOW - timedelta(minutes=1),
                )
                if kind in {"own", "joint", "future"}:
                    expected.add(account)
        async with tenant_transaction(pool, actor, tenant) as conn:
            nav, positions, policy = await repo.mine_portfolio(conn, tenant, actor, NOW)
            assert {row["account_id"] for row in nav} == expected
            assert {row["account_id"] for row in positions} == expected
            assert len([row for row in nav if row["as_of"] is None]) == 1
            assert all(row["index_ids"] == [index] for row in positions)
            assert policy is None
    finally:
        await pool.close()


async def test_concurrent_monitor_scopes_share_one_actor_daily_notice_budget():
    pool = await create_pool(DSN, min_size=1, max_size=3)
    worker = await create_pool(
        DSN, min_size=1, max_size=3, runtime_role="wealthsail_worker"
    )
    try:
        tenant, actor, _ = await seed(pool)
        monitors = []
        async with tenant_transaction(pool, actor, tenant) as conn:
            for direction in ("gold:physical", "bond:government"):
                saved = await repo.set_monitor(
                    conn,
                    tenant,
                    actor,
                    {"direction_id": direction},
                    True,
                    NOW - timedelta(days=1),
                )
                monitors.append(saved["id"])
        async with worker.acquire() as conn:
            monitors = [
                dict(row)
                for row in await conn.fetch(
                    "SELECT * FROM risk.opportunity_condition_monitors WHERE id::text=ANY($1::text[])",
                    monitors,
                )
            ]
            for monitor in monitors:
                async with conn.transaction():
                    await repo.record_monitor_result(conn, monitor, result(), NOW)

        async def change(monitor):
            changed = result("POSITION_REVIEW")
            changed["portfolio"]["noticeBudget"] = 1
            async with worker.acquire() as conn:
                async with conn.transaction():
                    return await repo.record_monitor_result(
                        conn, monitor, changed, NOW + timedelta(minutes=15)
                    )

        outputs = await asyncio.gather(*(change(monitor) for monitor in monitors))
        assert sum(row["notice"] for row in outputs) == 1
        assert sum(row.get("reason") == "BUDGET_OR_SILENCE" for row in outputs) == 1
    finally:
        await worker.close()
        await pool.close()


async def test_real_board_version_upgrade_is_baseline_and_ack_does_not_change_canonical_conditions():
    pool = await create_pool(DSN, min_size=1, max_size=2)
    worker = await create_pool(
        DSN, min_size=1, max_size=2, runtime_role="wealthsail_worker"
    )
    try:
        tenant, actor, other = await seed(pool)
        scope = {"direction_id": "gold:physical", "index_id": "au9999"}
        async with tenant_transaction(pool, actor, tenant) as conn:
            saved = await repo.set_monitor(
                conn, tenant, actor, scope, True, NOW - timedelta(days=1)
            )
        async with worker.acquire() as conn:
            monitor = dict(
                await conn.fetchrow(
                    "SELECT * FROM risk.opportunity_condition_monitors WHERE id::text=$1",
                    saved["id"],
                )
            )
            async with conn.transaction():
                assert (
                    await repo.record_monitor_result(
                        conn, monitor, research_result(), NOW - timedelta(minutes=30)
                    )
                )["baseline"]
            async with conn.transaction():
                upgrade = await repo.record_monitor_result(
                    conn, monitor, board_result(), NOW
                )
                assert upgrade["baseline"] and not upgrade["notice"]
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM risk.opportunity_condition_notices WHERE monitor_id=$1",
                    monitor["id"],
                )
                == 0
            )
            current = board_result("HIGH")
            async with conn.transaction():
                assert (
                    await repo.record_monitor_result(
                        conn, monitor, current, NOW + timedelta(minutes=15)
                    )
                )["notice"]
            before = await conn.fetch(
                "SELECT condition_fingerprint,result FROM risk.opportunity_decision_snapshots WHERE monitor_id=$1 ORDER BY as_of",
                monitor["id"],
            )
            assert len(before) == 3
        async with tenant_transaction(pool, actor, tenant) as conn:
            notices = await repo.list_notices(
                conn, tenant, actor, **scope, exact_scope=True
            )
            assert len(notices) == 1 and notices[0]["severity"] == "WATCH"
            await repo.ack_notice(
                conn, tenant, actor, UUID(notices[0]["id"]), NOW + timedelta(minutes=16)
            )
        async with tenant_transaction(pool, other, tenant) as conn:
            assert (
                await repo.list_notices(conn, tenant, other, **scope, exact_scope=True)
                == []
            )
        async with worker.acquire() as conn:
            after = await conn.fetch(
                "SELECT condition_fingerprint,result FROM risk.opportunity_decision_snapshots WHERE monitor_id=$1 ORDER BY as_of",
                monitor["id"],
            )
            assert before == after
            current["researchBoard"]["items"][0].update(
                readAt="just read", summary="copy-only update"
            )
            async with conn.transaction():
                assert not (
                    await repo.record_monitor_result(
                        conn, monitor, current, NOW + timedelta(minutes=30)
                    )
                )["notice"]
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM risk.opportunity_condition_notices WHERE monitor_id=$1",
                    monitor["id"],
                )
                == 1
            )
    finally:
        await worker.close()
        await pool.close()


async def test_real_current_calendar_proof_rejects_old_fresh_projection_but_keeps_holding_identity():
    from datetime import date
    from backend.investment.decision import portfolio_summary, evaluate
    from backend.macro.research_board import confirmed_breach

    pool = await create_pool(DSN, min_size=1, max_size=2)
    try:
        tenant, actor, _ = await seed(pool)
        account, fund = uuid4(), uuid4()
        policy = {
            "policy_status": "CONFIRMED",
            "single_position_limit": 40,
            "cash_target_pct": 10,
            "max_drawdown_limit": 35,
        }
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO identity.accounts(id,tenant_id,account_type,name) VALUES($1,$2,'MANUAL','currentness fixture')",
                account,
                tenant,
            )
            await conn.execute(
                "INSERT INTO identity.account_members(tenant_id,account_id,user_id,relationship,permission_role,created_by,effective_from) VALUES($1,$2,$3,'BENEFICIAL_OWNER','OWNER',$3,'2019-01-01')",
                tenant,
                account,
                actor,
            )
            await conn.execute(
                "INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name) VALUES($1,'EXCHANGE_FUND','SSE','currentness fixture')",
                fund,
            )
            await conn.execute(
                "INSERT INTO reference.trading_calendars(market,calendar_date,is_trading_day) VALUES('SSE',$1,TRUE) ON CONFLICT DO NOTHING",
                NOW.date(),
            )

        async def snapshot(day):
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO ledger.account_nav_snapshots(tenant_id,account_id,as_of,cash_balance,positions_market_value,total_nav,data_quality_status,valuation_coverage_pct,unpriced_position_count,created_at) VALUES($1,$2,$3,10,90,100,'FRESH',100,0,$4)",
                    tenant,
                    account,
                    day,
                    NOW - timedelta(minutes=1),
                )
                await conn.execute(
                    "INSERT INTO ledger.position_snapshots(tenant_id,account_id,instrument_id,as_of,quantity,avg_cost,market_price,market_value,data_quality_status,created_at) VALUES($1,$2,$3,$4,1,90,90,90,'FRESH',$5)",
                    tenant,
                    account,
                    fund,
                    day,
                    NOW - timedelta(minutes=1),
                )

        await snapshot(date(2020, 1, 2))
        async with tenant_transaction(pool, actor, tenant) as conn:
            navs, positions, _ = await repo.mine_portfolio(conn, tenant, actor, NOW)
            assert navs[0]["valuation_current"] is False
            old = portfolio_summary(navs, positions, policy, instrument_id=fund)
            decision = evaluate(
                {},
                held=old["heldSelected"],
                budget_violation=old["budgetViolation"],
                portfolio=old,
            )
            assert old["heldSelected"] is True and old["asOf"] == "2020-01-02"
            assert (
                old["state"] == old["budgetState"] == "UNKNOWN"
                and decision["assessment"]["riskState"] == "UNKNOWN"
            )
            assert not confirmed_breach(decision, old)
        await snapshot(NOW.date())
        async with tenant_transaction(pool, actor, tenant) as conn:
            navs, positions, _ = await repo.mine_portfolio(conn, tenant, actor, NOW)
            assert navs[0]["valuation_current"] is True
            current = portfolio_summary(navs, positions, policy, instrument_id=fund)
            decision = evaluate(
                {},
                held=current["heldSelected"],
                budget_violation=current["budgetViolation"],
                portfolio=current,
            )
            assert current["state"] == "READY" and current["budgetState"] == "BLOCKED"
            assert confirmed_breach(decision, current)
    finally:
        await pool.close()
