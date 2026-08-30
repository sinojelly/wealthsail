# Cumulative Performance Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留“当前持仓浮动盈亏”的同时，增加可审计、出入金调整后的累计收益金额，并允许用户可选设置跟踪起点之前的历史累计收益。

**Architecture:** 每个投资账户建立不可变、版本化的收益基线。基线记录跟踪起始日、起点总资产和用户确认的历史累计收益；当前累计收益由后端按 `历史初始值 + 当前总资产 - 起点总资产 - 跟踪期净入金` 计算。首次完整 NAV 自动建立初始值为 0 的基线，用户只在知道第三方平台历史累计盈亏时才需要修改；修改只追加新版本，不改账本和 NAV 事实。

**Tech Stack:** PostgreSQL NUMERIC/RLS/触发器、FastAPI/Pydantic、asyncpg/Decimal、React 19、TanStack Query、Vitest/pytest。

**Spec:** `docs/09.WealthSail-MVP-Web投研工作台设计与实现规范.md` 第 3.2、4.1 节；`docs/18.WealthSail租户用户账户与资产作用域设计.md` 的账户归属和查询范围。

## Global Constraints

- JSON 金额保持 Decimal 字符串，禁止 float。
- 买卖、分红和费用不是外部资金流；累计收益只调整 `CASH_IN/CASH_OUT`。
- 首次导入持仓不代表历史入金；不得再次把总市值当作累计收益。
- 无完整当前估值、无收益基线或作用域内任一账户不可计算时，累计收益返回 null。
- 用户不需要填写初始值；默认从基线日以 0 开始跟踪。
- 用户设置的是“历史累计收益金额”，不是收益率；累计收益率/TWR 本阶段继续不发布。
- 基线修改必须追加版本、校验 If-Match、支持幂等键并写审计。

---

### Task 1: Performance-baseline schema and security

**Files:**
- Create: `migrations/039_account_performance_baselines.sql`
- Modify: `scripts/migrate.py`
- Modify: `tests/unit/test_migration_runner.py`

**Interfaces:**
- Produces table: `ledger.account_performance_baselines`
- Produces trigger function: `ledger.initialize_account_performance_baseline()`

- [ ] **Step 1: Write migration contract tests**

The test loads migration 039 and asserts behaviorally relevant contracts: account/tenant composite FK, nullable `created_by` only for AUTO source, `NUMERIC(28,8)` money, `(tenant_id, account_id, version)` uniqueness, idempotency uniqueness, append-only trigger, API SELECT/INSERT policies, no browser grants, and automatic baseline creation after the first complete account NAV.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_migration_runner.py -q`

Expected: migration 039 is missing.

- [ ] **Step 3: Implement migration**

Create columns `baseline_as_of`, `baseline_total_asset`, `initial_cumulative_pnl`, `source`, `version`, `created_by`, `idempotency_key`, `request_hash`, `created_at`. Backfill one AUTO version from each active account's latest complete NAV. A SECURITY DEFINER NAV trigger inserts version 1 with initial value 0 for future accounts. RLS permits tenant-member reads and OWNER/ADMIN/EDITOR API inserts with `created_by = auth.uid()`; UPDATE/DELETE are trigger-rejected.

- [ ] **Step 4: Extend migration readback**

Readback verifies table/trigger existence, API least privilege, browser denial, and Worker ability to create NAV while the SECURITY DEFINER trigger initializes a baseline without granting Worker direct baseline INSERT.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_migration_runner.py -q`

Expected: PASS.

### Task 2: Decimal cumulative-performance domain calculation

**Files:**
- Modify: `backend/portfolio/repository.py`
- Modify: `backend/portfolio/service.py`
- Modify: `tests/unit/test_portfolio_integrity.py`

**Interfaces:**
- Produces: `latest_performance_baselines(conn, tenant_id, owner_user_id=None)`
- Produces: `external_cash_flows_since_baselines(conn, tenant_id, owner_user_id=None)`
- Extends summary: `cumulativePnl`, `cumulativePnlRate=null`, `performanceBaselineAsOf`, `performanceBaselineMixed`

- [ ] **Step 1: Write failing calculation tests**

```python
# baseline NAV 100000, current NAV 118000, later deposits 10000,
# historical initial PnL -2000 => -2000 + 118000 - 100000 - 10000 = 6000
assert result["cumulativePnl"] == "6000.00"
assert result["cumulativePnlRate"] is None
```

Add separate tests proving withdrawals use their signed cash amount, BUY/SELL legs are excluded, missing baseline returns null, incomplete current NAV returns null, and MINE/MEMBER scopes pass the owner filter to both baseline and cash-flow queries.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_portfolio_integrity.py tests/unit/test_portfolio_asset_scope.py -q`

Expected: cumulativePnl remains null and repository methods are missing.

- [ ] **Step 3: Implement repository queries**

Select the highest baseline version per account in scope. Aggregate confirmed, non-reversed `CASH_IN/CASH_OUT` cash legs whose `trade_date > baseline_as_of` and `trade_date <= latest NAV as_of`; return signed net inflow per account.

- [ ] **Step 4: Implement service calculation**

Require one complete current NAV and one baseline for every scoped account. Sum `initial + current NAV - baseline NAV - net inflow` using Decimal, expose the earliest baseline date and whether account dates differ, and keep TWR null.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_portfolio_integrity.py tests/unit/test_portfolio_asset_scope.py -q`

Expected: all portfolio calculation and scope tests pass.

### Task 3: Baseline read/update API

**Files:**
- Modify: `backend/portfolio/api.py`
- Modify: `backend/portfolio/repository.py`
- Modify: `backend/portfolio/service.py`
- Create: `tests/unit/test_performance_baseline_api.py`

**Interfaces:**
- Produces: `GET /api/v1/portfolio/performance-baselines`
- Produces: `PUT /api/v1/portfolio/accounts/{accountId}/performance-baseline`
- Request: `{ "initialCumulativePnl": "-2000.00" }`, headers `If-Match`, `Idempotency-Key`

- [ ] **Step 1: Write failing API/service tests**

Test that GET returns only MINE-owned accounts and current baseline versions; PUT rejects an unowned account, stale version, malformed amount and reused idempotency key with different content. A valid PUT appends version 2 with unchanged `baseline_as_of/baseline_total_asset`, writes `PERFORMANCE_BASELINE_REVISED`, and returns the new DTO.

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_performance_baseline_api.py -q`

Expected: routes and service methods are absent.

- [ ] **Step 3: Implement GET and PUT**

Use the existing tenant transaction and authenticated context. Parse If-Match with the shared strict parser, lock the latest account baseline, check beneficial ownership and tenant edit role, compare request hash for replays, append one version, and write audit in the same transaction.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_performance_baseline_api.py -q`

Expected: PASS.

### Task 4: Dashboard cumulative-profit card

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/test/fixtures.ts`
- Modify: `frontend/src/features/dashboard/components/MetricCards.tsx`
- Modify: `frontend/src/features/dashboard/metric-cards.test.tsx`

**Interfaces:**
- Consumes: summary cumulative fields and baseline metadata.

- [ ] **Step 1: Write the failing component test**

```tsx
expect(screen.getByText("累计收益")).toBeInTheDocument();
expect(screen.getByText("+¥6,000.00")).toHaveClass("pnl-gain");
expect(screen.getByText("自 2026-08-30 跟踪")).toBeInTheDocument();
expect(screen.queryByText(/TWR/)).not.toBeInTheDocument();
```

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir frontend test -- metric-cards.test.tsx`

Expected: only the holding-profit card exists.

- [ ] **Step 3: Add the sixth card**

Use Chinese-market colors for the amount. The hint displays the baseline date, `多个基线日期` when mixed, or `等待完整估值` when unavailable. Do not display a fabricated percentage.

- [ ] **Step 4: Verify GREEN**

Run: `pnpm --dir frontend test -- metric-cards.test.tsx`

Expected: PASS.

### Task 5: Optional baseline setting UI

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/portfolio.ts`
- Modify: `frontend/src/api/hooks.ts`
- Create: `frontend/src/features/settings/components/PerformanceBaselineCard.tsx`
- Modify: `frontend/src/features/settings/SettingsPage.tsx`
- Modify: `frontend/src/features/settings/settings.test.tsx`

**Interfaces:**
- Produces hooks: `usePerformanceBaselines()` and `useUpdatePerformanceBaseline()`

- [ ] **Step 1: Write failing interaction tests**

Test that the card explains default zero tracking without requiring input, lists each owned account, accepts positive/negative two-decimal amounts, submits account/version/idempotency values, refreshes summary/baselines after success, and renders no editor controls for VIEWER.

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir frontend test -- settings.test.tsx`

Expected: the baseline card and controls are absent.

- [ ] **Step 3: Implement API client/hooks/card**

The card labels the field “跟踪前历史累计收益”，shows “不知道可保持 0”，and never asks for a return percentage. Disable save while unchanged or pending; map version conflict to “数据已更新，请刷新后重试”.

- [ ] **Step 4: Verify GREEN**

Run: `pnpm --dir frontend test -- settings.test.tsx`

Expected: PASS.

### Task 6: Documentation and full verification

**Files:**
- Modify: `docs/09.WealthSail-MVP-Web投研工作台设计与实现规范.md`
- Modify: `docs/10.WealthSail-MVP前后端实现与系统部署规范.md`
- Modify: `docs/11.数据库升级方法.md`

- [ ] **Step 1: Document financial semantics and operations**

Document the baseline equation, external-flow rules, default-zero behavior, optional historical adjustment, append-only revisions, null/abstention conditions, migration 039 commands and readback SQL.

- [ ] **Step 2: Run complete verification**

Run backend pytest, frontend Vitest, ESLint, TypeScript production build, migration discovery/readback unit tests, `git diff --check`, and verify no unrelated untracked design files are staged.
