# Watchlist and Split PRE_MARKET Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-owned watchlist and split PRE_MARKET into an 08:30 shared macro report plus an 08:35 user-specific holdings/watchlist report.

**Architecture:** A new RLS-protected `identity.watchlist` module exposes canonical-symbol CRUD and feeds a user-aware report context. PRE_MARKET remains the public subscription/session but gains internal report variants and audience ownership; the scheduler dispatches macro and personalized phases against the existing single PRE_MARKET job, with a daily append-only macro cache and audience-specific job keys.

**Tech Stack:** PostgreSQL migrations and RLS, FastAPI, asyncpg, Pydantic v2, React 19, TanStack Query, TypeScript, Vitest, pytest, Ruff, ESLint.

**Spec:** `docs/superpowers/specs/2026-09-01-watchlist-and-split-pre-market-design.md`

## Global Constraints

- Complete Tasks 1–4 and publish Feature 1 before starting Task 5.
- Keep public report sessions exactly `PRE_MARKET / INTRADAY / FUND_1430 / POST_MARKET`.
- Keep `workflow.jobs` at one enabled row per tenant and public report session.
- Do not add model calls; missing governed data renders `暂无`.
- Watchlist writes are always self-owned; browser input cannot choose `user_id`, `asset_name`, or `asset_type`.
- Personalized reports are visible only to their `audience_user_id`; macro and legacy reports remain tenant-visible.
- Report subscription switches control Outbox only, never report generation or audit persistence.
- Every behavior change follows RED → GREEN → refactor and every task ends with focused tests and a Git commit pushed to `origin/master`.

---

### Task 1: Migration 044 — self-owned watchlist boundary

**Files:**
- Create: `migrations/044_user_watchlist.sql`
- Modify: `scripts/migrate.py`
- Create: `tests/unit/test_watchlist_migration.py`
- Modify: `tests/unit/test_migration_runner.py`

**Interfaces:**
- Produces: `identity.watchlist(user_id, symbol, asset_name, asset_type, added_at)` with primary key `(user_id, symbol)`.
- Produces: readback failure text `watchlist ownership boundary is missing or unsafe`.

- [ ] **Step 1: Write the failing migration contract tests**

```python
def test_044_creates_self_owned_watchlist_without_public_access() -> None:
    sql = (ROOT / "migrations/044_user_watchlist.sql").read_text("utf-8")
    for token in (
        "CREATE TABLE identity.watchlist",
        "PRIMARY KEY (user_id, symbol)",
        "CHECK (asset_type IN ('STOCK', 'ETF', 'FUND'))",
        "ENABLE ROW LEVEL SECURITY",
        "user_id = auth.uid()",
        "REVOKE ALL ON identity.watchlist FROM PUBLIC",
        "GRANT SELECT, INSERT, DELETE ON identity.watchlist TO wealthsail_api",
    ):
        assert token in sql


def test_readback_checks_watchlist_shape_rls_and_privileges() -> None:
    source = inspect.getsource(_readback)
    assert "watchlist ownership boundary is missing or unsafe" in source
    assert "identity.watchlist" in source
    assert '("SELECT", "INSERT", "DELETE", "UPDATE")' in source
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_watchlist_migration.py tests/unit/test_migration_runner.py::test_readback_checks_watchlist_shape_rls_and_privileges
```

Expected: FAIL because migration 044 and its readback contract do not exist.

- [ ] **Step 3: Implement migration 044**

Create the table and policies in one transaction. Use fixed policy names:

```sql
CREATE TABLE identity.watchlist (
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL CHECK (symbol = upper(btrim(symbol)) AND length(symbol) BETWEEN 1 AND 32),
    asset_name TEXT NOT NULL CHECK (length(btrim(asset_name)) BETWEEN 1 AND 200),
    asset_type TEXT NOT NULL CHECK (asset_type IN ('STOCK', 'ETF', 'FUND')),
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);
ALTER TABLE identity.watchlist ENABLE ROW LEVEL SECURITY;
CREATE POLICY watchlist_self_select ON identity.watchlist
    FOR SELECT TO authenticated USING (user_id = auth.uid());
CREATE POLICY watchlist_self_insert ON identity.watchlist
    FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());
CREATE POLICY watchlist_self_delete ON identity.watchlist
    FOR DELETE TO authenticated USING (user_id = auth.uid());
CREATE POLICY watchlist_domain_api_select ON identity.watchlist
    FOR SELECT TO wealthsail_api
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY watchlist_domain_api_insert ON identity.watchlist
    FOR INSERT TO wealthsail_api
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY watchlist_domain_api_delete ON identity.watchlist
    FOR DELETE TO wealthsail_api
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
REVOKE ALL ON identity.watchlist FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, DELETE ON identity.watchlist TO wealthsail_api;
```

The API role does not receive UPDATE because canonical metadata is immutable per row; a changed classification requires delete then add after server validation. Extend `_readback` to require all five columns, composite primary key, RLS enabled, all six fixed policies, API SELECT/INSERT/DELETE true, API UPDATE false, and all four privileges false for `authenticated` and `public`.

- [ ] **Step 4: Verify GREEN and migration discovery**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_watchlist_migration.py tests/unit/test_migration_runner.py
.\.venv\Scripts\python.exe -m ruff check scripts tests/unit/test_watchlist_migration.py
```

- [ ] **Step 5: Commit and push**

```powershell
git add migrations/044_user_watchlist.sql scripts/migrate.py tests/unit/test_watchlist_migration.py tests/unit/test_migration_runner.py
git commit -m "feat(watchlist): add self-owned storage boundary"
git push origin master
```

---

### Task 2: Watchlist domain API with canonical instrument validation

**Files:**
- Create: `backend/watchlist/__init__.py`
- Create: `backend/watchlist/repository.py`
- Create: `backend/watchlist/service.py`
- Create: `backend/watchlist/api.py`
- Modify: `backend/main.py`
- Create: `tests/unit/test_watchlist_api.py`
- Create: `tests/unit/test_watchlist_service.py`

**Interfaces:**
- Produces: `list_watchlist(conn, user_id: str) -> list[dict]`.
- Produces: `add_watchlist_item(conn, ctx, symbol: str) -> dict`.
- Produces: `remove_watchlist_item(conn, ctx, symbol: str) -> dict`.
- Produces: the three `/api/v1/watchlist` routes with the standard `ok(...)` envelope.

- [ ] **Step 1: Write failing service tests**

Cover canonicalization, lookup and ownership:

```python
@pytest.mark.asyncio
async def test_add_resolves_server_canonical_metadata_and_is_idempotent() -> None:
    conn = WatchlistConn(instrument={
        "symbol": "600036", "asset_name": "招商银行", "asset_type": "STOCK"
    })
    ctx = TenantContext("user-1", "tenant-1", tenant_role="VIEWER")

    first = await service.add_watchlist_item(conn, ctx, " 600036 ")
    second = await service.add_watchlist_item(conn, ctx, "600036")

    assert first == second
    assert first["assetName"] == "招商银行"
    assert conn.insert_args[0] == "user-1"
    assert conn.insert_args[1:] == ("600036", "招商银行", "STOCK")


@pytest.mark.asyncio
async def test_add_rejects_symbol_missing_from_authoritative_master() -> None:
    with pytest.raises(ValidationError_, match="未通过主数据验证"):
        await service.add_watchlist_item(
            WatchlistConn(instrument=None), viewer_context(), "UNKNOWN"
        )
```

Also assert delete SQL contains `WHERE user_id=$1 AND symbol=$2`, missing delete raises `NotFoundError`, and audit actions/details use normalized data.

- [ ] **Step 2: Write failing API tests**

Use `create_app()` with dependency overrides. Assert unauthenticated 401, every tenant role gets 200, POST only accepts `{symbol}`, extra fields are rejected, DELETE encodes the path symbol, and service receives `ctx.user_id` indirectly through the context rather than a request-selected user.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py
```

Expected: import/route failures because the module is absent.

- [ ] **Step 4: Implement repository and service**

Repository lookup must resolve an active TICKER alias and map internal types:

```sql
SELECT alias.alias_value AS symbol, instrument.canonical_name AS asset_name,
       CASE instrument.asset_type
         WHEN 'CN_STOCK' THEN 'STOCK'
         WHEN 'EXCHANGE_FUND' THEN 'ETF'
         WHEN 'OFF_EXCHANGE_FUND' THEN 'FUND'
       END AS asset_type
FROM reference.instrument_aliases alias
JOIN reference.instruments instrument ON instrument.id = alias.instrument_id
WHERE alias.alias_type='TICKER'
  AND upper(alias.alias_value)=upper($1)
  AND instrument.lifecycle_status='ACTIVE'
  AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
ORDER BY alias.effective_from DESC
LIMIT 1
```

Insert with `ON CONFLICT (user_id, symbol) DO NOTHING`, then read the canonical row. Service normalizes the symbol, rejects empty/over-32 input, calls `write_audit` only when insertion/deletion changes a row, and maps snake_case rows to `{symbol, assetName, assetType, addedAt}`.

- [ ] **Step 5: Implement API and mount router**

```python
router = APIRouter(prefix="/watchlist", tags=["watchlist"])

class AddWatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=32)
```

Each endpoint opens `tenant_transaction(pool, ctx.user_id, ctx.tenant_id)`. Mount `watchlist_router` under the existing API prefix in `backend/main.py`.

- [ ] **Step 6: Verify GREEN, formatting and route registration**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py tests/unit/test_domain_api_expansion.py
.\.venv\Scripts\python.exe -m ruff format backend/watchlist tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py
.\.venv\Scripts\python.exe -m ruff check backend tests
```

- [ ] **Step 7: Commit and push**

```powershell
git add backend/watchlist backend/main.py tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py
git commit -m "feat(watchlist): expose canonical self-service API"
git push origin master
```

---

### Task 3: Feed the watchlist into user-aware report context

**Files:**
- Modify: `backend/reports/repository.py`
- Modify: `backend/reports/service.py`
- Create: `tests/unit/test_report_watchlist_context.py`

**Interfaces:**
- Produces: `repository.get_user_watchlist_context(conn, user_id) -> list[dict]`.
- Changes: `_build_context(conn, tenant_id, session, *, audience_user_id=None)`; omitted audience preserves tenant-wide legacy behavior.

- [ ] **Step 1: Write failing context tests**

```python
@pytest.mark.asyncio
async def test_report_context_reads_watchlist_for_explicit_audience(monkeypatch) -> None:
    watched = [{"symbol": "600036", "name": "招商银行", "type": "STOCK"}]
    monkeypatch.setattr(repository, "get_user_watchlist_context", AsyncMock(return_value=watched))

    context = await service._build_context(
        report_conn(), "tenant-1", "PRE_MARKET", audience_user_id="user-1"
    )

    assert context["watchlist"] == watched
    repository.get_user_watchlist_context.assert_awaited_once_with(ANY, "user-1")


@pytest.mark.asyncio
async def test_legacy_report_context_has_empty_watchlist_without_audience() -> None:
    context = await service._build_context(report_conn(), "tenant-1", "POST_MARKET")
    assert context["watchlist"] == []
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_watchlist_context.py
```

- [ ] **Step 3: Implement the minimal context query**

Select watchlist rows by user ID and left join latest market observations through the TICKER alias. Return `symbol/name/type/addedAt/instrumentId/price/changePct/quality/asOf`; preserve null values instead of inventing prices. Add `watchlist: []` to every context and only query when `audience_user_id` is present.

- [ ] **Step 4: Verify GREEN and existing report tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_watchlist_context.py tests/unit/test_pre_market_report.py tests/unit/test_personalized_report_push.py
```

- [ ] **Step 5: Commit and push**

```powershell
git add backend/reports/repository.py backend/reports/service.py tests/unit/test_report_watchlist_context.py
git commit -m "feat(reports): load user watchlist context"
git push origin master
```

---

### Task 4: Asset dashboard “我的关注” UI and Feature 1 release gate

**Files:**
- Create: `frontend/src/api/watchlist.ts`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/hooks.ts`
- Create: `frontend/src/features/dashboard/components/WatchlistPanel.tsx`
- Modify: `frontend/src/features/dashboard/DashboardPage.tsx`
- Modify: `frontend/src/index.css`
- Create: `frontend/src/features/dashboard/watchlist.test.tsx`
- Modify: `docs/10.WealthSail-MVP前后端实现与系统部署规范.md`

**Interfaces:**
- Produces: `WatchlistItem`, `watchlistApi.list/add/remove`, and query/mutation hooks.
- Produces: dashboard panel with server-backed search and mutation feedback.

- [ ] **Step 1: Write failing frontend tests**

Mock the existing search hook and new watchlist hooks. Cover:

```tsx
it("adds a canonical search result and refreshes my watchlist", async () => {
  renderWithSession(<DashboardPage />);
  await user.type(screen.getByLabelText("搜索关注标的"), "600036");
  await user.click(await screen.findByRole("button", { name: "关注 招商银行" }));
  expect(addWatchlist).toHaveBeenCalledWith({ symbol: "600036" });
});

it("removes an existing item without changing holdings", async () => {
  renderWithSession(<DashboardPage />);
  await user.click(screen.getByRole("button", { name: "取消关注 招商银行" }));
  expect(removeWatchlist).toHaveBeenCalledWith("600036");
});
```

Also test empty state, API error display and that no request contains `userId`, `assetName` or `assetType`.

- [ ] **Step 2: Run Vitest and verify RED**

```powershell
npm test -- --run src/features/dashboard/watchlist.test.tsx
```

- [ ] **Step 3: Implement API types and hooks**

```ts
export interface WatchlistItem {
  symbol: string;
  assetName: string;
  assetType: InstrumentType;
  addedAt: string;
}
```

Use private query key `("watchlist", "mine")`. Both mutations invalidate the watchlist query only after success; authentication failures flow through `onAuthFailure`.

- [ ] **Step 4: Implement the dashboard panel**

Use `useInstrumentSearch` with the existing 300 ms debounce and two-character minimum. The list remains visible while a mutation is pending. Disable only the symbol currently being added/removed. Place the panel after `SentimentCard` and before charts so it is visible for users without holdings.

- [ ] **Step 5: Verify Feature 1**

```powershell
npm test -- --run src/features/dashboard/watchlist.test.tsx src/features/dashboard/dashboard.test.tsx
npm run lint
npm run build
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_watchlist_migration.py tests/unit/test_watchlist_service.py tests/unit/test_watchlist_api.py tests/unit/test_report_watchlist_context.py
git diff --check
```

- [ ] **Step 6: Document and commit Feature 1**

Document migration 044, API bodies/responses, self-ownership and deployment order. Then:

```powershell
git add frontend/src docs/10.WealthSail-MVP前后端实现与系统部署规范.md
git commit -m "feat(dashboard): add my watchlist panel"
git push origin master
```

- [ ] **Step 7: Report Feature 1 outcome to the user**

State the commits, files, focused verification and that production must apply migration 044 before API/frontend deployment. Do not start Task 5 until this checkpoint message has been sent.

---

### Task 5: Migration 045 — report variants, audiences and macro cache

**Files:**
- Create: `migrations/045_split_pre_market_reports.sql`
- Modify: `scripts/migrate.py`
- Create: `tests/unit/test_split_pre_market_migration.py`
- Modify: `tests/unit/test_migration_runner.py`

**Interfaces:**
- Produces: `reporting.reports.report_variant`, `audience_user_id` and audience-aware uniqueness.
- Produces: append-only `reporting.shared_pre_market_briefs` keyed by trade date.
- Produces: RLS that permits shared reports or the authenticated audience only.

- [ ] **Step 1: Write failing migration tests**

Assert the migration:

```python
for token in (
    "ADD COLUMN report_variant TEXT NOT NULL DEFAULT 'STANDARD'",
    "ADD COLUMN audience_user_id UUID",
    "PRE_MARKET_MACRO",
    "PRE_MARKET_PERSONALIZED",
    "FOREIGN KEY (tenant_id, audience_user_id)",
    "CREATE UNIQUE INDEX uq_reports_variant_audience_version",
    "CREATE TABLE reporting.shared_pre_market_briefs",
    "protect_shared_pre_market_brief",
    "audience_user_id IS NULL",
    "audience_user_id = auth.uid()",
):
    assert token in sql
```

Readback must reject a missing variant check, missing composite FK, wrong unique index, mutable cache, browser write grants, or a policy that lets a member select another user's audience report.

- [ ] **Step 2: Run and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_split_pre_market_migration.py tests/unit/test_migration_runner.py::test_readback_requires_split_pre_market_boundary
```

- [ ] **Step 3: Implement migration 045**

Drop the old `reports_tenant_id_trade_date_report_session_version_key`, add columns/check/FK, and create:

```sql
CREATE UNIQUE INDEX uq_reports_variant_audience_version
ON reporting.reports (
    tenant_id, trade_date, report_session, report_variant,
    COALESCE(audience_user_id, '00000000-0000-0000-0000-000000000000'::uuid),
    version
);
```

Create the shared cache with JSONB draft/source context, SHA-256 context hash, quality, generated timestamp and an update/delete rejection trigger. Grant only SELECT/INSERT to API and Worker roles. Replace report/report-item browser SELECT policies so individual reports and child items are visible only when the parent audience is NULL or `auth.uid()`. Replace the corresponding `wealthsail_api` policies with the same predicate using `NULLIF(current_setting('app.user_id', true), '')::uuid`; do not leave the former tenant-wide API SELECT policy active.

- [ ] **Step 4: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_split_pre_market_migration.py tests/unit/test_migration_runner.py
.\.venv\Scripts\python.exe -m ruff check scripts tests
```

- [ ] **Step 5: Commit and push**

```powershell
git add migrations/045_split_pre_market_reports.sql scripts/migrate.py tests/unit/test_split_pre_market_migration.py tests/unit/test_migration_runner.py
git commit -m "feat(reports): add audience-aware pre-market variants"
git push origin master
```

---

### Task 6: Audience-aware report repository, API and audit UI

**Files:**
- Modify: `backend/reports/repository.py`
- Modify: `backend/reports/service.py`
- Modify: `backend/reports/api.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/features/reports/components/ReportTimeline.tsx`
- Modify: `frontend/src/features/reports/reports.test.tsx`
- Create: `tests/unit/test_report_audience_variants.py`

**Interfaces:**
- Changes: list/detail/history/latest/insert repository methods accept `actor_user_id`, `report_variant`, and `audience_user_id` where applicable.
- Produces: report DTO fields `variant` and `audienceUserId`.

- [ ] **Step 1: Write failing backend tests**

Assert list SQL groups by `(report_session, report_variant, audience_user_id)`, filters `(audience_user_id IS NULL OR audience_user_id=$actor)`, and returns both current user's macro/personalized rows. Assert detail/history use the same visibility predicate. Assert version 1 for Alice does not increment Bob's personalized version.

- [ ] **Step 2: Write failing frontend test**

Provide two PRE_MARKET fixtures and assert both labels and times are rendered:

```tsx
expect(screen.getByText("盘前简报·宏观")).toBeInTheDocument();
expect(screen.getByText("盘前简报·个性化")).toBeInTheDocument();
```

- [ ] **Step 3: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_audience_variants.py
npm test -- --run src/features/reports/reports.test.tsx
```

- [ ] **Step 4: Implement repository/service/API changes**

Make `STANDARD` and NULL audience defaults preserve every existing caller. Variant title mapping:

```python
REPORT_VARIANT_META = {
    "PRE_MARKET_MACRO": ("盘前简报·宏观", "隔夜市场与今日全球重点"),
    "PRE_MARKET_PERSONALIZED": ("盘前简报·个性化", "持仓、关注标的与今日操作提醒"),
}
```

Every public list/detail API passes `ctx.user_id`; unauthorized report IDs return 404, not 403, to avoid audience enumeration.

- [ ] **Step 5: Implement timeline labels and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_audience_variants.py tests/unit/test_report_integrity.py
npm test -- --run src/features/reports/reports.test.tsx
```

- [ ] **Step 6: Commit and push**

```powershell
git add backend/reports frontend/src/api/types.ts frontend/src/features/reports tests/unit/test_report_audience_variants.py
git commit -m "feat(reports): expose private pre-market report variants"
git push origin master
```

---

### Task 7: Daily shared 08:30 macro report

**Files:**
- Create: `backend/reports/pre_market_macro.py`
- Modify: `backend/integrations/market_data.py`
- Modify: `backend/reports/repository.py`
- Modify: `backend/reports/service.py`
- Create: `tests/unit/test_pre_market_macro.py`

**Interfaces:**
- Produces: `pre_market_macro.get_or_generate(pool, trade_date) -> SharedPreMarketBrief`.
- Produces: `generate_report(..., report_variant="PRE_MARKET_MACRO")` using cached macro draft and push content.

- [ ] **Step 1: Write failing deterministic content tests**

Cover fresh and missing data:

```python
def test_macro_report_has_only_the_two_required_sections() -> None:
    draft = build_report(source_context())
    assert [item["title"] for item in draft["items"]] == [
        "隔夜市场速览", "今日重点关注"
    ]


def test_macro_report_marks_unsupported_sources_as_missing() -> None:
    draft = build_report({})
    assert "暂无" in draft["summary"]
    assert draft["quality"] == "UNAVAILABLE"
```

- [ ] **Step 2: Write failing cache concurrency tests**

Use two concurrent `get_or_generate` calls and a fake transactional advisory lock. Assert the global snapshot method executes once, one row is inserted, and the second result has `cache_hit=True`.

- [ ] **Step 3: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_macro.py
```

- [ ] **Step 4: Implement append-only cache and report path**

Add `market_data.get_pre_market_global_snapshot()` with the stable shape below. It may query configured market providers for Dow Jones, Nasdaq, S&P 500 and A50, but must return null/empty facts with source quality `UNAVAILABLE` on timeout, parse failure or unsupported data; `economicEvents` and `globalEvents` remain empty until a governed provider exists.

```python
{
    "usIndices": [{"name": "道琼斯", "changePct": "0.25", "asOf": "..."}],
    "a50Future": {"name": "富时中国A50期货", "changePct": "-0.10", "asOf": "..."},
    "economicEvents": [],
    "globalEvents": [],
    "qualityStatus": "FRESH|ESTIMATED|STALE|UNAVAILABLE",
    "sourceName": "...",
}
```

Build from this governed result only. The model result is always `None`; provider/model audit strings are `deterministic/shared-pre-market-v1`. Persist a per-tenant `PRE_MARKET_MACRO` report with NULL audience and fan out to all PRE_MARKET-subscribed bindings.

- [ ] **Step 5: Verify GREEN and no model calls**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_macro.py tests/unit/test_personalized_report_push.py tests/unit/test_outbox_delivery.py
```

- [ ] **Step 6: Commit and push**

```powershell
git add backend/reports/pre_market_macro.py backend/integrations/market_data.py backend/reports/repository.py backend/reports/service.py tests/unit/test_pre_market_macro.py
git commit -m "feat(reports): cache daily pre-market macro brief"
git push origin master
```

---

### Task 8: 08:35 personalized holdings and watchlist report

**Files:**
- Create: `backend/reports/pre_market_personalized.py`
- Modify: `backend/reports/service.py`
- Modify: `backend/reports/repository.py`
- Create: `tests/unit/test_pre_market_personalized.py`

**Interfaces:**
- Produces: `generate_pre_market_personalized(pool, ctx, settings, *, job_run_id=None) -> dict | None`.
- Returns `None` only when active positions and watchlist are both empty.

- [ ] **Step 1: Write failing four-branch tests**

Parametrize holdings/watchlist combinations and assert only the empty/empty case skips. The three generated cases must have exactly:

```python
["持仓今日影响", "关注标的动态", "今日操作提醒"]
```

Assert no `chat_completion` call, target Outbox user equals the audience, and missing quote/action/cutoff facts render `暂无`.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_personalized.py
```

- [ ] **Step 3: Implement user-scoped context**

Call portfolio summary/positions with `AssetScope(kind="MINE", owner_user_id=ctx.user_id)`. Resolve the union of held and watched instrument IDs for company actions and cutoff rules. The report insert uses variant `PRE_MARKET_PERSONALIZED`, audience `ctx.user_id`, and `delivery_user_id=ctx.user_id`.

- [ ] **Step 4: Implement deterministic formatter**

Full audit summary contains the three numbered sections. The WeCom summary remains under 200 Chinese characters. Do not include global overnight content because it belongs to the 08:30 report.

- [ ] **Step 5: Verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_pre_market_personalized.py tests/unit/test_pre_market_report.py tests/unit/test_report_watchlist_context.py
```

- [ ] **Step 6: Commit and push**

```powershell
git add backend/reports/pre_market_personalized.py backend/reports/service.py backend/reports/repository.py tests/unit/test_pre_market_personalized.py
git commit -m "feat(reports): generate user pre-market watch briefs"
git push origin master
```

---

### Task 9: Schedule 08:30/08:35 phases with existing PRE_MARKET jobs

**Files:**
- Modify: `backend/jobs/scheduler.py`
- Modify: `backend/jobs/runner.py`
- Modify: `backend/debug/api.py`
- Modify: `backend/debug/service.py`
- Modify: `backend/worker.py`
- Modify: `tests/unit/test_scheduler_reliability.py`
- Modify: `tests/unit/test_backend_common.py`
- Modify: `tests/unit/test_debug_report_trigger.py`
- Create: `tests/unit/test_split_pre_market_runner.py`

**Interfaces:**
- Produces internal dispatch values `PRE_MARKET_MACRO` and `PRE_MARKET_PERSONALIZED`.
- Keeps database job lookup mapped to public `PRE_MARKET`.
- Keeps debug response `{reportId, outboxId}` and adds optional `phase=PERSONALIZED`.

- [ ] **Step 1: Write failing scheduler tests**

Assert exact Shanghai slots:

```python
assert due_at("PRE_MARKET_MACRO", "2026-09-02T08:30:05+08:00")
assert due_at("PRE_MARKET_PERSONALIZED", "2026-09-02T08:35:05+08:00")
assert not due_at("PRE_MARKET_PERSONALIZED", "2026-09-02T08:34:59+08:00")
```

Assert four public session labels remain unchanged and both phases skip exchange holidays.

- [ ] **Step 2: Write failing runner tests**

Macro phase queries existing PRE_MARKET jobs once per tenant. Personalized phase enumerates ACTIVE tenant members, isolates each member failure, and uses job keys containing phase and user UUID. The same tick/restart must return terminal skip rather than create duplicate reports. Add recovery tests proving `parse_business_job_key()` reconstructs macro versus personalized phase and the audience UUID from persisted keys, so a Worker restart never replays a personalized run as the legacy tenant-wide PRE_MARKET path.

- [ ] **Step 3: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_scheduler_reliability.py tests/unit/test_split_pre_market_runner.py tests/unit/test_debug_report_trigger.py
```

- [ ] **Step 4: Implement scheduler and runner mapping**

Extend `SessionSchedule` with `public_session`. For legacy sessions it equals `session`; both PRE_MARKET schedules set `public_session="PRE_MARKET"`. Use stable keys `job:<job-id>:<date>:PRE_MARKET_MACRO` and `job:<job-id>:<date>:PRE_MARKET_PERSONALIZED:<user-id>`, parsed by a strict helper during recovery. `run_session` routes macro once per tenant and personalized once per active member. Preserve lease heartbeat, CAS completion and bounded recovery; classify empty/empty as `SKIPPED:NO_PERSONAL_CONTEXT`.

- [ ] **Step 5: Extend controlled debug triggering**

`POST /debug/reports/trigger/PRE_MARKET` runs macro by default. `?phase=PERSONALIZED&user_id=<uuid>` runs the user path, returns 404 for an inactive/cross-tenant target, and returns `{reportId: null, outboxId: null}` for empty/empty without writing scheduler jobs. OWNER/ADMIN remains mandatory.

- [ ] **Step 6: Verify GREEN and recovery behavior**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_scheduler_reliability.py tests/unit/test_split_pre_market_runner.py tests/unit/test_debug_report_trigger.py tests/unit/test_runtime_monitoring.py tests/unit/test_worker_startup.py
```

- [ ] **Step 7: Commit and push**

```powershell
git add backend/jobs backend/debug backend/worker.py tests/unit/test_scheduler_reliability.py tests/unit/test_backend_common.py tests/unit/test_debug_report_trigger.py tests/unit/test_split_pre_market_runner.py
git commit -m "feat(scheduler): dispatch split pre-market reports"
git push origin master
```

---

### Task 10: Documentation, full verification and Feature 2 release gate

**Files:**
- Modify: `docs/10.WealthSail-MVP前后端实现与系统部署规范.md`
- Modify: `docs/20.WealthSail累计收益与标准报告任务设计及运维.md`
- Modify: `tests/smoke_api.py`
- Modify: `tests/e2e/test_four_sessions_workflow.py`

**Interfaces:**
- Documents migrations 044/045, the unchanged four-job invariant, two PRE_MARKET variants, privacy and operational checks.

- [ ] **Step 1: Update smoke/E2E contracts first**

Keep the public session set at four, but update PRE_MARKET list assertions to expect macro plus current-user personalized variants. Add a no-context fixture asserting only macro. Verify report-detail links resolve both IDs.

- [ ] **Step 2: Run smoke contract tests and fix any regression**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/e2e/test_four_sessions_workflow.py tests/unit/test_report_audience_variants.py
```

- [ ] **Step 3: Update operations documentation**

Document:

- execute 044 before watchlist API/frontend;
- execute 045 before split scheduler code;
- restart API and Worker and rebuild frontend;
- do not insert a fifth workflow job;
- verify 08:30 macro and 08:35 personalized job keys, reports and Outbox;
- verify another tenant member cannot request a personalized report ID;
- rollback is forward-only: pause the deployment and add a new migration rather than editing ledger rows.

- [ ] **Step 4: Run complete verification**

```powershell
.\.venv\Scripts\python.exe -m ruff format --check backend tests scripts
.\.venv\Scripts\python.exe -m ruff check backend tests scripts
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm test -- --run
npm run lint
npm run build
cd ..
git diff --check
```

If the repository-wide Ruff format baseline still reports unrelated legacy drift, run format check on every Python file changed by this plan and record the existing baseline separately; never reformat unrelated files in this feature.

- [ ] **Step 5: Request independent code review**

Review against the spec with emphasis on:

- watchlist self-ownership and canonical metadata;
- report audience leakage through list/detail/items/version history;
- shared cache concurrency;
- scheduler/job-run idempotency;
- subscription filtering for both phases;
- no model calls and no fifth workflow job.

Resolve every Critical/Important finding with a new failing test before committing.

- [ ] **Step 6: Commit documentation/final fixes and push**

```powershell
git add docs tests backend frontend scripts migrations
git commit -m "docs(reports): document split pre-market operations"
git push origin master
```

- [ ] **Step 7: Verify repository state and report Feature 2 outcome**

```powershell
git rev-parse HEAD
git rev-parse origin/master
git status --short
```

Require identical SHAs and an empty status. Report changed components, migrations, exact test counts and the production rollout order without exposing credentials.
