# Report Subscriptions and Debug Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four user-controlled report push subscriptions and an OWNER/ADMIN-only single-run report trigger that returns the generated report and queued Outbox identifiers.

**Architecture:** Extend the existing global `identity.user_preferences` row with four default-true booleans, expose them through identity APIs, and enforce them only while expanding report deliveries into Outbox rows. Add a separate debug router/service that forces one report version and targets one active tenant member without touching scheduler tables; the existing Worker remains responsible for delivery.

**Tech Stack:** PostgreSQL forward migrations and RLS, FastAPI/Pydantic, asyncpg, React 19, TanStack Query, TypeScript, Vitest/Testing Library, Pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-01-report-subscriptions-debug-trigger-design.md`

## Global Constraints

- `AUTH_DEV_MODE=true` remains rejected at startup in every environment.
- `OWNER/ADMIN/EDITOR/VIEWER` may update only their own subscriptions.
- Debug triggering requires the current tenant role `OWNER` or `ADMIN`.
- A disabled subscription suppresses only Outbox creation; report generation and audit persistence continue.
- Debug triggering never inserts, updates, leases, or deletes `workflow.jobs` or `workflow.job_runs`.
- Missing legacy `identity.user_preferences` rows behave as all subscriptions enabled.
- The debug endpoint targets only the caller or an ACTIVE member of the caller's current tenant.
- Existing report routes, report DTOs, scheduler behavior, Outbox payloads, links, and idempotency keys remain compatible.

---

### Task 1: Add the forward-only subscription schema contract

**Files:**
- Create: `migrations/043_user_report_subscriptions.sql`
- Create: `tests/unit/test_report_subscriptions_migration.py`
- Modify: `scripts/migrate.py`
- Modify: `tests/unit/test_migration_runner.py`
- Modify: `docs/11.数据库升级方法.md`

**Interfaces:**
- Consumes: existing `identity.user_preferences(user_id, default_tenant_id, default_asset_scope)` and its `wealthsail_api`-only RLS boundary.
- Produces: four `BOOLEAN NOT NULL DEFAULT TRUE` columns named `pre_market_report_enabled`, `intraday_report_enabled`, `fund_1430_report_enabled`, and `post_market_report_enabled`.

- [ ] **Step 1: Write migration contract tests that fail because migration 043 is absent**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_043_adds_default_true_report_subscription_columns_without_broad_grants():
    sql = (ROOT / "migrations/043_user_report_subscriptions.sql").read_text(
        encoding="utf-8"
    )
    for column in (
        "pre_market_report_enabled",
        "intraday_report_enabled",
        "fund_1430_report_enabled",
        "post_market_report_enabled",
    ):
        assert f"{column} BOOLEAN NOT NULL DEFAULT TRUE" in sql
    assert "GRANT" not in sql.upper()
    assert "ALTER TABLE identity.user_preferences ENABLE ROW LEVEL SECURITY" not in sql
```

Extend `tests/unit/test_migration_runner.py` so `discover_migrations` expects
`043_user_report_subscriptions.sql`, and assert `_readback` names all four columns plus
`wealthsail_api`, `authenticated`, and `PUBLIC` privilege checks.

- [ ] **Step 2: Run the migration tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_subscriptions_migration.py tests/unit/test_migration_runner.py
```

Expected: failure because migration 043 and its readback contract do not exist.

- [ ] **Step 3: Add migration 043**

```sql
BEGIN;

ALTER TABLE identity.user_preferences
    ADD COLUMN pre_market_report_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN intraday_report_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN fund_1430_report_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN post_market_report_enabled BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN identity.user_preferences.pre_market_report_enabled IS
    'Whether PRE_MARKET report deliveries are queued for this user.';
COMMENT ON COLUMN identity.user_preferences.intraday_report_enabled IS
    'Whether INTRADAY report deliveries are queued for this user.';
COMMENT ON COLUMN identity.user_preferences.fund_1430_report_enabled IS
    'Whether FUND_1430 report deliveries are queued for this user.';
COMMENT ON COLUMN identity.user_preferences.post_market_report_enabled IS
    'Whether POST_MARKET report deliveries are queued for this user.';

COMMIT;
```

Do not add grants: migration 032 already limits the table to `wealthsail_api`.

- [ ] **Step 4: Extend migration readback**

Add an `_readback` query over `information_schema.columns` and reject any column that is absent, nullable, or lacks a true default. Reuse `has_table_privilege` to require `wealthsail_api` SELECT/UPDATE and reject SELECT/UPDATE for `authenticated` and `PUBLIC`.

```python
subscription_columns = await conn.fetch(
    """
    SELECT column_name, is_nullable, column_default
      FROM information_schema.columns
     WHERE table_schema = 'identity'
       AND table_name = 'user_preferences'
       AND column_name = ANY($1::text[])
    """,
    list(REPORT_SUBSCRIPTION_COLUMNS),
)
```

- [ ] **Step 5: Document migration 043 and rerun GREEN**

Add migration 043 to the table and production sequence in `docs/11.数据库升级方法.md`.

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_subscriptions_migration.py tests/unit/test_migration_runner.py
.venv\Scripts\python.exe -m ruff check scripts/migrate.py tests/unit/test_report_subscriptions_migration.py tests/unit/test_migration_runner.py
```

Expected: all selected tests and Ruff pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add migrations/043_user_report_subscriptions.sql scripts/migrate.py tests/unit/test_report_subscriptions_migration.py tests/unit/test_migration_runner.py "docs/11.数据库升级方法.md"
git commit -m "feat(identity): add report subscription preferences"
git push origin master
```

---

### Task 2: Expose self-service subscription read and update APIs

**Files:**
- Create: `tests/unit/test_report_subscription_identity.py`
- Modify: `backend/identity/domain_service.py`
- Modify: `backend/identity/api.py`
- Modify: `tests/unit/test_identity_service.py`

**Interfaces:**
- Consumes: migration 043 columns and `IdentityService._write_audit`.
- Produces: `GET /auth/me.reportSubscriptions` and `PATCH /auth/report-subscriptions`; domain method `update_report_subscriptions(conn, actor, changes) -> dict[str, bool]`.

- [ ] **Step 1: Write failing identity service tests**

Create a fake async connection that records SQL and returns a row containing all four booleans. Test that `get_me` maps database snake-case columns to the four uppercase session keys, including true defaults when the left join yields null.

```python
assert result["reportSubscriptions"] == {
    "PRE_MARKET": True,
    "INTRADAY": False,
    "FUND_1430": True,
    "POST_MARKET": False,
}
```

Test the update method with
`TenantContext("viewer-1", "tenant-1", tenant_role="VIEWER")` and one change:

```python
result = await service.update_report_subscriptions(
    conn,
    actor,
    {"INTRADAY": False},
)
assert result["INTRADAY"] is False
assert audit["action"] == "REPORT_SUBSCRIPTIONS_UPDATED"
assert "user_id" not in audit["details"]
```

Test an empty change mapping raises `ValidationError` and no SQL executes.

- [ ] **Step 2: Run identity tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_subscription_identity.py tests/unit/test_identity_service.py
```

Expected: failures because the DTO fields and update method are missing.

- [ ] **Step 3: Extend `get_me` and add the update method**

Select the four columns from `identity.user_preferences`, then map null to true:

```python
"reportSubscriptions": {
    "PRE_MARKET": profile.get("pre_market_report_enabled") is not False,
    "INTRADAY": profile.get("intraday_report_enabled") is not False,
    "FUND_1430": profile.get("fund_1430_report_enabled") is not False,
    "POST_MARKET": profile.get("post_market_report_enabled") is not False,
}
```

Implement a fixed session-to-column map; never interpolate request-provided strings into SQL. Use an upsert so an anomalous missing preference row is repaired with default-true values, then apply only supplied changes with positional parameters and return all four columns. Audit the complete returned state.

```python
REPORT_SUBSCRIPTION_COLUMNS = {
    "PRE_MARKET": "pre_market_report_enabled",
    "INTRADAY": "intraday_report_enabled",
    "FUND_1430": "fund_1430_report_enabled",
    "POST_MARKET": "post_market_report_enabled",
}
```

- [ ] **Step 4: Add the PATCH route with strict validation**

```python
class ReportSubscriptionsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    PRE_MARKET: bool | None = None
    INTRADAY: bool | None = None
    FUND_1430: bool | None = None
    POST_MARKET: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提交一个研报订阅开关")
        return self
```

The route opens `tenant_transaction(pool, ctx.user_id, ctx.tenant_id)` and passes only `body.model_dump(exclude_unset=True)` to the domain service. Do not call `ctx.require_roles`; all active roles may update themselves.

- [ ] **Step 5: Run identity tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_subscription_identity.py tests/unit/test_identity_service.py
.venv\Scripts\python.exe -m ruff check backend/identity/api.py backend/identity/domain_service.py tests/unit/test_report_subscription_identity.py
```

Expected: all selected tests and Ruff pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/identity/api.py backend/identity/domain_service.py tests/unit/test_report_subscription_identity.py tests/unit/test_identity_service.py
git commit -m "feat(identity): expose report subscription settings"
git push origin master
```

---

### Task 3: Filter Outbox fan-out without suppressing reports

**Files:**
- Modify: `backend/reports/repository.py`
- Modify: `backend/reports/service.py`
- Modify: `tests/unit/test_outbox_delivery.py`
- Modify: `tests/unit/test_personalized_report_push.py`

**Interfaces:**
- Consumes: session preference columns from Task 1.
- Produces: `insert_report_outbox_deliveries(conn, *, tenant_id: str, report_id: str, session: str, version: int, trade_date: object, content: str | None = None, target_user_id: str | None = None) -> int`, `get_report_outbox_id(conn, tenant_id: str, report_id: str, user_id: str) -> str | None`, and `generate_report(pool, ctx, settings, session, *, job_run_id=None, actor_type="USER", force=False, delivery_user_id=None) -> dict`.

- [ ] **Step 1: Add failing Outbox behavior tests**

Extend the delivery fake to accept the new SQL arguments. Add table-driven tests for each session where two active bindings exist and one user's matching preference is false; assert exactly the enabled binding is inserted. Assert SQL uses `LEFT JOIN identity.user_preferences`, `COALESCE`, all four fixed columns, and a `CASE` driven by the validated session argument.

```python
@pytest.mark.parametrize(
    ("session", "disabled_column"),
    [
        ("PRE_MARKET", "pre_market_report_enabled"),
        ("INTRADAY", "intraday_report_enabled"),
        ("FUND_1430", "fund_1430_report_enabled"),
        ("POST_MARKET", "post_market_report_enabled"),
    ],
)
async def test_delivery_filters_only_the_matching_disabled_session(
    session: str,
    disabled_column: str,
) -> None:
    conn = _PreferenceDeliveryConnection(
        session=session,
        disabled_column=disabled_column,
        eligible_binding={"id": "binding-enabled", "user_id": "user-enabled"},
    )
    count = await insert_report_outbox_deliveries(
        conn,
        tenant_id="tenant-a",
        report_id="report-1",
        session=session,
        version=1,
        trade_date="2026-09-01",
    )
    assert count == 1
    assert conn.inserted[0][1] == "binding-enabled"
    assert disabled_column in conn.sql
```

Add tests that `target_user_id="user-2"` inserts only that user's binding and that a missing preference row remains eligible. Add a test for `get_report_outbox_id` returning the target user's row only.

- [ ] **Step 2: Run Outbox tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_outbox_delivery.py tests/unit/test_personalized_report_push.py
```

Expected: failures because subscriptions, target filtering, force mode, and Outbox ID lookup are absent.

- [ ] **Step 3: Implement subscription-aware binding selection**

Use one static query whose only dynamic input is a validated session value:

```sql
SELECT binding.id, binding.user_id
  FROM identity.channel_bindings binding
  LEFT JOIN identity.user_preferences preference
    ON preference.user_id = binding.user_id
 WHERE binding.tenant_id = $1
   AND binding.channel_type = 'wecom'
   AND binding.status = 'ACTIVE'
   AND ($3::uuid IS NULL OR binding.user_id = $3::uuid)
   AND CASE $2
       WHEN 'PRE_MARKET' THEN COALESCE(preference.pre_market_report_enabled, TRUE)
       WHEN 'INTRADAY' THEN COALESCE(preference.intraday_report_enabled, TRUE)
       WHEN 'FUND_1430' THEN COALESCE(preference.fund_1430_report_enabled, TRUE)
       WHEN 'POST_MARKET' THEN COALESCE(preference.post_market_report_enabled, TRUE)
       ELSE FALSE
   END
 ORDER BY binding.user_id, binding.id
```

Keep payloads and idempotency keys unchanged. Add a tenant-scoped lookup joining `workflow.outbox.channel_binding_id` to `identity.channel_bindings.id` and filtering tenant, report, and user.

- [ ] **Step 4: Add report force and target parameters**

Change the public function signature without changing existing callers:

```python
async def generate_report(
    pool,
    ctx,
    settings,
    session,
    *,
    job_run_id=None,
    actor_type="USER",
    force=False,
    delivery_user_id=None,
) -> dict:
```

Guard idempotent replay with `if latest is not None and not force`, pass `delivery_user_id` through `_persist_generation`, and then into `insert_report_outbox_deliveries(target_user_id=delivery_user_id)`. Existing route and Worker calls rely on defaults.

- [ ] **Step 5: Run Outbox and report tests GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_outbox_delivery.py tests/unit/test_personalized_report_push.py tests/unit/test_report_integrity.py
.venv\Scripts\python.exe -m ruff check backend/reports/repository.py backend/reports/service.py tests/unit/test_outbox_delivery.py
```

Expected: selected tests and Ruff pass; existing report generation still creates reports when delivery count is zero.

- [ ] **Step 6: Commit Task 3**

```powershell
git add backend/reports/repository.py backend/reports/service.py tests/unit/test_outbox_delivery.py tests/unit/test_personalized_report_push.py
git commit -m "feat(reports): honor per-user push subscriptions"
git push origin master
```

---

### Task 4: Add the OWNER/ADMIN single-run debug endpoint

**Files:**
- Create: `backend/debug/__init__.py`
- Create: `backend/debug/api.py`
- Create: `backend/debug/service.py`
- Create: `tests/unit/test_debug_report_trigger.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: Task 3 `generate_report(pool, ctx, settings, session, actor_type="USER", force=True, delivery_user_id=target_user_id)` and `get_report_outbox_id(conn, tenant_id, report_id, user_id)`. The separate `DEBUG_REPORT_TRIGGERED` action distinguishes this path because `audit.audit_log.actor_type` has a closed vocabulary.
- Produces: `POST /api/v1/debug/reports/trigger/{session}?user_id=<uuid>` returning `{reportId, outboxId}`.

- [ ] **Step 1: Write failing service and API tests**

Use an ASGI test app with dependency overrides for pool, tenant context, settings, and request ID. Parametrize roles:

```python
@pytest.mark.parametrize(
    ("role", "status"),
    [("OWNER", 200), ("ADMIN", 200), ("EDITOR", 403), ("VIEWER", 403)],
)
async def test_debug_trigger_role_boundary(role, status):
    app = build_debug_test_app(role=role)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/debug/reports/trigger/POST_MARKET")
    assert response.status_code == status
```

Test an unknown session returns the canonical WealthSail 400 validation envelope, omitted `user_id` passes the caller ID, an inactive/cross-tenant member returns 404, and a successful service call invokes:

```python
await generate_report(
    pool,
    ctx,
    settings,
    "POST_MARKET",
    actor_type="USER",
    force=True,
    delivery_user_id="target-user",
)
```

Assert the response contains exactly `reportId` and nullable `outboxId`. The service fake exposes only tenant membership, report generation, outbox lookup, and audit calls; no workflow job method exists, proving the code path has no scheduler dependency.

- [ ] **Step 2: Run debug tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_debug_report_trigger.py
```

Expected: import or route failures because the debug module is absent.

- [ ] **Step 3: Implement the debug service**

```python
async def trigger_report_once(pool, ctx, settings, session, target_user_id=None):
    ctx.require_roles("OWNER", "ADMIN")
    if session not in repository.REPORT_SESSIONS:
        raise ValidationError_(
            "未知报告时段",
            field_errors=[{"field": "session", "message": session}],
        )
    target = target_user_id or ctx.user_id
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        member = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM identity.tenant_members
                 WHERE tenant_id=$1 AND user_id=$2 AND status='ACTIVE'
            )
            """,
            ctx.tenant_id,
            target,
        )
    if not member:
        raise NotFoundError("目标用户不是当前租户的活动成员")
    report = await report_service.generate_report(
        pool,
        ctx,
        settings,
        session,
        actor_type="USER",
        force=True,
        delivery_user_id=target,
    )
    async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
        outbox_id = await repository.get_report_outbox_id(
            conn, ctx.tenant_id, report["id"], target
        )
        await write_audit(
            conn,
            tenant_id=ctx.tenant_id,
            actor_type="USER",
            actor_id=ctx.user_id,
            action="DEBUG_REPORT_TRIGGERED",
            resource_type="reporting.reports",
            resource_id=report["id"],
            details={"session": session, "targetUserId": target, "outboxId": outbox_id},
        )
    return {"reportId": report["id"], "outboxId": outbox_id}
```

- [ ] **Step 4: Add and register the router**

Use `Literal["PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"]` for the path parameter and `UUID | None` for the optional `user_id` query parameter. Register the router in `backend/main.py` under the existing API prefix. Do not import `backend.jobs` from the debug package.

- [ ] **Step 5: Run debug and main app tests GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_debug_report_trigger.py tests/unit/test_backend_common.py
.venv\Scripts\python.exe -m ruff check backend/debug backend/main.py tests/unit/test_debug_report_trigger.py
```

Expected: selected tests and Ruff pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add backend/debug backend/main.py tests/unit/test_debug_report_trigger.py
git commit -m "feat(debug): add controlled one-shot report trigger"
git push origin master
```

---

### Task 5: Add four subscription switches to Account Security

**Files:**
- Create: `frontend/src/features/settings/components/ReportSubscriptionCard.tsx`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/identity.ts`
- Modify: `frontend/src/api/hooks.ts`
- Modify: `frontend/src/features/settings/SettingsPage.tsx`
- Modify: `frontend/src/features/settings/settings.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: Task 2 `MeResponse.reportSubscriptions` and PATCH response.
- Produces: `ReportSubscriptions`, `ReportSubscriptionUpdate`, `updateReportSubscriptions`, `useUpdateReportSubscriptions`, and a settings card available to every tenant role.

- [ ] **Step 1: Add failing frontend interaction tests**

Extend the mocked hooks with `useUpdateReportSubscriptions`. Assert all four labels render for an OWNER and again after setting `meFixture.activeRole = "VIEWER"`:

```typescript
expect(screen.getByRole("switch", { name: "盘前简报" })).toBeChecked();
expect(screen.getByRole("switch", { name: "盘中跟踪" })).toBeChecked();
expect(screen.getByRole("switch", { name: "基金 2:30 报告" })).toBeChecked();
expect(screen.getByRole("switch", { name: "盘后复盘" })).toBeChecked();
```

Click the INTRADAY switch and assert the mutation receives only `{ INTRADAY: false }`. Reject the mutation in a separate test and assert the switch returns to checked and an alert contains the backend error. Assert no button or heading named “调试触发” appears.

- [ ] **Step 2: Run frontend tests and verify RED**

Run:

```powershell
npm test -- --run frontend/src/features/settings/settings.test.tsx
```

Run from `frontend/`. Expected: missing type, hook, card, or switch assertions fail.

- [ ] **Step 3: Add TypeScript DTO and client call**

```typescript
export type ReportSubscriptions = Record<ReportSession, boolean>;
export type ReportSubscriptionUpdate = Partial<ReportSubscriptions>;

export interface MeResponse {
  reportSubscriptions: ReportSubscriptions;
}
```

```typescript
export async function updateReportSubscriptions(
  input: ReportSubscriptionUpdate,
): Promise<ReportSubscriptions> {
  const envelope = await request<ReportSubscriptions>(
    "/v1/auth/report-subscriptions",
    { method: "PATCH", body: JSON.stringify(input) },
  );
  return envelope.data;
}
```

The mutation updates the private `identity/me` query cache with the complete returned state and calls the shared 401 handler on failure.

- [ ] **Step 4: Implement the card and page integration**

Define a constant four-row label list. The card owns temporary state initialized from props and synchronizes it when props change. For each switch, store the previous state, optimistically toggle, await `mutateAsync({ [session]: checked })`, replace state with the server response, and restore the previous state on failure. Disable only while the mutation is pending and render a `role="alert"` error.

```tsx
<input
  type="checkbox"
  role="switch"
  aria-label={label}
  checked={values[session]}
  disabled={mutation.isPending}
  onChange={(event) => void change(session, event.currentTarget.checked)}
/>
```

Render `ReportSubscriptionCard` in the main settings column whenever `me.data` exists, without checking the tenant role. Add scoped CSS matching existing panel and form controls; do not modify unrelated page layout.

- [ ] **Step 5: Run frontend tests, lint, and build GREEN**

Run from `frontend/`:

```powershell
npm test -- --run src/features/settings/settings.test.tsx
npm run lint
npm run build
```

Expected: Vitest, ESLint, TypeScript, and Vite build pass.

- [ ] **Step 6: Commit Task 5**

```powershell
git add frontend/src/api/types.ts frontend/src/api/identity.ts frontend/src/api/hooks.ts frontend/src/features/settings/SettingsPage.tsx frontend/src/features/settings/components/ReportSubscriptionCard.tsx frontend/src/features/settings/settings.test.tsx frontend/src/index.css
git commit -m "feat(settings): add report subscription switches"
git push origin master
```

---

### Task 6: Update operational documentation and perform final verification

**Files:**
- Modify: `docs/10.WealthSail-MVP前后端实现与系统部署规范.md`
- Modify: `docs/20.WealthSail累计收益与标准报告任务设计及运维.md`
- Verify: all files changed by Tasks 1-5

**Interfaces:**
- Consumes: completed API, migration, UI, and debug contracts.
- Produces: deployment steps, API examples, expected no-delivery behavior, and verified release commit.

- [ ] **Step 1: Document user and developer workflows**

Document:

```text
PATCH /api/v1/auth/report-subscriptions
{"FUND_1430": false}

POST /api/v1/debug/reports/trigger/POST_MARKET
POST /api/v1/debug/reports/trigger/POST_MARKET?user_id=<active-member-uuid>
```

State that `outboxId=null` means the report was generated but the target subscription/binding produced no delivery. State that deployment requires migration 043, API/Worker restart, and frontend rebuild; no report job backfill is required.

- [ ] **Step 2: Run focused backend verification**

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_report_subscriptions_migration.py tests/unit/test_report_subscription_identity.py tests/unit/test_outbox_delivery.py tests/unit/test_debug_report_trigger.py
.venv\Scripts\python.exe -m ruff check backend tests scripts
.venv\Scripts\python.exe -m ruff format --check backend tests scripts
```

Expected: all focused tests and Ruff checks pass.

- [ ] **Step 3: Run complete backend verification**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: zero failures.

- [ ] **Step 4: Run complete frontend verification**

Run from `frontend/`:

```powershell
npm test -- --run
npm run lint
npm run build
```

Expected: zero Vitest/ESLint/TypeScript/Vite failures.

- [ ] **Step 5: Inspect the release diff**

```powershell
git diff --check
git status --short
git diff --stat HEAD~5..HEAD
```

Verify no secrets, generated `frontend/dist` assets, migration ledger edits, scheduler job writes, `AUTH_DEV_MODE` relaxation, or unrelated changes are present.

- [ ] **Step 6: Request independent code review**

Provide the reviewer with the spec path, plan path, base SHA `8cfe1e1`, current HEAD, and the explicit security/delivery constraints. Fix every Critical or Important finding and rerun the affected focused tests.

- [ ] **Step 7: Run fresh verification after review fixes**

Repeat Steps 2-5 after the last code change. Completion claims must quote the final test counts and build exit status.

- [ ] **Step 8: Commit documentation/fixes and push master**

```powershell
git add docs backend frontend/src migrations scripts tests
git commit -m "docs(reports): document subscriptions and debug triggering"
git push origin master
```

Expected: `origin/master` points to the final local HEAD and `git status --short` is empty.
