"""Opt-in NAS install acceptance on a disposable PostgreSQL 16/18 cluster.

Set NAS_TEST_ADMIN_DATABASE_URL to an explicitly isolated loopback cluster's
postgres database, logged in as its nas_test_admin superuser. The cluster must
have no postgres role, application roles, or non-template databases. This test
creates prismscore_db and wealthsail_nas_test and intentionally leaves them for
the caller to inspect and dispose of with the entire temporary cluster.

No .env files, external services, or running application server are used.
"""

from __future__ import annotations

import base64
import os
from decimal import Decimal
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from backend.common.auth import decode_access_token, verify_password
from backend.common.database import create_pool, tenant_transaction
from backend.errors import AuthRequiredError, ForbiddenError
from backend.identity.domain_service import (
    IdentityService,
    IdentitySettings,
    TenantContext,
)
from scripts.configure_nas import database_dsn
from scripts.initialize_postgres import DEDICATED_ROLES, initialize_database


def _isolated_admin_url() -> str:
    dsn = os.environ.get("NAS_TEST_ADMIN_DATABASE_URL", "")
    if not dsn:
        pytest.skip("disposable NAS installation cluster is not configured")
    parts = urlsplit(dsn)
    if (
        parts.scheme not in {"postgres", "postgresql"}
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parts.port is None
        or unquote(parts.username or "") != "nas_test_admin"
        or not parts.password
        or parts.path != "/postgres"
        or parts.fragment
        or any(
            key != "sslmode" or value != "disable"
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        )
    ):
        raise ValueError(
            "NAS_TEST_ADMIN_DATABASE_URL requires an explicit loopback port, "
            "nas_test_admin login, /postgres, and no options except sslmode=disable"
        )
    return dsn


async def _sentinel_snapshot(maintenance, sentinel):
    return (
        await maintenance.fetchval(
            "SELECT datacl::text FROM pg_database WHERE datname='prismscore_db'"
        ),
        await sentinel.fetchval(
            "SELECT nspacl::text FROM pg_namespace WHERE nspname='public'"
        ),
        await sentinel.fetchval(
            "SELECT relacl::text FROM pg_class WHERE oid='public.nas_sentinel'::regclass"
        ),
        await sentinel.fetchval("SELECT marker FROM public.nas_sentinel"),
    )


async def _assert_http_onboarding(pool, api_url, bootstrap, identity_settings, monkeypatch):
    from backend import config
    from backend.dependencies import get_settings_dep

    settings = config.Settings(
        _env_file=None,
        app_env="production",
        database_url=api_url,
        worker_database_url="",
        jwt_secret=identity_settings.jwt_secret,
        refresh_token_hmac_key=identity_settings.refresh_token_hmac_key,
        password_reset_hmac_key="nas-integration-password-reset-" + "d" * 40,
        binding_hmac_key=identity_settings.binding_hmac_key,
        routing_encryption_key=base64.urlsafe_b64encode(b"e" * 32).decode("ascii"),
        wecom_enabled=False,
        password_reset_email_enabled=False,
        wecom_callback_token="",
        wecom_corp_secret="",
        wecom_bot_secret="",
        wecom_opportunity_webhook_url="",
        model_api_key=None,
        auth_dev_mode=False,
        web_base_url="https://nas.example.test",
        cors_origins=["https://nas.example.test"],
    )
    # Importing backend.main also constructs its module-level app. Replace its
    # config source before import so neither app can load the workspace .env.
    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    from backend import main

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    app = main.create_app()
    app.state.db_pool = pool
    app.dependency_overrides[get_settings_dep] = lambda: settings
    # ASGITransport does not run lifespan; all requests use the existing real
    # restricted pool and stay in process, without contacting any provider.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://nas.example.test",
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": bootstrap["email"], "password": bootstrap["password"]},
        )
        assert login.status_code == 200, login.text
        result = login.json()["data"]
        assert "_refreshToken" not in result
        refresh_cookie = client.cookies.get(settings.refresh_cookie_name)
        assert refresh_cookie
        set_cookie = login.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "secure" in set_cookie
        assert "samesite=strict" in set_cookie
        assert "path=/api/v1/auth" in set_cookie
        client.headers["Authorization"] = f"Bearer {result['accessToken']}"
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["data"]["profile"]["email"] == bootstrap["email"]
        assert me.json()["data"]["platformRoles"] == ["PLATFORM_ADMIN"]
        summary = await client.get("/api/v1/portfolio/summary")
        assert summary.status_code == 200, summary.text
        assert summary.json()["meta"]["qualityStatus"] == "MISSING"
        assert summary.json()["data"]["totalAsset"] is None
        assert summary.json()["data"]["positionCount"] == 0
        refreshed = await client.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert client.cookies.get(settings.refresh_cookie_name) != refresh_cookie
        assert refreshed.json()["data"]["user"]["id"] == result["user"]["id"]
        logged_out = await client.post("/api/v1/auth/logout")
        assert logged_out.status_code == 200, logged_out.text
        assert client.cookies.get(settings.refresh_cookie_name) is None
        rejected = await client.post("/api/v1/auth/refresh")
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_fresh_nas_installation_identity_isolation_and_upgrade_preservation(
    monkeypatch, tmp_path,
):
    # Explicitly prevent configuration consumers from discovering root .env.
    monkeypatch.setenv("WEALTHSAIL_ENV_FILE", str(tmp_path / "absent.env"))
    admin_url = _isolated_admin_url()
    parts = urlsplit(admin_url)
    maintenance = await asyncpg.connect(admin_url)
    sentinel = admin = pool = None
    try:
        authority = await maintenance.fetchrow(
            "SELECT current_user::text AS username, rolsuper FROM pg_roles WHERE rolname=current_user"
        )
        assert dict(authority) == {"username": "nas_test_admin", "rolsuper": True}
        assert not await maintenance.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='postgres')"
        ), "use a fresh cluster initialized with -U nas_test_admin"
        assert set(await maintenance.fetchval(
            "SELECT array_agg(datname) FROM pg_database"
        )) == {"postgres", "template0", "template1"}, "cluster must be disposable and pristine"
        assert not await maintenance.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))",
            [*DEDICATED_ROLES, "authenticated", "prismscore_reader"],
        ), "application roles must not predate this isolated test"
        assert not await maintenance.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname <> 'nas_test_admin' AND left(rolname,3) <> 'pg_')"
        ), "cluster must not have other application login or execution roles"
        assert not await maintenance.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_namespace
                 WHERE nspname NOT IN ('public','information_schema')
                   AND left(nspname,3) <> 'pg_'
            ) OR EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname <> 'information_schema'
                   AND left(n.nspname,3) <> 'pg_'
                   AND c.relkind IN ('r','p','v','m','S','f')
            )
            """
        ), "postgres maintenance database must have no user schemas or relations"

        # Represent the unrelated application sharing the NAS database cluster.
        await maintenance.execute("CREATE ROLE prismscore_reader NOLOGIN")
        await maintenance.execute("CREATE DATABASE prismscore_db TEMPLATE template0")
        await maintenance.execute("REVOKE ALL ON DATABASE prismscore_db FROM PUBLIC")
        await maintenance.execute("GRANT CONNECT ON DATABASE prismscore_db TO prismscore_reader")
        sentinel_url = urlunsplit(parts._replace(path="/prismscore_db"))
        sentinel = await asyncpg.connect(sentinel_url)
        await sentinel.execute(
            """
            CREATE TABLE public.nas_sentinel(marker TEXT NOT NULL);
            INSERT INTO public.nas_sentinel VALUES ('other-application-remains-intact');
            GRANT USAGE ON SCHEMA public TO prismscore_reader;
            GRANT SELECT ON public.nas_sentinel TO prismscore_reader;
            """
        )
        sentinel_before = await _sentinel_snapshot(maintenance, sentinel)

        target_database = "wealthsail_nas_test"
        api_url = database_dsn(
            host=parts.hostname, port=parts.port, database=target_database,
            user="wealthsail_api_login", password="NAS-API-Only-Password:%@2026",
        )
        worker_url = database_dsn(
            host=parts.hostname, port=parts.port, database=target_database,
            user="wealthsail_worker_login", password="NAS-Worker-Only-Password:%@2026",
        )
        confirm_target = f"{'[' + parts.hostname + ']' if ':' in parts.hostname else parts.hostname}:{parts.port}/{target_database}"

        # A public function/type/extension also makes a database nonempty. None
        # of these necessarily creates a pg_class relation or an extra schema.
        for kind, ddl in (
            ("function", "CREATE FUNCTION public.operator_marker() RETURNS integer LANGUAGE sql AS 'SELECT 7'"),
            ("type", "CREATE TYPE public.operator_status AS ENUM ('ready')"),
            ("extension", "CREATE EXTENSION pgcrypto"),
        ):
            existing_database = f"wealthsail_existing_{kind}"
            await maintenance.execute(f"CREATE DATABASE {existing_database} TEMPLATE template0")
            existing_url = urlunsplit(parts._replace(path=f"/{existing_database}"))
            existing = await asyncpg.connect(existing_url)
            try:
                await existing.execute(ddl)
                with pytest.raises(RuntimeError, match="not an empty"):
                    await initialize_database(
                        api_url.replace(f"/{target_database}?", f"/{existing_database}?"),
                        worker_url.replace(f"/{target_database}?", f"/{existing_database}?"),
                        admin_user="nas_test_admin", admin_password=unquote(parts.password),
                        confirm_target=confirm_target.replace(target_database, existing_database),
                    )
                assert not await existing.fetchval(
                    "SELECT to_regclass('public.wealthsail_nas_installation') IS NOT NULL"
                )
                assert not await maintenance.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[]))",
                    list(DEDICATED_ROLES),
                )
            finally:
                await existing.close()
        bootstrap = {
            "email": "nas-admin@example.test", "password": "InitialWebPassword!2026",
            "nickname": "NAS 管理员", "tenant_name": "NAS 个人空间",
        }

        async def install(bootstrap_inputs=None):
            return await initialize_database(
                api_url, worker_url, admin_user="nas_test_admin",
                admin_password=unquote(parts.password), confirm_target=confirm_target,
                bootstrap=bootstrap_inputs,
            )

        # This executes every migration and both real runtime-login boundary checks.
        installed = await install(bootstrap)
        assert installed["createdDatabase"] is True
        assert installed["administrator"]["created"] is True
        admin_id = UUID(installed["administrator"]["userId"])
        admin_tenant = UUID(installed["administrator"]["tenantId"])
        admin = await asyncpg.connect(urlunsplit(parts._replace(path=f"/{target_database}")))
        counts = await admin.fetchrow(
            """
            SELECT (SELECT count(*) FROM identity.profiles) AS profiles,
                   (SELECT count(*) FROM identity.tenants) AS tenants,
                   (SELECT count(*) FROM identity.accounts) AS accounts,
                   (SELECT count(*) FROM ledger.ledger_events) AS events,
                   (SELECT count(*) FROM ledger.ledger_legs) AS legs,
                   (SELECT count(*) FROM ledger.position_snapshots) AS positions,
                   (SELECT count(*) FROM ledger.account_nav_snapshots) AS nav,
                   (SELECT count(*) FROM market.fund_lookthrough) AS lookthrough,
                   (SELECT count(*) FROM workflow.jobs) AS jobs
            """
        )
        assert dict(counts) == {
            "profiles": 1, "tenants": 1, "accounts": 0, "events": 0, "legs": 0,
            "positions": 0, "nav": 0, "lookthrough": 0, "jobs": 4,
        }
        original_hash = await admin.fetchval(
            "SELECT password_hash FROM identity.profiles WHERE id=$1", admin_id,
        )
        assert verify_password(bootstrap["password"], original_hash)
        assert await admin.fetchval(
            "SELECT initial_cleanup_done FROM public.wealthsail_nas_installation WHERE singleton"
        )
        assert await _sentinel_snapshot(maintenance, sentinel) == sentinel_before

        pool = await create_pool(api_url, min_size=1, max_size=2)
        identity_settings = IdentitySettings(
            jwt_secret="nas-integration-jwt-" + "a" * 40,
            refresh_token_hmac_key="nas-integration-refresh-" + "b" * 40,
            binding_hmac_key="nas-integration-invitation-" + "c" * 40,
        )
        await _assert_http_onboarding(pool, api_url, bootstrap, identity_settings, monkeypatch)
        identity = IdentityService(pool, identity_settings)
        login = await identity.login(email=bootstrap["email"], password=bootstrap["password"])
        assert login["user"]["platformRoles"] == ["PLATFORM_ADMIN"]
        session = decode_access_token(login["accessToken"], identity_settings.jwt_secret)
        assert session.user_id == str(admin_id)
        assert session.tenant_id == str(admin_tenant)
        actor = TenantContext(str(admin_tenant), str(admin_id), "OWNER")
        async with tenant_transaction(pool, actor.user_id, actor.tenant_id) as conn:
            me = await identity.get_me(conn, actor)
        assert me["platformRoles"] == ["PLATFORM_ADMIN"]
        assert me["activeTenantId"] == str(admin_tenant)

        # Real refresh rotation consumes the original token; logout revokes its successor.
        refreshed = await identity.refresh_session(login["_refreshToken"])
        assert refreshed["_refreshToken"] != login["_refreshToken"]
        with pytest.raises(AuthRequiredError):
            await identity.refresh_session(login["_refreshToken"])
        await identity.revoke_refresh_session(refreshed["_refreshToken"])
        await identity.revoke_refresh_session(refreshed["_refreshToken"])
        with pytest.raises(AuthRequiredError):
            await identity.refresh_session(refreshed["_refreshToken"])

        personal_invitation = await identity.create_personal_tenant_invitation(
            actor_user_id=str(admin_id), actor_tenant_id=str(admin_tenant),
            email="nas-owner@example.test", tenant_name="第二个个人空间",
        )
        owner = await identity.register(
            email="nas-owner@example.test", password="SecondOwnerPassword!2026",
            nickname="第二位用户", invitation_token=personal_invitation["token"],
        )
        owner_login = await identity.login(
            email=owner["email"], password="SecondOwnerPassword!2026",
        )
        assert owner_login["user"]["role"] == "OWNER"
        assert owner_login["user"]["platformRoles"] == []
        assert owner["tenantId"] != str(admin_tenant)
        with pytest.raises(ForbiddenError):
            await identity.create_personal_tenant_invitation(
                actor_user_id=owner["userId"], actor_tenant_id=owner["tenantId"],
                email="not-authorized@example.test", tenant_name="不应创建的租户",
            )
        owner_actor = TenantContext(owner["tenantId"], owner["userId"], "OWNER")
        member_invitation = await identity.create_invitation(
            owner_actor, email="nas-viewer@example.test", role="VIEWER",
        )
        viewer = await identity.register(
            email="nas-viewer@example.test", password="NewViewerPassword!2026",
            nickname="租户成员", invitation_token=member_invitation["token"],
        )
        assert viewer["tenantId"] == owner["tenantId"]
        assert await admin.fetchval("SELECT count(*) FROM workflow.jobs") == 8

        # Add isolated test facts only after proving that the fresh install was empty.
        own_account, other_account = uuid4(), uuid4()
        await admin.execute(
            """
            INSERT INTO identity.accounts(id,tenant_id,account_type,name)
            VALUES ($1,$2,'MANUAL','Admin account'), ($3,$4,'MANUAL','Other account')
            """,
            own_account, admin_tenant, other_account, UUID(owner["tenantId"]),
        )
        async with tenant_transaction(pool, str(admin_id), str(admin_tenant)) as conn:
            visible = await conn.fetch("SELECT id FROM identity.accounts ORDER BY id")
            assert [row["id"] for row in visible] == [own_account]
        async with tenant_transaction(pool, owner["userId"], owner["tenantId"]) as conn:
            visible = await conn.fetch("SELECT id FROM identity.accounts ORDER BY id")
            assert [row["id"] for row in visible] == [other_account]
        async with tenant_transaction(pool, owner["userId"], str(admin_tenant)) as conn:
            # Existing RLS checks memberships, independently of the requested
            # tenant claim: a forged claim cannot reveal the admin's account.
            visible = await conn.fetch("SELECT id FROM identity.accounts ORDER BY id")
            assert [row["id"] for row in visible] == [other_account]
        with pytest.raises(ForbiddenError):
            await identity.switch_tenant(owner_actor, tenant_id=str(admin_tenant), refresh_token=None)

        fund, holding = uuid4(), uuid4()
        await admin.execute(
            """
            INSERT INTO reference.instruments(id,asset_type,primary_market,canonical_name)
            VALUES ($1,'OFF_EXCHANGE_FUND','FUND_CN','Isolated imported fund'),
                   ($2,'CN_STOCK','SSE','Isolated imported holding')
            """,
            fund, holding,
        )
        lookthrough_id = await admin.fetchval(
            """
            INSERT INTO market.fund_lookthrough(
                fund_instrument_id,holding_instrument_id,weight_pct,report_period,metadata
            ) VALUES ($1,$2,12.34,DATE '2026-09-30','{"source":"isolated-real-import-test"}')
            RETURNING id
            """,
            fund, holding,
        )
        without_bootstrap = await install()
        assert without_bootstrap["createdDatabase"] is False
        assert "administrator" not in without_bootstrap
        repeated = await install({**bootstrap, "password": "MustNotReplacePassword!2026"})
        assert repeated["createdDatabase"] is False
        assert repeated["administrator"]["created"] is False
        assert repeated["administrator"]["userId"] == str(admin_id)
        assert await admin.fetchval(
            "SELECT password_hash FROM identity.profiles WHERE id=$1", admin_id,
        ) == original_hash
        assert await admin.fetchval("SELECT count(*) FROM identity.profiles") == 3
        assert await admin.fetchval("SELECT count(*) FROM identity.tenants") == 2
        assert await admin.fetchval("SELECT count(*) FROM workflow.jobs") == 8
        assert await admin.fetchval(
            "SELECT weight_pct FROM market.fund_lookthrough WHERE id=$1", lookthrough_id,
        ) == Decimal("12.34")
        assert await _sentinel_snapshot(maintenance, sentinel) == sentinel_before
    finally:
        if pool is not None:
            await pool.close()
        for connection in (admin, sentinel, maintenance):
            if connection is not None:
                await connection.close()
