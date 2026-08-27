-- =============================================================================
-- WealthSail Migration 021: 天天基金来源导入、持仓周期与净值区间指标
--
-- 核心语义：当前持仓快照是外部账户基线；历史流水是来源事实，不伪造 BUY/SELL。
-- 预览仍复用 DRAFT import batch，确认后按基金逐项 REVERSAL + ADJUSTMENT。
-- =============================================================================

-- The account component is part of every binding relationship, preventing a
-- valid binding from being attached to another valid account in the same tenant.
ALTER TABLE ledger.portfolio_import_batches
    ADD CONSTRAINT uq_import_batches_tenant_id_account
        UNIQUE (tenant_id, id, account_id);

-- 1. 匿名来源账号与 WealthSail 专用投资账户的租户内绑定。
CREATE TABLE ledger.portfolio_source_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL,
    provider_code TEXT NOT NULL CHECK (provider_code IN ('TIANTIAN_FUND')),
    external_account_key UUID NOT NULL,
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'ACTIVE', 'DISABLED')),
    created_by_batch_id UUID NOT NULL,
    first_imported_at TIMESTAMPTZ,
    last_imported_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, id, account_id),
    UNIQUE (tenant_id, provider_code, external_account_key),
    CONSTRAINT fk_source_bindings_tenant_account
        FOREIGN KEY (tenant_id, account_id)
        REFERENCES identity.accounts(tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_source_bindings_tenant_created_batch
        FOREIGN KEY (tenant_id, created_by_batch_id, account_id)
        REFERENCES ledger.portfolio_import_batches(tenant_id, id, account_id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    CHECK (first_imported_at IS NULL OR last_imported_at IS NULL
           OR last_imported_at >= first_imported_at),
    CHECK ((status = 'PENDING' AND first_imported_at IS NULL AND last_imported_at IS NULL)
           OR (status IN ('ACTIVE', 'DISABLED')
               AND first_imported_at IS NOT NULL AND last_imported_at IS NOT NULL))
);

CREATE UNIQUE INDEX uq_source_bindings_active_account_provider
    ON ledger.portfolio_source_bindings (tenant_id, account_id, provider_code)
    WHERE status = 'ACTIVE';
CREATE INDEX idx_source_bindings_account
    ON ledger.portfolio_source_bindings (tenant_id, account_id, status);

-- 2. 扩展导入批次；保留 MANUAL/CSV/NATURAL_LANGUAGE 兼容路径。
ALTER TABLE ledger.portfolio_import_batches
    DROP CONSTRAINT portfolio_import_batches_source_type_check;
ALTER TABLE ledger.portfolio_import_batches
    ADD CONSTRAINT portfolio_import_batches_source_type_check CHECK (
        source_type IN ('MANUAL', 'CSV', 'NATURAL_LANGUAGE', 'TIANTIAN_FUND_ZIP')
    ),
    ADD COLUMN source_binding_id UUID,
    ADD COLUMN package_schema_version TEXT,
    ADD COLUMN package_content_hash CHAR(64),
    ADD COLUMN source_as_of TIMESTAMPTZ,
    ADD COLUMN parser_version TEXT,
    ADD COLUMN preview_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'VALID',
    ADD COLUMN validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN target_account_state_hash CHAR(64),
    ADD COLUMN terminal_command_type TEXT,
    ADD COLUMN terminal_idempotency_key TEXT,
    ADD COLUMN terminal_content_hash CHAR(64),
    ADD CONSTRAINT portfolio_import_batches_validation_status_check
        CHECK (validation_status IN ('VALID', 'INVALID', 'NEEDS_REVIEW')),
    ADD CONSTRAINT portfolio_import_batches_validation_errors_check
        CHECK (jsonb_typeof(validation_errors) = 'array'),
    ADD CONSTRAINT portfolio_import_batches_preview_summary_check
        CHECK (jsonb_typeof(preview_summary) = 'object'),
    ADD CONSTRAINT portfolio_import_batches_package_hash_check
        CHECK (package_content_hash IS NULL OR package_content_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT portfolio_import_batches_account_hash_check
        CHECK (target_account_state_hash IS NULL OR target_account_state_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT portfolio_import_batches_terminal_command_check CHECK (
        source_type <> 'TIANTIAN_FUND_ZIP'
        OR (status = 'DRAFT'
            AND terminal_command_type IS NULL
            AND terminal_idempotency_key IS NULL
            AND terminal_content_hash IS NULL)
        OR (status = 'CONFIRMED'
            AND terminal_command_type = 'CONFIRM'
            AND terminal_idempotency_key IS NOT NULL
            AND terminal_content_hash ~ '^[0-9a-f]{64}$')
        OR (status = 'CANCELLED'
            AND terminal_command_type = 'CANCEL'
            AND terminal_idempotency_key IS NOT NULL
            AND terminal_content_hash ~ '^[0-9a-f]{64}$')
    ),
    ADD CONSTRAINT portfolio_import_batches_terminal_command_type_check CHECK (
        terminal_command_type IS NULL OR terminal_command_type IN ('CONFIRM', 'CANCEL')
    ),
    ADD CONSTRAINT portfolio_import_batches_tiantian_fields_check CHECK (
        source_type <> 'TIANTIAN_FUND_ZIP'
        OR (
            source_binding_id IS NOT NULL
            AND package_schema_version = 'wealthsail-tiantian-import/v2'
            AND package_content_hash IS NOT NULL
            AND source_as_of IS NOT NULL
            AND parser_version IS NOT NULL
            AND target_account_state_hash IS NOT NULL
        )
    ),
    ADD CONSTRAINT uq_import_batches_tenant_id_source_binding
        UNIQUE (tenant_id, id, source_binding_id),
    ADD CONSTRAINT fk_import_batches_tenant_source_binding
        FOREIGN KEY (tenant_id, source_binding_id, account_id)
        REFERENCES ledger.portfolio_source_bindings(tenant_id, id, account_id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;

-- New drafts start as NEEDS_REVIEW; rows upgraded from earlier migrations retain VALID.
ALTER TABLE ledger.portfolio_import_batches
    ALTER COLUMN validation_status SET DEFAULT 'NEEDS_REVIEW';
CREATE UNIQUE INDEX uq_import_batches_tiantian_package
    ON ledger.portfolio_import_batches (tenant_id, source_binding_id, package_content_hash)
    WHERE source_type = 'TIANTIAN_FUND_ZIP' AND status <> 'CANCELLED';
CREATE UNIQUE INDEX uq_import_batches_terminal_idempotency
    ON ledger.portfolio_import_batches
       (tenant_id, terminal_command_type, terminal_idempotency_key)
    WHERE source_type = 'TIANTIAN_FUND_ZIP'
      AND terminal_idempotency_key IS NOT NULL;

-- 3. A POSITION_SNAPSHOT is an absolute target state, including zero for CLOSED.
ALTER TABLE ledger.portfolio_import_items
    DROP CONSTRAINT portfolio_import_items_item_type_check,
    DROP CONSTRAINT portfolio_import_items_check;
ALTER TABLE ledger.portfolio_import_items
    ADD CONSTRAINT portfolio_import_items_item_type_check CHECK (
        item_type IN ('CASH_BALANCE', 'POSITION', 'POSITION_SNAPSHOT')
    ),
    ADD COLUMN external_record_id TEXT,
    ADD COLUMN source_symbol TEXT,
    ADD COLUMN source_market_value NUMERIC(28, 8),
    ADD COLUMN source_nav NUMERIC(28, 10),
    ADD COLUMN source_nav_date DATE,
    ADD COLUMN comparison_status TEXT,
    ADD COLUMN candidate_holding_started_on DATE,
    ADD COLUMN holding_start_status TEXT,
    ADD COLUMN user_holding_started_on DATE,
    ADD COLUMN source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD CONSTRAINT portfolio_import_items_comparison_status_check CHECK (
        comparison_status IS NULL
        OR comparison_status IN ('NEW', 'UNCHANGED', 'CHANGED', 'CLOSED', 'CONFLICT')
    ),
    ADD CONSTRAINT portfolio_import_items_holding_start_status_check CHECK (
        holding_start_status IS NULL
        OR holding_start_status IN (
            'RECONCILED', 'PARTIAL_HISTORY', 'MISMATCH',
            'INSUFFICIENT_DATA', 'USER_CONFIRMED'
        )
    ),
    ADD CONSTRAINT portfolio_import_items_source_payload_check
        CHECK (jsonb_typeof(source_payload) = 'object'),
    ADD CONSTRAINT portfolio_import_items_source_values_check CHECK (
        (source_market_value IS NULL OR source_market_value >= 0)
        AND (source_nav IS NULL OR source_nav > 0)
        AND ((source_nav IS NULL AND source_nav_date IS NULL)
             OR (source_nav IS NOT NULL AND source_nav_date IS NOT NULL))
    ),
    ADD CONSTRAINT portfolio_import_items_value_shape_check CHECK (
        (item_type = 'CASH_BALANCE'
            AND instrument_id IS NULL AND quantity IS NULL AND unit_cost IS NULL
            AND cash_amount IS NOT NULL AND cash_amount >= 0
            AND comparison_status IS NULL)
        OR
        (item_type = 'POSITION'
            AND instrument_id IS NOT NULL AND quantity IS NOT NULL AND quantity > 0
            AND unit_cost IS NOT NULL AND unit_cost >= 0 AND cash_amount IS NULL
            AND comparison_status IS NULL)
        OR
        (item_type = 'POSITION_SNAPSHOT'
            AND instrument_id IS NOT NULL AND quantity IS NOT NULL AND quantity >= 0
            AND unit_cost IS NOT NULL AND unit_cost >= 0 AND cash_amount IS NULL
            AND external_record_id IS NOT NULL AND length(btrim(external_record_id)) > 0
            AND source_symbol ~ '^[0-9]{6}$'
            AND comparison_status IS NOT NULL)
    );

CREATE UNIQUE INDEX uq_import_items_external_snapshot_record
    ON ledger.portfolio_import_items (tenant_id, batch_id, external_record_id)
    WHERE external_record_id IS NOT NULL;
CREATE INDEX idx_import_items_snapshot_comparison
    ON ledger.portfolio_import_items (tenant_id, batch_id, comparison_status, instrument_id)
    WHERE item_type = 'POSITION_SNAPSHOT';

-- 4. Draft-only normalized source records used by preview and review.
CREATE TABLE ledger.portfolio_import_source_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    batch_id UUID NOT NULL,
    record_type TEXT NOT NULL CHECK (record_type IN (
        'POSITION_SNAPSHOT', 'FUND_TRANSACTION', 'REVIEW_REQUIRED', 'EXCLUDED_SUMMARY'
    )),
    external_record_id TEXT NOT NULL CHECK (length(btrim(external_record_id)) > 0),
    instrument_id UUID REFERENCES reference.instruments(id) ON DELETE RESTRICT,
    occurred_at TIMESTAMPTZ,
    classification TEXT NOT NULL CHECK (length(btrim(classification)) > 0),
    normalized_payload JSONB NOT NULL CHECK (jsonb_typeof(normalized_payload) = 'object'),
    content_hash CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    validation_status TEXT NOT NULL CHECK (
        validation_status IN ('VALID', 'INVALID', 'NEEDS_REVIEW')
    ),
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(validation_errors) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, batch_id, record_type, external_record_id),
    CONSTRAINT fk_source_records_tenant_batch
        FOREIGN KEY (tenant_id, batch_id)
        REFERENCES ledger.portfolio_import_batches(tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_source_records_batch_classification
    ON ledger.portfolio_import_source_records
    (tenant_id, batch_id, validation_status, record_type, occurred_at);

-- 5. Confirmed external transaction facts. These never become fake BUY/SELL events.
CREATE TABLE ledger.portfolio_source_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    source_binding_id UUID NOT NULL,
    source_import_batch_id UUID NOT NULL,
    external_transaction_id TEXT NOT NULL
        CHECK (length(btrim(external_transaction_id)) > 0),
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE RESTRICT,
    product_code TEXT NOT NULL CHECK (product_code ~ '^[0-9]{6}$'),
    product_name TEXT NOT NULL CHECK (length(btrim(product_name)) > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    business_code TEXT NOT NULL DEFAULT '',
    business_type TEXT NOT NULL CHECK (business_type IN (
        'PURCHASE', 'REDEEM', 'CONVERSION_IN', 'CONVERSION_OUT',
        'DIVIDEND_CASH', 'DIVIDEND_REINVEST', 'FORCED_ADJUSTMENT',
        'NON_POSITION', 'UNKNOWN'
    )),
    application_value NUMERIC(28, 10),
    application_unit TEXT NOT NULL DEFAULT '',
    confirmation_value NUMERIC(28, 10),
    confirmation_unit TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL CHECK (length(btrim(classification)) > 0),
    source_payload JSONB NOT NULL CHECK (jsonb_typeof(source_payload) = 'object'),
    content_hash CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, source_binding_id, external_transaction_id),
    CONSTRAINT fk_source_transactions_tenant_binding
        FOREIGN KEY (tenant_id, source_binding_id)
        REFERENCES ledger.portfolio_source_bindings(tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_source_transactions_tenant_batch
        FOREIGN KEY (tenant_id, source_import_batch_id)
        REFERENCES ledger.portfolio_import_batches(tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_source_transactions_tenant_batch_binding
        FOREIGN KEY (tenant_id, source_import_batch_id, source_binding_id)
        REFERENCES ledger.portfolio_import_batches(tenant_id, id, source_binding_id)
        ON DELETE RESTRICT,
    CHECK (application_value IS NULL OR application_value >= 0),
    CHECK (confirmation_value IS NULL OR confirmation_value >= 0)
);
CREATE INDEX idx_source_transactions_instrument_time
    ON ledger.portfolio_source_transactions
    (tenant_id, source_binding_id, instrument_id, occurred_at, external_transaction_id);

-- 6. Daily materialized holding-period/range projection. Official NAV stays in
-- market.market_observations(field_code='NAV'); this table only stores derivation.
CREATE TABLE ledger.position_period_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL,
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id) ON DELETE RESTRICT,
    source_binding_id UUID NOT NULL,
    as_of DATE NOT NULL,
    holding_started_on DATE,
    holding_days INTEGER CHECK (holding_days IS NULL OR holding_days > 0),
    holding_start_method TEXT NOT NULL CHECK (holding_start_method IN (
        'TRANSACTION_RECONCILED', 'PARTIAL_HISTORY', 'MISMATCH',
        'INSUFFICIENT_DATA', 'USER_CONFIRMED'
    )),
    current_nav NUMERIC(28, 10),
    current_nav_date DATE,
    period_high_nav NUMERIC(28, 10),
    period_high_date DATE,
    period_low_nav NUMERIC(28, 10),
    period_low_date DATE,
    from_high_pct NUMERIC(12, 4),
    from_low_pct NUMERIC(12, 4),
    range_position_pct NUMERIC(12, 4),
    calculation_status TEXT NOT NULL CHECK (
        calculation_status IN ('READY', 'PENDING', 'PARTIAL', 'CONFLICT')
    ),
    quality_reasons TEXT[] NOT NULL DEFAULT '{}'::text[]
        CHECK (array_position(quality_reasons, NULL) IS NULL),
    calculation_version TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, account_id, instrument_id, as_of),
    CONSTRAINT fk_position_metrics_tenant_account
        FOREIGN KEY (tenant_id, account_id)
        REFERENCES identity.accounts(tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_position_metrics_tenant_binding
        FOREIGN KEY (tenant_id, source_binding_id, account_id)
        REFERENCES ledger.portfolio_source_bindings(tenant_id, id, account_id)
        ON DELETE CASCADE,
    CHECK ((holding_started_on IS NULL AND holding_days IS NULL)
           OR (holding_started_on IS NOT NULL AND holding_days = as_of - holding_started_on + 1)),
    CHECK ((current_nav IS NULL AND current_nav_date IS NULL)
           OR (current_nav > 0 AND current_nav_date IS NOT NULL)),
    CHECK ((period_high_nav IS NULL AND period_high_date IS NULL)
           OR (period_high_nav > 0 AND period_high_date IS NOT NULL)),
    CHECK ((period_low_nav IS NULL AND period_low_date IS NULL)
           OR (period_low_nav > 0 AND period_low_date IS NOT NULL)),
    CHECK (period_high_nav IS NULL OR period_low_nav IS NULL OR period_high_nav >= period_low_nav),
    CHECK (from_high_pct IS NULL OR from_high_pct <= 0),
    CHECK (from_low_pct IS NULL OR from_low_pct >= 0),
    CHECK (range_position_pct IS NULL OR range_position_pct BETWEEN 0 AND 100)
);
CREATE INDEX idx_position_period_metrics_latest
    ON ledger.position_period_metrics
    (tenant_id, account_id, instrument_id, as_of DESC);

-- 7. Persistent recoverable work; no process-local background tasks.
ALTER TABLE workflow.job_runs
    ADD COLUMN job_type TEXT,
    ADD COLUMN payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD CONSTRAINT job_runs_job_type_check CHECK (
        job_type IS NULL OR job_type IN (
            'FUND_NAV_BACKFILL', 'POSITION_PERIOD_METRICS_REBUILD'
        )
    ),
    ADD CONSTRAINT job_runs_payload_check CHECK (jsonb_typeof(payload) = 'object');
CREATE INDEX idx_job_runs_import_claimable
    ON workflow.job_runs (job_type, status, available_at, next_retry_at, lease_until)
    WHERE job_type IN ('FUND_NAV_BACKFILL', 'POSITION_PERIOD_METRICS_REBUILD')
      AND status IN ('PENDING', 'RUNNING', 'FAILED');

-- 8. Confirmation requires exactly the ledger effects implied by each item.
CREATE UNIQUE INDEX uq_ledger_events_import_item_reversal_effect
    ON ledger.ledger_events (tenant_id, import_batch_id, import_item_id)
    WHERE import_batch_id IS NOT NULL AND reverses_event_id IS NOT NULL;

CREATE OR REPLACE FUNCTION ledger.protect_portfolio_import_batch()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'confirmed/cancelled import batches are historical facts';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status <> 'DRAFT' THEN
        RAISE EXCEPTION 'terminal import batch status is immutable';
    END IF;
    IF NEW.status NOT IN ('DRAFT', 'CONFIRMED', 'CANCELLED') THEN
        RAISE EXCEPTION 'invalid import batch transition';
    END IF;
    IF NEW.status = 'CONFIRMED' THEN
        IF OLD.source_type = 'TIANTIAN_FUND_ZIP' AND OLD.validation_status <> 'VALID' THEN
            RAISE EXCEPTION 'a Tiantian import batch must be VALID before confirmation';
        END IF;
        IF OLD.source_type = 'TIANTIAN_FUND_ZIP' AND NOT EXISTS (
            SELECT 1
              FROM ledger.portfolio_source_bindings binding
             WHERE binding.tenant_id = OLD.tenant_id
               AND binding.id = OLD.source_binding_id
               AND binding.account_id = OLD.account_id
               AND binding.status = 'ACTIVE'
        ) THEN
            RAISE EXCEPTION 'confirmed Tiantian import requires its ACTIVE account binding';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM ledger.portfolio_import_items item
             WHERE item.tenant_id = OLD.tenant_id AND item.batch_id = OLD.id
        ) THEN
            RAISE EXCEPTION 'an import batch cannot be confirmed without items';
        END IF;
        IF EXISTS (
            SELECT 1 FROM ledger.portfolio_import_items item
             WHERE item.tenant_id = OLD.tenant_id AND item.batch_id = OLD.id
               AND (item.validation_status <> 'VALID'
                    OR item.comparison_status = 'CONFLICT')
        ) THEN
            RAISE EXCEPTION 'all import items must be valid and conflict-free';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM ledger.portfolio_import_items item
             WHERE item.tenant_id = OLD.tenant_id
               AND item.batch_id = OLD.id
               AND (
                    (item.item_type IN ('CASH_BALANCE', 'POSITION') AND NOT EXISTS (
                        SELECT 1 FROM ledger.ledger_events event
                         WHERE event.tenant_id = item.tenant_id
                           AND event.import_batch_id = item.batch_id
                           AND event.import_item_id = item.id
                           AND event.event_type = 'ADJUSTMENT'
                           AND event.status = 'CONFIRMED'
                           AND event.reverses_event_id IS NULL
                    ))
                    OR (item.item_type = 'POSITION_SNAPSHOT'
                        AND item.comparison_status IN ('NEW', 'CHANGED') AND NOT EXISTS (
                        SELECT 1 FROM ledger.ledger_events event
                         WHERE event.tenant_id = item.tenant_id
                           AND event.import_batch_id = item.batch_id
                           AND event.import_item_id = item.id
                           AND event.event_type = 'ADJUSTMENT'
                           AND event.status = 'CONFIRMED'
                           AND event.reverses_event_id IS NULL
                    ))
                    OR (item.item_type = 'POSITION_SNAPSHOT'
                        AND item.comparison_status IN ('CHANGED', 'CLOSED') AND NOT EXISTS (
                        SELECT 1 FROM ledger.ledger_events event
                         WHERE event.tenant_id = item.tenant_id
                           AND event.import_batch_id = item.batch_id
                           AND event.import_item_id = item.id
                           AND event.event_type = 'REVERSAL'
                           AND event.status = 'CONFIRMED'
                           AND event.reverses_event_id IS NOT NULL
                    ))
                    OR (item.item_type = 'POSITION_SNAPSHOT'
                        AND item.comparison_status = 'UNCHANGED' AND EXISTS (
                        SELECT 1 FROM ledger.ledger_events event
                         WHERE event.tenant_id = item.tenant_id
                           AND event.import_batch_id = item.batch_id
                           AND event.import_item_id = item.id
                    ))
                    OR (item.item_type = 'POSITION_SNAPSHOT'
                        AND item.comparison_status = 'CLOSED' AND EXISTS (
                        SELECT 1 FROM ledger.ledger_events event
                         WHERE event.tenant_id = item.tenant_id
                           AND event.import_batch_id = item.batch_id
                           AND event.import_item_id = item.id
                           AND event.reverses_event_id IS NULL
                    ))
               )
        ) THEN
            RAISE EXCEPTION 'each actionable import item requires its expected ledger effect';
        END IF;
    END IF;
    IF ROW(
        NEW.id, NEW.tenant_id, NEW.account_id, NEW.created_by, NEW.source_type,
        NEW.source_content_hash, NEW.idempotency_key, NEW.created_at,
        NEW.source_binding_id, NEW.package_schema_version, NEW.package_content_hash,
        NEW.source_as_of, NEW.parser_version, NEW.preview_summary,
        NEW.validation_status, NEW.validation_errors, NEW.target_account_state_hash
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.tenant_id, OLD.account_id, OLD.created_by, OLD.source_type,
        OLD.source_content_hash, OLD.idempotency_key, OLD.created_at,
        OLD.source_binding_id, OLD.package_schema_version, OLD.package_content_hash,
        OLD.source_as_of, OLD.parser_version, OLD.preview_summary,
        OLD.validation_status, OLD.validation_errors, OLD.target_account_state_hash
    ) THEN
        RAISE EXCEPTION 'import batch ownership/source/idempotency fields are immutable';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'import batch version must increment by one';
    END IF;
    RETURN NEW;
END;
$$;

-- ACTIVE bindings and confirmed source facts are historical identity/facts.
CREATE FUNCTION ledger.protect_portfolio_source_binding()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'PENDING' THEN
            RAISE EXCEPTION 'active/disabled source bindings cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;
    IF ROW(NEW.id, NEW.tenant_id, NEW.account_id, NEW.provider_code,
           NEW.external_account_key, NEW.created_by_batch_id, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.tenant_id, OLD.account_id, OLD.provider_code,
           OLD.external_account_key, OLD.created_by_batch_id, OLD.created_at) THEN
        RAISE EXCEPTION 'source binding ownership cannot be changed';
    END IF;
    IF OLD.status = 'PENDING' AND NEW.status NOT IN ('PENDING', 'ACTIVE') THEN
        RAISE EXCEPTION 'invalid source binding transition';
    END IF;
    IF OLD.status = 'ACTIVE' AND NEW.status NOT IN ('ACTIVE', 'DISABLED') THEN
        RAISE EXCEPTION 'invalid source binding transition';
    END IF;
    IF OLD.status = 'DISABLED' AND NEW.status NOT IN ('DISABLED', 'ACTIVE') THEN
        RAISE EXCEPTION 'invalid source binding transition';
    END IF;
    IF OLD.first_imported_at IS NOT NULL
       AND NEW.first_imported_at IS DISTINCT FROM OLD.first_imported_at THEN
        RAISE EXCEPTION 'source binding first import is immutable';
    END IF;
    IF NEW.last_imported_at IS NOT NULL AND OLD.last_imported_at IS NOT NULL
       AND NEW.last_imported_at < OLD.last_imported_at THEN
        RAISE EXCEPTION 'source binding last import cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_portfolio_source_bindings_trigger
BEFORE UPDATE OR DELETE ON ledger.portfolio_source_bindings
FOR EACH ROW EXECUTE FUNCTION ledger.protect_portfolio_source_binding();

CREATE FUNCTION ledger.protect_portfolio_source_record()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
DECLARE parent_status TEXT;
BEGIN
    SELECT status INTO parent_status
      FROM ledger.portfolio_import_batches
     WHERE tenant_id = COALESCE(NEW.tenant_id, OLD.tenant_id)
       AND id = COALESCE(NEW.batch_id, OLD.batch_id)
     FOR UPDATE;
    IF parent_status IS DISTINCT FROM 'DRAFT' THEN
        RAISE EXCEPTION 'source preview records can change only while batch is DRAFT';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
CREATE TRIGGER protect_portfolio_import_source_records_trigger
BEFORE INSERT OR UPDATE OR DELETE ON ledger.portfolio_import_source_records
FOR EACH ROW EXECUTE FUNCTION ledger.protect_portfolio_source_record();

CREATE FUNCTION ledger.protect_portfolio_source_transaction()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ledger, pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'confirmed portfolio source transactions are append-only';
END;
$$;
CREATE TRIGGER protect_portfolio_source_transactions_trigger
BEFORE UPDATE OR DELETE ON ledger.portfolio_source_transactions
FOR EACH ROW EXECUTE FUNCTION ledger.protect_portfolio_source_transaction();

-- 9. Tenant isolation and least privilege. Raw source payloads are never exposed
-- to the browser role; the sanitizing Domain API owns all UI reads and writes.
ALTER TABLE ledger.portfolio_source_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_source_bindings FORCE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_import_source_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_import_source_records FORCE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_source_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.portfolio_source_transactions FORCE ROW LEVEL SECURITY;
ALTER TABLE ledger.position_period_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.position_period_metrics FORCE ROW LEVEL SECURITY;

CREATE POLICY source_bindings_api_select ON ledger.portfolio_source_bindings
    FOR SELECT TO wealthsail_api
    USING (identity.is_tenant_member(tenant_id));
CREATE POLICY source_bindings_api_insert ON ledger.portfolio_source_bindings
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));
CREATE POLICY source_bindings_api_update ON ledger.portfolio_source_bindings
    FOR UPDATE TO wealthsail_api
    USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']))
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));
CREATE POLICY source_bindings_api_delete ON ledger.portfolio_source_bindings
    FOR DELETE TO wealthsail_api
    USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

CREATE POLICY source_records_api_select ON ledger.portfolio_import_source_records
    FOR SELECT TO wealthsail_api USING (identity.is_tenant_member(tenant_id));
CREATE POLICY source_records_api_insert ON ledger.portfolio_import_source_records
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));
CREATE POLICY source_records_api_delete ON ledger.portfolio_import_source_records
    FOR DELETE TO wealthsail_api
    USING (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

CREATE POLICY source_transactions_api_select ON ledger.portfolio_source_transactions
    FOR SELECT TO wealthsail_api USING (identity.is_tenant_member(tenant_id));
CREATE POLICY source_transactions_api_insert ON ledger.portfolio_source_transactions
    FOR INSERT TO wealthsail_api
    WITH CHECK (identity.has_tenant_role(tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']));

CREATE POLICY position_metrics_api_select ON ledger.position_period_metrics
    FOR SELECT TO wealthsail_api USING (identity.is_tenant_member(tenant_id));

REVOKE ALL ON
    ledger.portfolio_source_bindings,
    ledger.portfolio_import_source_records,
    ledger.portfolio_source_transactions,
    ledger.position_period_metrics
FROM PUBLIC, authenticated;

GRANT SELECT, INSERT, DELETE ON
    ledger.portfolio_source_bindings,
    ledger.portfolio_import_source_records
TO wealthsail_api;
GRANT UPDATE (status, confirmed_at, cancelled_at, version, updated_at,
              terminal_command_type, terminal_idempotency_key, terminal_content_hash)
    ON ledger.portfolio_import_batches TO wealthsail_api;
GRANT UPDATE (user_holding_started_on, updated_at)
    ON ledger.portfolio_import_items TO wealthsail_api;
GRANT UPDATE (display_name, status, first_imported_at, last_imported_at, updated_at)
    ON ledger.portfolio_source_bindings TO wealthsail_api;
GRANT SELECT, INSERT ON
    ledger.portfolio_source_transactions
TO wealthsail_api;
GRANT SELECT ON ledger.position_period_metrics TO wealthsail_api;

GRANT SELECT ON
    ledger.portfolio_source_transactions
TO wealthsail_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    ledger.position_period_metrics
TO wealthsail_worker;

COMMENT ON TABLE ledger.portfolio_source_bindings IS
    '匿名外部平台账号到 WealthSail 专用投资账户的租户内绑定；ACTIVE 后不可改绑。';
COMMENT ON TABLE ledger.portfolio_import_source_records IS
    'DRAFT 预览暂存来源记录；不是正式交易事实，取消或过期后可清理。';
COMMENT ON TABLE ledger.portfolio_source_transactions IS
    '用户确认后的外部基金流水来源事实；不可变且不自动伪装成 BUY/SELL。';
COMMENT ON TABLE ledger.position_period_metrics IS
    '持仓开始日和官方单位净值区间的每日派生投影，可按算法版本重建。';
