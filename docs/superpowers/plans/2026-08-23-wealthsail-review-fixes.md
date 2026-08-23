# WealthSail Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the security, data integrity, phase-boundary, traceability, and reproducibility regressions introduced between `93f3a81` and `518eb09` without removing the restored business detail.

**Architecture:** Keep the modular monolith and PostgreSQL facts/projections design. Hermes and WeCom remain replaceable edge adapters that call scoped Domain APIs; financial calculations and state transitions remain deterministic. L0/L1 is the only current delivery scope, while detailed L2–L5 material stays documented with explicit phase labels and gates.

**Tech Stack:** Python 3.12+, pytest, PostgreSQL 16/Supabase, PowerShell documentation validation, Hermes/WeCom adapter boundary, Markdown.

**Spec:** `ai-review/03.WealthSail-93f3a81到518eb09变更Review与修复方案.md`

## Global Constraints

- L1 includes A shares, exchange funds, off-exchange funds, four daily sessions, WeCom, and 2–3 users.
- Gold, commodities, global markets, backtests, DSL, broker integration stay at L4.
- Multi-Agent and autonomous Skill evolution stay at L5 and are experimental last-priority capabilities.
- Hermes/Channel adapters never receive database service-role credentials.
- User acceptance of advice is not proof of an executed trade.
- Missing, stale, conflicting, or malformed market data must fail closed.
- Restored business scenarios and examples remain, but every non-L1 section is phase-labelled.

---

### Task 1: Secure WeCom and Binding Primitives

**Files:**
- Modify: `adapters/messaging/wecom_adapter.py`
- Modify: `tests/e2e/test_four_sessions_workflow.py`

**Interfaces:**
- Consumes: explicit WeCom credentials and explicit recipient IDs.
- Produces: `send_markdown_message(content: str, to_user: str, idempotency_key: str | None = None, dry_run: bool = False) -> dict[str, str]` and token helpers requiring an injected secret.

- [x] **Step 1: Write failing recipient and configuration tests**

```python
def test_wecom_rejects_broadcast_recipient():
    adapter = WeComAdapter(corp_id="corp", corp_secret="secret", agent_id=1)
    with pytest.raises(ValueError, match="收件人"):
        adapter.send_markdown_message("private", "@all")

def test_wecom_missing_credentials_fail_closed():
    adapter = WeComAdapter(corp_id="", corp_secret="", agent_id=1)
    result = adapter.send_markdown_message("private", "user-1")
    assert result["status"] == "CONFIG_ERROR"
```

- [x] **Step 2: Run the tests and confirm both fail for the current defaults**

Run: `python -m pytest tests/e2e/test_four_sessions_workflow.py -v`

- [x] **Step 3: Require recipients, explicit dry-run, and injected token secret**

Implement recipient validation, remove `@all`, return `CONFIG_ERROR` without credentials, and reject empty/static binding secrets.

- [x] **Step 4: Run the focused tests and full Python suite**

Run: `python -m pytest tests/e2e/test_four_sessions_workflow.py -v` then `python -m pytest tests -v`.

### Task 2: Make Ledger Prototype Honest and Safe

**Files:**
- Modify: `modules/ledger/service.py`
- Modify: `tests/unit/test_ledger_math.py`

**Interfaces:**
- Consumes: validated positive quantities/prices and non-negative fees.
- Produces: frozen events/legs, UTC timestamps, SELL and reversal-safe projections, process-local prototype semantics explicitly documented.

- [x] **Step 1: Add failing tests for negative fees, SELL, mutation, and duplicate keys**

```python
def test_buy_rejects_negative_fee():
    ledger = funded_ledger(Decimal("1000"))
    with pytest.raises(ValueError, match="手续费"):
        ledger.record_buy(uuid.uuid4(), Decimal("1"), Decimal("10"), Decimal("-1"), date.today(), "bad-fee")
```

- [x] **Step 2: Confirm the negative-fee test fails because current code accepts it**

Run: `python -m pytest tests/unit/test_ledger_math.py -k negative_fee -v`.

- [x] **Step 3: Implement minimal validation, immutable values, UTC timestamps, and SELL projection**

Use frozen dataclasses/tuples, reject negative fees, add `record_sell`, preserve cost basis proportionally, and reject overselling.

- [x] **Step 4: Run ledger tests and mutation-oriented edge cases**

Run: `python -m pytest tests/unit/test_ledger_math.py -v`.

### Task 3: Replace Fake Market and Fund Estimates

**Files:**
- Modify: `modules/market_data/providers.py`
- Modify: `tests/unit/test_market_data_quality.py`
- Modify: `skills/fund_intraday_valuation/SKILL.md`

**Interfaces:**
- Consumes: source timestamps and explicit fund component return/weight observations.
- Produces: observations defaulting to UNAVAILABLE and a deterministic `estimate_fund_nav(...)` result with method, coverage, lower/upper range, and quality.

- [x] **Step 1: Add failing timestamp, default-quality, and estimation tests**

```python
def test_market_observation_defaults_fail_closed_and_use_distinct_timestamps():
    first = MarketObservation("600519", "CLOSE")
    second = MarketObservation("510300", "CLOSE")
    assert first.quality_status is DataQualityStatus.UNAVAILABLE
    assert second.observed_at >= first.observed_at
```

- [x] **Step 2: Confirm current FRESH default makes the test fail**

Run: `python -m pytest tests/unit/test_market_data_quality.py -v`.

- [x] **Step 3: Use `default_factory`, explicit freshness classification, and component-based fund estimates**

Delete the fixed `0.995/1.015` calculation. Return UNAVAILABLE when holdings coverage is below the documented threshold.

- [x] **Step 4: Correct the fund Skill threshold and run provider tests**

Change the dip condition to an unambiguous signed-return comparison and require policy validation before any amount suggestion.

### Task 4: Repair Database Integrity and RLS Design

**Files:**
- Modify: `migrations/002_identity_and_tenancy.sql`
- Modify: `migrations/003_reference_and_ledger.sql`
- Modify: `migrations/004_market_and_research.sql`
- Modify: `migrations/005_rls_security_policies.sql`
- Modify: `migrations/006_mock_seed_data.sql`
- Modify: `docs/06.WealthSail数据模型与数据源治理设计.md`
- Modify: `docs/07.WealthSail安全隐私与合规设计.md`

**Interfaces:**
- Consumes: Supabase `auth.uid()` only in the Supabase deployment profile.
- Produces: role-aware RLS, append-only facts, same-tenant composite foreign keys, idempotent three-tenant fixtures, and a documented portable PostgreSQL identity adapter.

- [x] **Step 1: Extend document validation with migration security invariants**

The validator must fail when ledger/audit receive generic member UPDATE/DELETE policies, when `@all` is present, or when a Hermes example contains `SUPABASE_SERVICE_ROLE_KEY`.

- [x] **Step 2: Run the validator and confirm current migrations/docs fail**

Run: `powershell -File scripts/validate_docs.ps1`.

- [x] **Step 3: Add role helpers, explicit per-table policies, append-only triggers, and tenant composite keys**

Use separate policy statements instead of the current all-table CRUD loop. Revoke PUBLIC execution on security-definer helpers and grant only to authenticated/runtime roles where present.

- [x] **Step 4: Make seed rows deterministic and cover all three pilot tenants**

Give ledger legs deterministic IDs, create at least one account per tenant, and ensure repeated seed execution has no economic effect.

- [x] **Step 5: Re-run documentation/security validation**

Run: `powershell -File scripts/validate_docs.ps1`.

### Task 5: Restore Phase and Architecture Consistency

**Files:**
- Modify: `docs/01.WealthSail投研平台需求文档.md`
- Modify: `docs/02.WealthSail投研平台全栈技术选型与架构评估.md`
- Modify: `docs/03.WealthSail-MVP快速构建与实战指南.md`
- Modify: `skills/premarket_briefing/SKILL.md`
- Modify: `skills/intraday_hitl_monitor/SKILL.md`
- Modify: `skills/postmarket_review/SKILL.md`
- Modify: `skills/macro_cycle_analysis/SKILL.md`

**Interfaces:**
- Consumes: L0–L5 canonical level definitions.
- Produces: an L0/L1-only MVP flow plus a fully detailed phase-labelled panorama, scoped Hermes API diagrams, and demo-labelled examples.

- [x] **Step 1: Add L5 and move Multi-Agent/Skill autonomy to last priority**

Preserve detailed scenarios while adding limitations, entry criteria, evaluation metrics, rollback, and single-agent fallback.

- [x] **Step 2: Restore a neutral architecture comparison**

Compare modular Python, Hermes, OpenClaw, LangGraph, OpenAI Agents SDK, multi-agent frameworks, and DBOS/Restate/Temporal by trust boundary, durable execution, HITL, operations, cost, and trigger conditions.

- [x] **Step 3: Separate recommendation confirmation from execution facts in every diagram and Skill**

All examples must show decision recording first and a separate confirmed-fill step before ledger writes.

- [x] **Step 4: Mark all hard-coded examples as `EXAMPLE / NOT_LIVE` and use a trading-day placeholder**

No example price may be labelled FRESH without a source timestamp supplied at runtime.

### Task 6: Restore Tests, Traceability, and Reproducibility

**Files:**
- Modify: `docs/04.WealthSail-MVP功能验收与自动化测试规范.md`
- Modify: `docs/05.WealthSail-全量系统功能验收与全栈测试规范.md`
- Modify: `docs/08.WealthSail需求设计测试追溯矩阵.md`
- Modify: `scripts/validate_docs.ps1`
- Create: `requirements-dev.txt`

**Interfaces:**
- Consumes: canonical requirement and test IDs from PRD/traceability.
- Produces: no dangling trace IDs, reproducible pytest dependencies, and truthful NOT_RUN/PASS evidence semantics.

- [x] **Step 1: Extend validator to compare every traced test ID against docs/04 and docs/05**

The test-ID parser must expand ranges such as `IT-RLS-001~006` or require explicit rows; this implementation will use explicit rows for L0/L1 critical tests.

- [x] **Step 2: Confirm the current 72 dangling references fail validation**

Run: `powershell -File scripts/validate_docs.ps1`.

- [x] **Step 3: Restore detailed normal/alternative/exception test definitions**

Use the canonical IDs already present in `docs/08`; do not rename them to ST/IT variants without updating the trace matrix in the same patch.

- [x] **Step 4: Add reproducible development dependencies**

Create `requirements-dev.txt` with bounded versions for pytest and runtime imports used by the prototype.

- [x] **Step 5: Run all verification gates**

Run Python tests, documentation validation, front-end tests, `git diff --check`, and inspect `git status` before committing.

### Task 7: Final Review and Git Commit

**Files:**
- Modify: this plan's checkbox states only if execution tracking is retained.

- [x] **Step 1: Re-read the Review specification and map every blocker to a diff or test**
- [x] **Step 2: Run fresh full verification commands and capture exact results**
- [x] **Step 3: Review `git diff --stat` and `git diff` for accidental loss of detailed content**
- [x] **Step 4: Commit with a detailed message describing security, phase, data, and test fixes**
