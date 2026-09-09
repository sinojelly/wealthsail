"""Migration runner safety contract tests."""

import inspect
from pathlib import Path

import scripts.migrate as migration_runner
from scripts.migrate import (
    _readback,
    _watchlist_policy_has_required_ownership,
    discover_migrations,
    is_loopback_database,
    load_migrations,
    safe_target_name,
)


def test_production_migration_discovery_never_includes_dev_seeds() -> None:
    root = Path(__file__).resolve().parents[2]
    migrations = discover_migrations(root / "migrations")

    assert migrations
    assert all(path.parent.name == "migrations" for path in migrations)
    assert all("seed" not in path.name.lower() for path in migrations)
    assert any(path.name.startswith("013_") for path in migrations)


def test_production_migrations_have_one_file_per_version() -> None:
    """The deploy runner must be able to load the complete forward-only chain."""
    root = Path(__file__).resolve().parents[2]

    migrations = load_migrations(root / "migrations")

    versions = [migration.version for migration in migrations]
    assert len(versions) == len(set(versions))
    assert versions[-1] == 60
    assert migrations[-1].path.name == "060_pre_market_holding_trends.sql"


def test_057_extends_corporate_actions_without_widening_runtime_writes() -> None:
    """Manual ingestion needs dated, revisioned facts without runtime writes."""
    root = Path(__file__).resolve().parents[2]
    path = root / "migrations" / "057_corporate_actions_ingestion.sql"

    assert path.exists()
    migration = path.read_text(encoding="utf-8")
    for contract in (
        "ADD COLUMN IF NOT EXISTS action_date DATE",
        "ADD COLUMN IF NOT EXISTS summary TEXT",
        "ADD COLUMN IF NOT EXISTS source_event_id TEXT",
        "ADD COLUMN IF NOT EXISTS source_ref TEXT",
        "EX_RIGHTS",
        "RIGHTS_ISSUE",
        "OTHER",
        "UNIQUE (source_event_id, revision)",
        "idx_corporate_actions_date_instrument",
        "MANUAL_CORPORATE_ACTIONS",
        "GRANT SELECT ON market.corporate_actions TO wealthsail_api",
    ):
        assert contract in migration
    assert "GRANT INSERT ON market.corporate_actions" not in migration
    assert "GRANT UPDATE ON market.corporate_actions" not in migration
    assert "GRANT DELETE ON market.corporate_actions" not in migration


def test_readback_requires_corporate_action_ingestion_contract() -> None:
    source = inspect.getsource(_readback)

    for contract in (
        "corporate action ingestion contract is missing or unsafe",
        "action_date",
        "summary",
        "source_event_id",
        "source_ref",
        "corporate_actions_source_event_id_revision_key",
        "MANUAL_CORPORATE_ACTIONS",
    ):
        assert contract in source


def test_056_grants_api_read_access_to_outbox_delivery_receipts() -> None:
    """Manual regenerate reads back the operator's outbox id (f9dbf82 path).

    wealthsail_api must get column-level SELECT on workflow.outbox — without it
    POST /v1/debug/reports/trigger/{session} fails with permission denied. The
    grant must stay column-level and must NOT expose the payload content column.
    """
    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations" / "056_api_outbox_read_grants.sql").read_text(
        encoding="utf-8"
    )

    assert "GRANT SELECT (" in sql
    assert ") ON workflow.outbox TO wealthsail_api;" in sql
    assert "channel_binding_id" in sql  # JOIN column for the receipt lookup
    assert "payload" not in sql.split("GRANT SELECT")[1]  # content stays hidden

    # The deploy runner must verify this boundary at migration time.
    readback = inspect.getsource(_readback)
    assert '"api_outbox_select"' in readback
    # Source stores the check as two adjacent string literals across lines.
    assert "has_any_column_privilege('wealthsail_api'," in readback
    assert "'workflow.outbox','SELECT')" in readback


def test_readback_requires_secure_report_subscription_preferences() -> None:
    source = inspect.getsource(_readback)

    assert "report subscription preference boundary is missing or unsafe" in source
    assert '("wealthsail_api", "authenticated", "public")' in source
    assert 'for privilege in ("SELECT", "INSERT", "UPDATE")' in source
    assert 'subscription_privileges[("wealthsail_api", "INSERT")]' in source
    for contract in (
        "pre_market_report_enabled",
        "intraday_report_enabled",
        "fund_1430_report_enabled",
        "post_market_report_enabled",
        "wealthsail_api",
        "authenticated",
        "public",
    ):
        assert contract in source


def test_readback_checks_watchlist_shape_rls_and_privileges() -> None:
    source = inspect.getsource(_readback)
    assert "watchlist ownership boundary is missing or unsafe" in source
    assert "identity.watchlist" in source
    assert '("SELECT", "INSERT", "DELETE", "UPDATE")' in source


def test_checksum_drift_reconciliation_only_rewrites_the_ledger() -> None:
    """--reconcile-drift must update the recorded checksum without re-running SQL.

    Contract: drift on a version NOT passed to --reconcile-drift still raises;
    the reconciled version is skipped after the ledger row is updated, so the
    run continues into later migrations that restore the corrected data.
    """
    source = inspect.getsource(migration_runner.migrate)

    assert "checksum drift at migration" in source
    assert "reconcile_drift == migration.version" in source
    assert "RECONCILE" in source
    # Ledger-only update: never re-execute the migration SQL on reconciliation.
    assert "UPDATE public.wealthsail_schema_migrations" in source
    assert "SET filename = $2, checksum_sha256 = $3" in source
    assert "(ledger only, SQL not re-executed)" in source


def test_reconcile_drift_flag_is_opt_in_single_version() -> None:
    source = inspect.getsource(migration_runner.parse_args)

    assert '"--reconcile-drift"' in source
    assert "type=int" in source
    assert "WITHOUT " in source  # help text must warn SQL is not re-executed


def test_watchlist_policy_readback_rejects_non_ownership_predicates() -> None:
    assert _watchlist_policy_has_required_ownership(
        "(user_id = auth.uid())", ("authenticated",)
    )
    assert _watchlist_policy_has_required_ownership(
        "(user_id = (NULLIF(current_setting('app.user_id'::text, true), ''::text))::uuid)",
        ("wealthsail_api",),
    )
    assert not _watchlist_policy_has_required_ownership(
        "(auth.uid() IS NOT NULL)", ("authenticated",)
    )
    assert not _watchlist_policy_has_required_ownership(
        "(current_setting('app.user_id'::text, true) IS NOT NULL)",
        ("wealthsail_api",),
    )


def test_readback_requires_split_pre_market_boundary() -> None:
    """Deploy readback must reject schema, cache, grant, or audience-policy drift."""
    source = inspect.getsource(_readback)

    for contract in (
        "split pre-market report boundary is missing or unsafe",
        "shared pre-market brief cache boundary is missing or unsafe",
        "reports_report_variant_check",
        "reports_variant_audience_check",
        "fk_reports_audience_profile",
        "fk_reports_tenant_audience",
        "uq_reports_variant_audience_version",
        "protect_shared_pre_market_briefs_trigger",
        "reporting_runtime_acl",
        "_acl_matches_allowlist",
        "reporting_relation_owners",
        "_reporting_relation_owners_are_safe",
        "_shared_pre_market_trigger_is_canonical",
        "_shared_pre_market_json_constraints_are_canonical",
        "_report_policy_has_required_audience",
    ):
        assert contract in source

    assert migration_runner._report_policy_has_required_audience(
        "reports",
        "current_user = 'authenticated' AND "
        "identity.is_tenant_member(tenant_id) AND "
        "(audience_user_id IS NULL OR audience_user_id = auth.uid())",
        ("authenticated",),
    )


def test_042_grants_only_runtime_read_access_for_pre_market_reference_data() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "042_pre_market_reference_read_grants.sql"
    ).read_text(encoding="utf-8")

    assert "GRANT SELECT ON market.corporate_actions" in migration
    assert "GRANT SELECT ON reference.instrument_cutoff_rules" in migration
    assert "TO wealthsail_api;" in migration
    assert (
        "GRANT SELECT ON market.corporate_actions TO wealthsail_worker" not in migration
    )
    assert (
        "GRANT SELECT ON reference.instrument_cutoff_rules TO wealthsail_worker"
        not in migration
    )
    assert (
        "REVOKE ALL ON market.corporate_actions FROM PUBLIC, authenticated" in migration
    )
    assert (
        "REVOKE ALL ON reference.instrument_cutoff_rules FROM PUBLIC, authenticated"
        in migration
    )
    assert "GRANT INSERT" not in migration
    assert "GRANT UPDATE" not in migration
    assert "GRANT DELETE" not in migration


def test_readback_requires_pre_market_reference_read_boundary() -> None:
    source = inspect.getsource(_readback)

    assert "pre-market reference read boundary is missing or unsafe" in source
    assert "market.corporate_actions" in source
    assert "reference.instrument_cutoff_rules" in source
    assert "reference.fund_profiles" in source
    assert "aclexplode" in source
    assert "has_table_privilege($1, $2, $3)" in source


def test_047_adds_reference_fund_profiles_with_least_privilege() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "047_reference_fund_profiles.sql").read_text(
        encoding="utf-8"
    )

    for contract in (
        "CREATE TABLE IF NOT EXISTS reference.fund_profiles",
        "purchase_status TEXT NOT NULL DEFAULT 'OPEN'",
        "CHECK (purchase_status IN ('OPEN', 'SUSPENDED'))",
        "redemption_status TEXT NOT NULL DEFAULT 'OPEN'",
        "effective_from DATE NOT NULL",
        "UNIQUE (instrument_id, effective_from)",
        "GRANT SELECT ON reference.fund_profiles TO wealthsail_api",
    ):
        assert contract in migration
    assert (
        "GRANT SELECT ON reference.fund_profiles TO wealthsail_worker" not in migration
    )
    assert (
        "REVOKE ALL ON reference.fund_profiles FROM PUBLIC, authenticated" in migration
    )
    assert "GRANT INSERT" not in migration
    assert "GRANT UPDATE" not in migration
    assert "GRANT DELETE" not in migration


def test_048_registers_watchlist_instrument_via_definer_without_table_writes() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "048_reference_watchlist_instrument_registration.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "CREATE OR REPLACE FUNCTION reference.ensure_watchlist_instrument",
        "SECURITY DEFINER",
        "SET search_path = reference, pg_catalog",
        "INSERT INTO reference.instruments",
        "INSERT INTO reference.instrument_aliases",
        "ON CONFLICT DO NOTHING",
        "GRANT EXECUTE ON FUNCTION reference.ensure_watchlist_instrument",
    ):
        assert contract in migration
    assert "GRANT EXECUTE ON FUNCTION reference.ensure_watchlist_instrument" in (
        migration
    )
    assert "GRANT INSERT ON reference.instruments" not in migration
    assert "GRANT INSERT ON reference.instrument_aliases" not in migration
    assert "GRANT EXECUTE" in migration and "TO wealthsail_api" in migration


def test_049_scopes_registration_reuse_by_market() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "049_watchlist_instrument_registration_disambiguation.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "CREATE OR REPLACE FUNCTION reference.ensure_watchlist_instrument",
        "AND alias.market = p_market",
        "SECURITY DEFINER",
        "GRANT EXECUTE ON FUNCTION reference.ensure_watchlist_instrument",
    ):
        assert contract in migration
    assert "GRANT INSERT ON reference.instruments" not in migration
    assert "TO wealthsail_api" in migration


def test_050_enables_fund_1430_job_and_preference_idempotently() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "050_enable_fund_1430_delivery.sql").read_text(
        encoding="utf-8"
    )

    for contract in (
        "INSERT INTO workflow.jobs",
        "'FUND_1430'",
        "NOT EXISTS",
        "UPDATE identity.user_preferences",
        "fund_1430_report_enabled = TRUE",
        "COALESCE(preferences.pre_market_report_enabled, FALSE)",
        "channel_type = 'wecom'",
        "binding.status = 'ACTIVE'",
    ):
        assert contract in migration
    # 迁移不得粗暴打开所有用户偏好：必须保留「至少开启一种报告 + 有企微绑定」前提。
    assert "WHERE" in migration
    assert "membership.status = 'ACTIVE'" in migration


def test_051_creates_fund_lookthrough_with_lookup_and_privilege_guard() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "051_fund_lookthrough.sql").read_text(
        encoding="utf-8"
    )

    for contract in (
        "CREATE TABLE IF NOT EXISTS market.fund_lookthrough",
        "fund_instrument_id UUID NOT NULL REFERENCES reference.instruments",
        "holding_instrument_id UUID NOT NULL REFERENCES reference.instruments",
        "weight_pct NUMERIC(5, 2) NOT NULL CHECK",
        "report_period DATE NOT NULL",
        "as_of TIMESTAMPTZ",
        "UNIQUE (fund_instrument_id, holding_instrument_id, report_period)",
        "idx_fund_lookthrough_fund_asof",
        "REVOKE ALL ON market.fund_lookthrough",
        "GRANT SELECT ON market.fund_lookthrough",
    ):
        assert contract in migration
    # worker 不写表（与 fund_profiles / cutoff_rules 边界一致）。
    assert "wealthsail_worker" in migration
    # schema 放在 market（与其他市场快照同处，便于 RLS 与备份策略一致）。
    assert "market.fund_lookthrough" in migration


def test_052_registers_otc_funds_idempotently_with_ticker_alias() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "052_register_otc_fund_instruments.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "INSERT INTO reference.instruments",
        "'OFF_EXCHANGE_FUND'",
        "'FUND_CN'",
        "001632 天弘中证食品饮料ETF联接C",
        "005275 中欧创新成长灵活配置混合A",
        "NOT EXISTS",
        "INSERT INTO reference.instrument_aliases",
        "alias_type = 'TICKER'",
        "ON CONFLICT DO NOTHING",
    ):
        assert contract in migration
    # 关键：`requested` CTE 只作用于单条语句。两条 INSERT 必须放在同一个
    # WITH 链里（否则第二条 FROM requested 会报 relation does not exist）。
    assert "(SELECT count(*) FROM inserted) AS inserted_count" in migration
    assert "(SELECT count(*) FROM aliased) AS alias_count" in migration
    # 迁移必须完整包含用户要求的 12 只基金代码。
    for code in (
        "001632",
        "012733",
        "011103",
        "000689",
        "008552",
        "161726",
        "070019",
        "002593",
        "008295",
        "004476",
        "001705",
        "005275",
    ):
        assert code in migration
    # 场外基金必须用 OFF_EXCHANGE_FUND + FUND_CN（约束内合法值），
    # 而非用户建议的不存在枚举 'FUND'。
    assert "'FUND'" not in migration.replace("OFF_EXCHANGE_FUND", "").replace(
        "'FUND_CN'", ""
    )


def test_053_seeds_index_funds_holdings_with_approximated_weights() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "053_index_funds_initial_holdings.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "INSERT INTO market.fund_lookthrough",
        "ON CONFLICT (fund_instrument_id, holding_instrument_id, report_period)",
        "DO UPDATE SET",
        "warning",
        "基于公开指数机制的估值近似",
        "date '2026-06-30'",
    ):
        assert contract in migration
    # 051 建表缺 metadata 列，053 必须先 ALTER 补齐（否则 INSERT 报列不存在）。
    assert (
        "ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
        in migration
    )
    # 五只指数 / ETF 联接基金均需覆盖
    for fund in ("161725", "161726", "001632", "011103", "012733"):
        assert fund in migration


def test_054_registers_missing_holdings_before_seeding_lookthrough() -> None:
    """054 必须先注册缺失持仓股票/基金，再回填 lookthrough（修复 053 静默失配）。

    且「注册」与「回填」必须是**多条顺序语句**：同一条语句内的多个
    data-modifying CTE 并发执行、互相看不到写入，写在一条语句里会再次
    插 0 行却显示 applied。
    """
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "054_backfill_index_fund_holdings.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        # 持仓股票注册 + alias 补全
        "'CN_STOCK'",
        "INSERT INTO reference.instrument_aliases",
        "ON CONFLICT DO NOTHING",
        # 基金注册 + alias 兜底（052 漏建的补回来）
        "'OFF_EXCHANGE_FUND'",
        "'FUND_CN'",
        # lookthrough 回填（与 053 幂等兼容）
        "INSERT INTO market.fund_lookthrough",
        "ON CONFLICT (fund_instrument_id, holding_instrument_id, report_period)",
        "DO NOTHING",
        # 警告标记保留
        "基于公开指数机制的估值近似",
        # 约束说明留档
        "同一条语句内",
    ):
        assert contract in migration
    # 五只指数 / ETF 联接基金均需覆盖
    for fund in ("161725", "161726", "001632", "011103", "012733"):
        assert fund in migration
    # 22 只持仓股票均需覆盖
    for holding in (
        "600519",
        "000858",
        "000568",
        "600809",
        "000596",
        "300760",
        "603259",
        "600276",
        "300015",
        "300122",
        "603288",
        "600887",
        "601012",
        "600438",
        "002129",
        "300274",
        "002459",
        "002230",
        "002415",
        "603019",
        "688111",
        "000938",
    ):
        assert holding in migration
    # 顺序约束：注册（instruments）必须出现在回填（fund_lookthrough）之前，
    # 且两者分属不同语句（用分号分隔），否则回填读不到新注册的行。
    register_at = migration.index("INSERT INTO reference.instruments")
    seed_at = migration.index("INSERT INTO market.fund_lookthrough")
    assert register_at < seed_at
    assert migration.count("INSERT INTO market.fund_lookthrough") == 1
    assert migration[register_at:seed_at].count(";") >= 1


def test_041_adds_daily_shared_market_brief_cache_with_least_privilege() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "041_shared_market_briefs.sql").read_text(
        encoding="utf-8"
    )

    for contract in (
        "CREATE TABLE reporting.shared_market_briefs",
        "trade_date DATE NOT NULL UNIQUE",
        "source_context JSONB NOT NULL",
        "generation_status TEXT NOT NULL",
        "GRANT SELECT, INSERT ON reporting.shared_market_briefs TO wealthsail_api, wealthsail_worker",
        "REVOKE ALL ON reporting.shared_market_briefs FROM PUBLIC, authenticated",
    ):
        assert contract in migration
    assert "GRANT UPDATE" not in migration
    assert "GRANT DELETE" not in migration


def test_readback_requires_shared_market_brief_cache_boundary() -> None:
    source = inspect.getsource(_readback)

    assert "shared market brief cache boundary is missing or unsafe" in source
    assert "shared_market_briefs" in source


def test_040_rejects_duplicate_report_sessions_before_adding_uniqueness() -> None:
    root = Path(__file__).resolve().parents[2]
    migration_path = root / "migrations" / "040_report_job_session_uniqueness.sql"

    assert migration_path.exists()
    migration = migration_path.read_text(encoding="utf-8")
    assert "GROUP BY tenant_id, report_session" in migration
    assert "HAVING count(*) > 1" in migration
    assert "duplicate workflow.jobs report sessions" in migration
    assert "ADD CONSTRAINT uq_jobs_tenant_report_session" in migration
    assert "UNIQUE (tenant_id, report_session)" in migration


def test_readback_requires_report_job_session_uniqueness() -> None:
    source = inspect.getsource(_readback)

    assert "report job session uniqueness constraint is missing" in source
    assert "uq_jobs_tenant_report_session" in source


def test_039_adds_append_only_account_performance_baselines() -> None:
    root = Path(__file__).resolve().parents[2]
    migration_path = root / "migrations" / "039_account_performance_baselines.sql"

    assert migration_path.exists()
    migration = migration_path.read_text(encoding="utf-8")
    for contract in (
        "CREATE TABLE ledger.account_performance_baselines",
        "baseline_total_asset NUMERIC(28, 8)",
        "initial_cumulative_pnl NUMERIC(28, 8)",
        "UNIQUE (tenant_id, account_id, version)",
        "UNIQUE (tenant_id, idempotency_key)",
        "performance baselines are append-only",
        "CREATE POLICY performance_baselines_api_select",
        "CREATE POLICY performance_baselines_api_insert",
        "created_by = auth.uid()",
        "CREATE FUNCTION ledger.initialize_account_performance_baseline",
        "AFTER INSERT OR UPDATE OF total_nav",
        "SECURITY DEFINER",
        "AUTO_MIGRATION",
        "idx_ledger_events_performance_cash_flow",
        "idx_ledger_events_confirmed_reversal",
        "pg_catalog.sha256",
        "pg_catalog.convert_to",
    ):
        assert contract in migration
    assert "public.digest" not in migration
    assert "GRANT UPDATE ON ledger.account_performance_baselines" not in migration
    assert "GRANT DELETE ON ledger.account_performance_baselines" not in migration
    assert "TO authenticated" not in migration


def test_readback_checks_performance_baseline_security_boundary() -> None:
    source = inspect.getsource(_readback)

    for contract in (
        "account performance baseline boundary is missing or unsafe",
        "account_performance_baselines",
        "initialize_account_performance_baseline_trigger",
        "api_can_read_performance_baselines",
        "api_can_insert_performance_baselines",
        "api_cannot_update_performance_baselines",
        "browser_cannot_read_performance_baselines",
        "worker_cannot_insert_performance_baselines",
    ):
        assert contract in source


def test_036_aligns_existing_fund_nav_sources_without_widening_the_enum() -> None:
    root = Path(__file__).resolve().parents[2]
    migration_path = root / "migrations" / "036_fund_nav_source_type_alignment.sql"

    assert migration_path.exists()
    migration = migration_path.read_text(encoding="utf-8")
    assert "UPDATE reference.data_sources" in migration
    assert "TiantianFund-EastMoney" in migration
    assert "WealthSail-Fund-Estimator" in migration
    assert "source_type = 'FUND_NAV'" in migration
    assert "DROP CONSTRAINT data_sources_source_type_check" not in migration


def test_migration_readback_rejects_fund_nav_source_type_drift() -> None:
    source = inspect.getsource(_readback)

    assert "fund NAV data source type alignment is missing" in source
    assert "TiantianFund-EastMoney" in source
    assert "WealthSail-Fund-Estimator" in source


def test_034_migration_limits_tenant_rename_to_current_owner() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "034_tenant_name_management.sql").read_text(
        encoding="utf-8"
    )

    assert "FOR UPDATE TO wealthsail_api" in migration
    assert "identity.has_tenant_role(id, ARRAY['OWNER'])" in migration
    assert "GRANT UPDATE (name, updated_at)" in migration


def test_migration_readback_verifies_tenant_rename_least_privilege() -> None:
    root = Path(__file__).resolve().parents[2]
    runner = (root / "scripts" / "migrate.py").read_text(encoding="utf-8")

    assert "tenant name update policy is missing or not owner-scoped" in runner
    assert "tenant name column privileges are missing or too broad" in runner
    assert "'identity.tenants','status','UPDATE'" in runner
    assert "'authenticated','identity.tenants','name','UPDATE'" in runner


def test_035_removes_stale_api_job_insert_capability() -> None:
    """Personal-tenant schedules must only be provisioned by the protected RPC."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "035_personal_tenant_job_privilege_hardening.sql"
    ).read_text(encoding="utf-8")

    assert "REVOKE INSERT ON workflow.jobs FROM wealthsail_api" in migration
    assert "DROP POLICY IF EXISTS domain_api_insert ON workflow.jobs" in migration


def test_readback_covers_identity_and_trade_hardening() -> None:
    source = inspect.getsource(_readback)

    for contract in (
        "auth_version",
        "password_reset_tokens",
        "register_with_tenant",
        "create_tenant",
        "fk_trade_fills_confirmed_by_tenant",
        "uq_trade_fills_ledger_event",
        "confirmed_by = auth.uid()",
        "wecom_callback_claims",
        "claim_wecom_callback",
        "market_hot_snapshots",
    ):
        assert contract in source


def test_025_migration_contains_forward_fix_contracts() -> None:
    """The forward fix must close every capability that readback promises."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "025_identity_session_and_trade_integrity_hardening.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "DROP FUNCTION IF EXISTS identity.register_with_tenant",
        "DROP FUNCTION IF EXISTS identity.create_tenant",
        "auth_version = auth_version + 1",
        "fk_trade_fills_confirmed_by_tenant",
        "FOREIGN KEY (tenant_id, confirmed_by)",
        "uq_trade_fills_ledger_event",
        "confirmed_by = auth.uid()",
    ):
        assert contract in migration


def test_027_migration_enforces_worker_owned_market_hot_projection() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "027_market_hot_read_model.sql").read_text(
        encoding="utf-8"
    )

    for contract in (
        "CREATE TABLE market.market_hot_snapshots",
        "jsonb_typeof(items) = 'array'",
        "GRANT SELECT ON market.market_hot_snapshots TO wealthsail_api",
        "GRANT SELECT, INSERT ON market.market_hot_snapshots TO wealthsail_worker",
        "REVOKE ALL ON market.market_hot_snapshots FROM PUBLIC, authenticated",
    ):
        assert contract in migration


def test_028_migration_allows_only_draft_import_item_updates() -> None:
    """Confirmation row locks need UPDATE RLS without widening column grants."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "028_portfolio_import_item_update_policy.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "DROP POLICY IF EXISTS domain_api_update ON ledger.portfolio_import_items",
        "CREATE POLICY domain_api_update",
        "ON ledger.portfolio_import_items",
        "FOR UPDATE TO wealthsail_api",
        "identity.has_tenant_role",
        "batch.status = 'DRAFT'",
    ):
        assert contract in migration
    assert "GRANT UPDATE ON ledger.portfolio_import_items" not in migration


def test_029_migration_persists_inference_method_and_confidence() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "029_holding_start_inference_metadata.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "holding_start_method",
        "holding_start_confidence",
        "holding_start_reasons",
        "PORTFOLIO_ACTIVITY_ESTIMATE",
        "TRANSACTION_RECONCILED",
    ):
        assert contract in migration


def test_030_migration_limits_api_job_reads_to_tenant_scoped_status_columns() -> None:
    """Batch detail needs job status without exposing worker payloads broadly."""
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "030_tiantian_job_status_read_policy.sql"
    ).read_text(encoding="utf-8")

    for contract in (
        "CREATE POLICY job_runs_api_select",
        "FOR SELECT TO wealthsail_api",
        "identity.is_tenant_member(tenant_id)",
        "GRANT SELECT (id, tenant_id, status, job_type, payload, available_at)",
    ):
        assert contract in migration
    assert "GRANT SELECT ON workflow.job_runs TO wealthsail_api" not in migration


def test_037_adds_append_only_holding_start_decisions_with_least_privilege() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "037_holding_start_decisions.sql").read_text(
        encoding="utf-8"
    )

    for contract in (
        "CREATE TABLE ledger.position_holding_start_decisions",
        "request_hash CHAR(64)",
        "UNIQUE (tenant_id, idempotency_key)",
        "REFERENCES identity.tenant_members(tenant_id, user_id)",
        "CREATE POLICY holding_start_decisions_api_select",
        "CREATE POLICY holding_start_decisions_api_insert",
        "identity.has_tenant_role",
        "GRANT SELECT, INSERT ON ledger.position_holding_start_decisions TO wealthsail_api",
        "GRANT SELECT ON ledger.position_holding_start_decisions TO wealthsail_worker",
        "holding-start decisions are append-only",
    ):
        assert contract in migration
    assert "GRANT UPDATE" not in migration
    assert "GRANT DELETE" not in migration


def test_038_grants_worker_only_the_projection_writes_used_by_revaluation() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "migrations" / "038_worker_projection_and_holding_confirmation.sql"
    ).read_text(encoding="utf-8")

    assert "GRANT INSERT, DELETE ON ledger.position_snapshots" in migration
    assert "GRANT UPDATE (" in migration
    assert (
        "market_price, market_value, unrealized_pnl, data_quality_status" in migration
    )
    assert "GRANT INSERT ON ledger.account_nav_snapshots" in migration
    assert "GRANT SELECT ON identity.accounts TO wealthsail_worker" in migration
    assert "recalculation_required BOOLEAN" in migration


def test_readback_checks_038_capabilities_and_least_privilege() -> None:
    source = inspect.getsource(_readback)

    for contract in (
        "worker valuation projection or holding confirmation boundary is missing",
        "worker_can_read_accounts",
        "worker_can_insert_positions",
        "worker_can_delete_positions",
        "worker_can_update_position_prices",
        "worker_cannot_update_position_facts",
        "worker_can_insert_account_nav",
        "worker_can_update_account_nav",
        "worker_cannot_reassign_account_nav",
        "confirmation_mode_exists",
        "previous_start_exists",
    ):
        assert contract in source


def test_readback_checks_post_import_holding_start_decision_boundary() -> None:
    source = inspect.getsource(_readback)

    assert "post-import holding-start decision boundary is missing or unsafe" in source
    for contract in (
        "position_holding_start_decisions",
        "protect_holding_start_decisions_trigger",
        "forced_rls_exists",
        "api_can_insert_holding_start_decision",
        "api_can_delete_holding_start_decision",
        "browser_can_read_holding_start_decision",
        "worker_can_read_holding_start_decision",
        "api_select_policy_exists",
        "api_insert_policy_exists",
    ):
        assert contract in source


def test_readback_rejects_a_missing_import_item_update_policy() -> None:
    source = inspect.getsource(_readback)

    assert "portfolio import item UPDATE policy is missing or too broad" in source


def test_readback_checks_tenant_scoped_job_status_access() -> None:
    source = inspect.getsource(_readback)

    assert "Tiantian job status SELECT policy is missing or too broad" in source
    assert "Tiantian job status column privileges missing" in source


def test_readback_checks_tenant_user_account_ownership_boundaries() -> None:
    """Deploy success requires the new identity model and no browser write path."""
    source = inspect.getsource(_readback)

    for contract in (
        "platform_roles",
        "user_preferences",
        "account_members",
        "ownership_scope",
        "CREATE_PERSONAL_TENANT",
        "identity ownership tables expose browser writes",
        "platform personal-tenant invitation privilege boundary is invalid",
        "api_can_probe_platform_roles",
    ):
        assert contract in source


def test_033_initializes_four_jobs_only_for_a_new_personal_tenant() -> None:
    """Joining an existing tenant must not create a second schedule set."""
    root = Path(__file__).resolve().parents[2]
    migration = (root / "migrations" / "033_personal_tenant_report_jobs.sql").read_text(
        encoding="utf-8"
    )

    personal_branch = migration.split(
        "IF v_inv.purpose = 'CREATE_PERSONAL_TENANT' THEN", 1
    )[1].split("ELSE", 1)[0]
    assert "INSERT INTO workflow.jobs" in personal_branch
    for session in ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"):
        assert personal_branch.count(f"'{session}'") == 1
    assert "GRANT INSERT ON workflow.jobs TO wealthsail_api" not in migration
    assert "GRANT INSERT ON workflow.jobs TO authenticated" not in migration


def test_readback_requires_personal_tenant_schedule_initialization() -> None:
    source = inspect.getsource(_readback)

    assert "personal tenant report schedule initialization is missing" in source
    assert "api_can_insert_jobs=" in source
    assert "function_owner_can_insert" in source
    assert "missing_definition_tokens=" in source


def test_safe_target_name_never_contains_password_or_query_secret() -> None:
    target = safe_target_name(
        "postgresql://deploy:super-secret@db.example:5432/wealthsail?sslmode=require"
    )

    assert target == "db.example:5432/wealthsail"
    assert "secret" not in target
    assert "deploy" not in target


def test_role_provisioning_grants_connect_to_the_selected_database() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts" / "provision_supabase_roles.sql").read_text(
        encoding="utf-8"
    )

    assert "current_database()" in source
    assert "GRANT CONNECT ON DATABASE postgres" not in source


def test_loopback_detection_cannot_be_bypassed_with_an_explicit_port() -> None:
    assert is_loopback_database("postgresql://postgres@127.0.0.1/wealthsail")
    assert is_loopback_database("postgresql://postgres@127.0.0.1:55432/wealthsail")
    assert is_loopback_database("postgresql://postgres@localhost:5432/wealthsail")
    assert is_loopback_database("postgresql://postgres@[::1]:5432/wealthsail")
    assert not is_loopback_database("postgresql://deploy@db.example:5432/wealthsail")
