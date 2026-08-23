-- ==============================================================================
-- WealthSail Migration 004: Market Data, Research, Reporting & Governance
-- Version: v2.0.0
-- Description: 来源快照、研究论点、四时段报告、风控规则、Outbox与审计日志
-- ==============================================================================

-- 1. 外部数据源抓取快照表 (Source Snapshots)
CREATE TABLE IF NOT EXISTS market.source_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES reference.data_sources(id),
    retrieval_key TEXT NOT NULL,
    origin_uri TEXT,
    published_at TIMESTAMPTZ,
    as_of TIMESTAMPTZ NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revised_at TIMESTAMPTZ,
    content_hash TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'UNAVAILABLE' CHECK (quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'CONFLICT', 'UNAVAILABLE', 'REVISED')),
    payload_ref TEXT, -- 指向对象存储的只读引用，不存大 JSON
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. 市场与宏观长表观测 (Market Observations)
CREATE TABLE IF NOT EXISTS market.market_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type TEXT NOT NULL CHECK (subject_type IN ('INSTRUMENT', 'INDEX', 'MACRO_SERIES', 'FUND')),
    subject_id UUID NOT NULL,
    field_code TEXT NOT NULL, -- CLOSE, NAV, ESTIMATED_NAV, FX_RATE, PMI, INVENTORY_STAGE
    value NUMERIC(28, 10),
    lower_value NUMERIC(28, 10), -- 估值/区间下限
    upper_value NUMERIC(28, 10), -- 估值/区间上限
    unit TEXT,
    currency CHAR(3) DEFAULT 'CNY',
    as_of TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    snapshot_id UUID REFERENCES market.source_snapshots(id),
    source_id UUID REFERENCES reference.data_sources(id),
    quality_status TEXT NOT NULL DEFAULT 'UNAVAILABLE' CHECK (quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'CONFLICT', 'UNAVAILABLE', 'REVISED')),
    method_version TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject_id, field_code, as_of, source_id, revision)
);

-- 3. 公司行动表 (Corporate Actions - 分红派息、拆并股)
CREATE TABLE IF NOT EXISTS market.corporate_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    action_type TEXT NOT NULL CHECK (action_type IN ('CASH_DIVIDEND', 'STOCK_DIVIDEND', 'SPLIT', 'MERGER', 'CODE_CHANGE', 'DELISTING')),
    ex_date DATE NOT NULL,
    record_date DATE,
    pay_date DATE,
    params JSONB NOT NULL, -- 如 {"cash_per_share": 0.20, "split_ratio": 1.5}
    source_id UUID REFERENCES reference.data_sources(id),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (instrument_id, action_type, ex_date, revision)
);

-- 4. 投资论点表 (Theses - 用户持仓逻辑与失效条件)
CREATE TABLE IF NOT EXISTS research.theses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID REFERENCES identity.accounts(id) ON DELETE CASCADE,
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    summary TEXT NOT NULL,
    horizon TEXT NOT NULL DEFAULT 'MEDIUM_TERM' CHECK (horizon IN ('SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM')),
    catalysts JSONB DEFAULT '[]', -- 潜在催化剂列表
    risks JSONB DEFAULT '[]',     -- 核心风险列表
    invalidation_conditions JSONB NOT NULL DEFAULT '[]', -- 失效/出局出场条件
    target_price NUMERIC(28, 10),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INVALIDATED', 'CLOSED')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. 模型执行记录表 (Model Runs - 审计与复现)
CREATE TABLE IF NOT EXISTS research.model_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_template_version TEXT NOT NULL,
    skill_version TEXT,
    input_snapshot_hashes JSONB NOT NULL,
    tool_calls JSONB DEFAULT '[]',
    structured_output_hash TEXT,
    token_usage JSONB DEFAULT '{}',
    cost_cny NUMERIC(10, 4) DEFAULT 0.0000,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'ABSTAINED')),
    error_class TEXT,
    correlation_id TEXT
);

-- 6. 投研建议表 (Recommendations - 结构化投研结论)
CREATE TABLE IF NOT EXISTS research.recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    instrument_id UUID REFERENCES reference.instruments(id),
    thesis_id UUID REFERENCES research.theses(id),
    model_run_id UUID REFERENCES research.model_runs(id),
    job_run_id UUID, -- migration 007 在 workflow.job_runs 创建后补充同租户复合外键
    recommendation_type TEXT NOT NULL CHECK (recommendation_type IN ('HOLD', 'OBSERVE', 'REVIEW', 'REDUCE_CANDIDATE', 'ADD_CANDIDATE', 'ABSTAIN')),
    summary TEXT NOT NULL,
    counter_case TEXT, -- 反方论点/空头挑刺
    invalidation_conditions JSONB,
    confidence NUMERIC(5, 4),
    policy_check_status TEXT NOT NULL DEFAULT 'PASSED' CHECK (policy_check_status IN ('PASSED', 'REJECTED', 'WARNING')),
    valid_until TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'PUBLISHED' CHECK (status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'EXPIRED', 'RETRACTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 7. 证据引用表 (Evidence References)
CREATE TABLE IF NOT EXISTS research.evidence_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    recommendation_id UUID REFERENCES research.recommendations(id) ON DELETE CASCADE,
    source_snapshot_id UUID REFERENCES market.source_snapshots(id),
    claim_type TEXT NOT NULL CHECK (claim_type IN ('PRICE_STAT', 'VALUATION', 'NEWS_FACT', 'POLICY', 'EARNINGS')),
    as_of TIMESTAMPTZ NOT NULL,
    excerpt TEXT,
    quality_status TEXT NOT NULL DEFAULT 'UNAVAILABLE' CHECK (quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'CONFLICT', 'UNAVAILABLE', 'REVISED')),
    relevance_score NUMERIC(5, 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 8. 四时段报告主表 (Reports - 盘前/盘中/14:30/盘后)
CREATE TABLE IF NOT EXISTS reporting.reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    report_session TEXT NOT NULL CHECK (report_session IN ('PRE_MARKET', 'INTRADAY', 'FUND_1430', 'POST_MARKET')),
    trade_date DATE NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'PUBLISHED' CHECK (status IN ('DRAFT', 'VALIDATED', 'PUBLISHED', 'PARTIAL', 'ABSTAIN', 'EXPIRED', 'RETRACTED')),
    as_of TIMESTAMPTZ NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'UNAVAILABLE' CHECK (quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE')),
    summary TEXT NOT NULL,
    model_run_id UUID REFERENCES research.model_runs(id),
    job_run_id UUID, -- 本文件末尾创建 workflow.job_runs 后由 migration 007 增加同租户复合外键
    policy_check_status TEXT NOT NULL DEFAULT 'PASSED',
    valid_until TIMESTAMPTZ NOT NULL,
    supersedes_report_id UUID REFERENCES reporting.reports(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, trade_date, report_session, version)
);

-- 9. 报告细项条目表 (Report Items)
CREATE TABLE IF NOT EXISTS reporting.report_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES reporting.reports(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL CHECK (item_type IN ('FACT', 'RISK', 'ACTION_PLAN', 'TODO', 'FUND_ESTIMATE', 'ALERT')),
    subject_id UUID,
    severity TEXT NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    action_plan JSONB, -- 包含 If-Then 结构化剧本
    data_quality_status TEXT NOT NULL DEFAULT 'UNAVAILABLE' CHECK (data_quality_status IN ('FRESH', 'ESTIMATED', 'STALE', 'UNAVAILABLE')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 10. 用户决策与确认表 (Decisions - 人机协同闭环)
CREATE TABLE IF NOT EXISTS reporting.decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    report_id UUID REFERENCES reporting.reports(id),
    recommendation_id UUID REFERENCES research.recommendations(id),
    user_id UUID NOT NULL REFERENCES identity.profiles(id),
    decision_action TEXT NOT NULL CHECK (decision_action IN ('ACCEPTED', 'IGNORED', 'DEFERRED')),
    status TEXT NOT NULL DEFAULT 'RECORDED' CHECK (status IN ('RECORDED', 'RETRACTED', 'SUPERSEDED')),
    reason TEXT,
    correction TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_key TEXT NOT NULL,
    previous_decision_id UUID REFERENCES reporting.decisions(id),
    UNIQUE (tenant_id, idempotency_key)
);

-- 10.1 成交草稿与确认事实。用户采纳建议只写 decisions；只有独立确认的
-- 成交记录才能关联 ledger_event_id。L1 支持用户确认/对账导入，券商回报为 L4。
CREATE TABLE IF NOT EXISTS ledger.trade_fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES identity.accounts(id) ON DELETE RESTRICT,
    instrument_id UUID NOT NULL REFERENCES reference.instruments(id),
    decision_id UUID REFERENCES reporting.decisions(id) ON DELETE SET NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity NUMERIC(28, 10) NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(28, 10) NOT NULL CHECK (unit_price > 0),
    fee NUMERIC(28, 8) NOT NULL DEFAULT 0 CHECK (fee >= 0),
    trade_date DATE NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('USER_CONFIRMED', 'STATEMENT_IMPORT', 'BROKER_FILL')),
    external_fill_id TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'CONFIRMED', 'REJECTED', 'CANCELLED')),
    ledger_event_id UUID REFERENCES ledger.ledger_events(id) ON DELETE RESTRICT,
    created_by UUID NOT NULL REFERENCES identity.profiles(id),
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    CHECK (
        (status = 'CONFIRMED' AND ledger_event_id IS NOT NULL AND confirmed_at IS NOT NULL)
        OR (status <> 'CONFIRMED' AND ledger_event_id IS NULL AND confirmed_at IS NULL)
    ),
    UNIQUE (tenant_id, idempotency_key)
);

-- 11. 账户投资政策与风险预算表 (Investment Policies)
CREATE TABLE IF NOT EXISTS risk.investment_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID REFERENCES identity.accounts(id) ON DELETE CASCADE,
    max_drawdown_limit NUMERIC(5, 2),
    single_position_limit NUMERIC(5, 2), -- 用户确认的单标的仓位上限(%)
    equity_target_pct NUMERIC(5, 2),
    bond_target_pct NUMERIC(5, 2),
    gold_target_pct NUMERIC(5, 2),
    cash_target_pct NUMERIC(5, 2),
    rebalance_tolerance_pct NUMERIC(5, 2),
    policy_status TEXT NOT NULL DEFAULT 'DRAFT',
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT investment_policies_status_check
        CHECK (policy_status IN ('DRAFT', 'CONFIRMED', 'RETIRED')),
    CONSTRAINT investment_policies_drawdown_check
        CHECK (max_drawdown_limit IS NULL OR max_drawdown_limit > 0 AND max_drawdown_limit <= 100),
    CONSTRAINT investment_policies_single_position_check
        CHECK (single_position_limit IS NULL OR single_position_limit BETWEEN 0 AND 100),
    CONSTRAINT investment_policies_equity_target_check
        CHECK (equity_target_pct IS NULL OR equity_target_pct BETWEEN 0 AND 100),
    CONSTRAINT investment_policies_bond_target_check
        CHECK (bond_target_pct IS NULL OR bond_target_pct BETWEEN 0 AND 100),
    CONSTRAINT investment_policies_gold_target_check
        CHECK (gold_target_pct IS NULL OR gold_target_pct BETWEEN 0 AND 100),
    CONSTRAINT investment_policies_cash_target_check
        CHECK (cash_target_pct IS NULL OR cash_target_pct BETWEEN 0 AND 100),
    CONSTRAINT investment_policies_rebalance_check
        CHECK (rebalance_tolerance_pct IS NULL OR rebalance_tolerance_pct BETWEEN 0 AND 100),
    CONSTRAINT investment_policies_effective_dates_check
        CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

-- 12. 触点风控告警规则表 (Alert Rules)
CREATE TABLE IF NOT EXISTS risk.alert_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    account_id UUID REFERENCES identity.accounts(id) ON DELETE CASCADE,
    instrument_id UUID REFERENCES reference.instruments(id),
    thesis_id UUID REFERENCES research.theses(id),
    rule_type TEXT NOT NULL CHECK (rule_type IN ('PRICE_TOUCH_STOP_LOSS', 'PRICE_TOUCH_TAKE_PROFIT', 'ESTIMATED_NAV_SWING', 'MACRO_CYCLE_SHIFT')),
    rule_params JSONB NOT NULL,
    silence_window_minutes INTEGER NOT NULL DEFAULT 60, -- 静默防振荡窗口(分钟)
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED', 'EXPIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 13. 告警事件表 (Alert Events)
CREATE TABLE IF NOT EXISTS risk.alert_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    rule_id UUID NOT NULL REFERENCES risk.alert_rules(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL, -- 唯一防振荡指纹
    dedupe_key TEXT, -- tenant + rule + time-window 的应用层稳定去重键
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'SUPPRESSED', 'DELIVERED', 'ACKNOWLEDGED', 'EXPIRED', 'CANCELLED')),
    suppress_reason TEXT,
    outbox_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 14. 任务调度表 (Jobs & Job Runs)
CREATE TABLE IF NOT EXISTS workflow.jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    job_name TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    report_session TEXT CHECK (report_session IN ('PRE_MARKET', 'INTRADAY', 'FUND_1430', 'POST_MARKET')),
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow.job_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    job_id UUID REFERENCES workflow.jobs(id) ON DELETE CASCADE,
    job_key TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'ABSTAINED')),
    error_class TEXT,
    UNIQUE (tenant_id, job_key)
);

-- 15. 持久化人机交互请求。等待用户响应不是 decision；decision 只在用户
-- 真正选择 ACCEPTED/IGNORED/DEFERRED 后追加。Token 仅保存带密钥摘要。
CREATE TABLE IF NOT EXISTS workflow.interaction_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE RESTRICT,
    channel_binding_id UUID NOT NULL REFERENCES identity.channel_bindings(id) ON DELETE RESTRICT,
    report_id UUID REFERENCES reporting.reports(id) ON DELETE RESTRICT,
    recommendation_id UUID REFERENCES research.recommendations(id) ON DELETE RESTRICT,
    request_type TEXT NOT NULL CHECK (request_type IN ('DECISION_PROMPT', 'FILL_CONFIRMATION', 'PORTFOLIO_CONFIRMATION')),
    allowed_actions TEXT[] NOT NULL CHECK (
        cardinality(allowed_actions) > 0
        AND array_position(allowed_actions, NULL) IS NULL
    ),
    action_token_hash TEXT NOT NULL,
    token_expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CONSUMED', 'EXPIRED', 'CANCELLED')),
    consumed_action TEXT,
    resolved_at TIMESTAMPTZ,
    payload_schema_version INTEGER NOT NULL DEFAULT 1 CHECK (payload_schema_version > 0),
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (token_expires_at > created_at),
    CHECK (report_id IS NOT NULL OR recommendation_id IS NOT NULL),
    CHECK (consumed_action IS NULL OR consumed_action = ANY (allowed_actions)),
    CHECK (
        (status = 'OPEN' AND consumed_action IS NULL AND resolved_at IS NULL)
        OR (status = 'CONSUMED' AND consumed_action IS NOT NULL AND resolved_at IS NOT NULL)
        OR (status IN ('EXPIRED', 'CANCELLED') AND consumed_action IS NULL AND resolved_at IS NOT NULL)
    ),
    UNIQUE (tenant_id, idempotency_key),
    UNIQUE (tenant_id, action_token_hash)
);

CREATE INDEX IF NOT EXISTS idx_interaction_requests_open_expiry
ON workflow.interaction_requests (tenant_id, token_expires_at)
WHERE status = 'OPEN';

-- 16. 事务性可靠投递 Outbox 表 (Outbox)
CREATE TABLE IF NOT EXISTS workflow.outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id UUID NOT NULL,
    payload JSONB NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'DEAD')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_class TEXT,
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    external_message_id TEXT, -- 渠道回执 ID，用于超时后查证而非盲目重发
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, topic, idempotency_key)
);

-- 17. 统一不可变审计日志表 (Audit Log - Append-Only)
CREATE TABLE IF NOT EXISTS audit.audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES identity.tenants(id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('USER', 'SYSTEM_WORKER', 'HERMES_AGENT')),
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    correlation_id TEXT,
    details JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
