# WealthSail Tiantian Fund Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved local Tiantian Fund capture, ZIP preview/confirmation import, source-account ledger reconciliation, historical NAV backfill, holding-period metrics, and Web presentation end to end.

**Architecture:** A local Playwright collector creates a versioned, hashed ZIP without credentials. FastAPI parses the package into a DRAFT, confirms normalized source facts and per-fund snapshot baselines transactionally, then a persistent Worker job backfills shared NAV observations and materializes per-position metrics. React provides a recoverable upload/preview/confirm wizard and compact expandable metrics display.

**Tech Stack:** Node.js 22+, Playwright 1.62, Node test runner, Python 3.12, FastAPI 0.115, Pydantic 2.10, asyncpg 0.30, PostgreSQL 16, pytest, React 19, TypeScript 5.9, TanStack Query 5, Vitest 4, Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-27-wealthsail-tiantian-fund-import-and-range-metrics-design.md`

## Global Constraints

- Implement on `master`; the user explicitly approved direct work on this branch.
- Use current holdings as the authoritative baseline; historical source rows never become BUY/SELL/DIVIDEND ledger events.
- Preserve Decimal/NUMERIC precision end to end; financial API fields are decimal strings.
- ZIP limits: 20 MB compressed, 100 MB expanded, 20 entries, 20,000 source rows, 1,000 position rows.
- Store no Tiantian password, Cookie, access token, complete bank number, or raw browser storage in the package, logs, tests, or Git.
- All new tenant tables use same-tenant foreign keys, FORCE RLS, minimum API/Worker grants, and indexed tenant access paths.
- Existing MANUAL/CSV import contracts remain backward compatible.
- Follow strict RED → verify failure → GREEN → verify pass → refactor for every production behavior.
- Use synthetic fixtures only. `tools/tiantian-fund-importer/private-data/` remains ignored.
- Commit verified slices with descriptive messages; run the full repository verification before the final commit and push.

---

## File Map

### Collector

- `tools/tiantian-fund-importer/src/holdings.mjs`: normalize current holdings and validate snapshot completeness.
- `tools/tiantian-fund-importer/src/browser-holdings.mjs`: capture a structured holdings response from the authenticated holdings page.
- `tools/tiantian-fund-importer/src/import-package.mjs`: construct the v2 upload files and ZIP with SHA-256 manifest.
- `tools/tiantian-fund-importer/src/domain.mjs`: normalize source transaction business types and holding-period share effects.
- `tools/tiantian-fund-importer/src/browser-session.mjs`: expose both transaction and holding request paths from one local session.
- `tools/tiantian-fund-importer/src/output.mjs`: preserve local diagnostic output and delegate v2 upload-package creation.
- `tools/tiantian-fund-importer/src/cli.mjs`: orchestrate holdings plus history and print the ZIP path.
- `tools/tiantian-fund-importer/test/*.test.mjs`: behavior tests with synthetic provider payloads.

### Backend and database

- `backend/portfolio/imports/package.py`: bounded ZIP reader, manifest/hash/schema validation.
- `backend/portfolio/imports/tiantian.py`: Pydantic source models and normalized records.
- `backend/portfolio/imports/analytics.py`: holding-start and NAV-range pure Decimal algorithms.
- `backend/portfolio/imports/repository.py`: source binding, staging, compare, confirm, cancel, and list queries.
- `backend/portfolio/imports/service.py`: authorization, idempotency, preview/confirm orchestration, and DTO rendering.
- `backend/portfolio/api.py`: multipart preview, batch reads, confirm/cancel/retry routes.
- `backend/portfolio/repository.py`: join latest period metrics into current holdings.
- `backend/portfolio/service.py`: render nested holding/range metric DTOs.
- `backend/integrations/market_data.py`: paginated official historical fund NAV fetch.
- `backend/jobs/data_tasks.py`: persistent NAV backfill and metric-rebuild job runner.
- `backend/worker.py`: poll data tasks in addition to the existing scheduler/outbox loops.
- `backend/config.py`: feature flag and upload/job bounds.
- `migrations/021_tiantian_fund_import_and_position_metrics.sql`: schema, constraints, RLS, grants, indexes, triggers.
- `scripts/migrate.py`: 021 readback checks.

### Frontend

- `frontend/src/api/types.ts`: import batch and nested position metric DTOs.
- `frontend/src/api/portfolio.ts`: multipart preview, batch reads, confirm/cancel/retry calls.
- `frontend/src/api/hooks.ts`: tenant-scoped queries, mutations, polling, and invalidation.
- `frontend/src/features/onboarding/OnboardingPage.tsx`: source selector and import history layout.
- `frontend/src/features/onboarding/components/TiantianImportWizard.tsx`: wizard state boundary.
- `frontend/src/features/onboarding/components/ImportPackageStep.tsx`: account/file selection and upload.
- `frontend/src/features/onboarding/components/ImportPreviewStep.tsx`: summary/tabs/problems.
- `frontend/src/features/onboarding/components/ImportConfirmStep.tsx`: explicit semantics confirmation.
- `frontend/src/features/onboarding/components/ImportResultStep.tsx`: ledger/metrics outcome split.
- `frontend/src/features/onboarding/components/ImportHistory.tsx`: recent batch list.
- `frontend/src/features/dashboard/components/FundRangeMetrics.tsx`: expanded official-NAV details.
- `frontend/src/features/dashboard/components/HoldingsTable.tsx`: compact duration and range position columns.
- `frontend/src/features/dashboard/DashboardPage.tsx`: repeatable import entry copy.
- `frontend/src/index.css`: wizard, status, expanded metrics, and responsive styles.

### Tests and documentation

- `tests/unit/test_tiantian_import_package.py`: ZIP parser/security.
- `tests/unit/test_tiantian_import_analytics.py`: holding start and NAV range.
- `tests/unit/test_tiantian_import_service.py`: preview/confirm authorization and orchestration.
- `tests/unit/test_tiantian_import_repository.py`: SQL command semantics through deterministic fake connections.
- `tests/unit/test_tiantian_nav_jobs.py`: persistent retryable background work.
- `tests/unit/test_migration_runner.py`: 021 readback contract.
- `frontend/src/features/onboarding/tiantian-import.test.tsx`: wizard behaviors.
- `frontend/src/features/dashboard/fund-range-metrics.test.tsx`: metric states and accessibility.
- `docs/08.WealthSail需求设计测试追溯矩阵.md`: TT requirements traceability.
- `docs/10.WealthSail-MVP前后端实现与系统部署规范.md`: runtime variables and deployment.
- `docs/11.数据库升级方法.md`: exact 021 migration/readback commands.
- `ai-review/<timestamp>.天天基金导入实现修改报告.md`: delivered changes, reasons, evidence, deployment notes.

---

### Task 1: Collector v2 holdings and ZIP package

**Files:**
- Create: `tools/tiantian-fund-importer/src/holdings.mjs`
- Create: `tools/tiantian-fund-importer/src/browser-holdings.mjs`
- Create: `tools/tiantian-fund-importer/src/import-package.mjs`
- Create: `tools/tiantian-fund-importer/test/holdings.test.mjs`
- Create: `tools/tiantian-fund-importer/test/import-package.test.mjs`
- Modify: `tools/tiantian-fund-importer/src/domain.mjs`
- Modify: `tools/tiantian-fund-importer/src/browser-session.mjs`
- Modify: `tools/tiantian-fund-importer/src/output.mjs`
- Modify: `tools/tiantian-fund-importer/src/cli.mjs`
- Modify: `tools/tiantian-fund-importer/package.json`
- Modify: `tools/tiantian-fund-importer/pnpm-lock.yaml`
- Modify: `tools/tiantian-fund-importer/README.md`

**Interfaces:**
- Produces: `normalizeHolding(raw, capturedAt)`, `validateHoldingSnapshot(rows, summary)`, `captureHoldings(page)`, `buildImportPackage(input)`, and a ZIP containing the five files from spec §10.
- Produces normalized transaction `businessType` values consumed by the backend parser.

- [ ] **Step 1: Write failing holding normalization tests**

```javascript
test("normalizes a six-digit current fund holding without leaking the bank account", () => {
  assert.deepEqual(normalizeHolding({
    fundCode: "110022", fundName: "易方达消费行业股票",
    holdShare: "1234.56", costAmount: "2500.00", marketValue: "2700.00",
    nav: "2.1876", navDate: "2026-08-27", bankCard: "6222000012345678"
  }, "2026-08-28T00:00:00+08:00"), {
    productCode: "110022", productName: "易方达消费行业股票",
    quantity: "1234.56", availableQuantity: null, averageCost: "2.02501377",
    costAmount: "2500.00", marketValue: "2700.00", latestNav: "2.1876",
    latestNavDate: "2026-08-27", accountMask: "****5678",
    capturedAt: "2026-08-28T00:00:00+08:00"
  });
});
```

The mutation this catches is trusting provider field spelling/number formatting or leaking the full bank account.

- [ ] **Step 2: Run the test and verify RED**

Run: `pnpm test -- test/holdings.test.mjs`

Expected: FAIL because `src/holdings.mjs` does not exist.

- [ ] **Step 3: Implement holding normalization and completeness validation**

Implement explicit provider-key aliases, Decimal-safe string division without binary floating point, six-digit code checks, masked account rendering, duplicate-code rejection, count reconciliation, and `COMPLETE/PARTIAL` output. Use integer-scaled decimal helpers in Node rather than `Number` for cost/share division.

- [ ] **Step 4: Verify holding tests GREEN**

Run: `pnpm test -- test/holdings.test.mjs`

Expected: all holding normalization, missing cost, duplicate code, and incomplete summary cases pass.

- [ ] **Step 5: Write failing ZIP contract tests**

```javascript
test("writes the exact v2 upload members and hashes their final bytes", async () => {
  const result = await buildImportPackage(syntheticInput, tempDir);
  assert.equal(result.manifest.schemaVersion, "wealthsail-tiantian-import/v2");
  assert.deepEqual(result.manifest.files.map((f) => f.name).sort(), [
    "current-positions.json", "excluded-summary.json", "fund-transactions.json",
    "review-required.json"
  ]);
  assert.match(result.zipPath, /wealthsail-tiantian-20260828\.zip$/);
});
```

The mutation this catches is packaging local raw/cash-management files or hashing different bytes from those archived.

- [ ] **Step 6: Run the ZIP test and verify RED**

Run: `pnpm test -- test/import-package.test.mjs`

Expected: FAIL because `buildImportPackage` is missing.

- [ ] **Step 7: Implement deterministic JSON files, manifest, SHA-256, and ZIP output**

Use `archiver` only for ZIP container writing. Hash the UTF-8 bytes passed to the archive, whitelist the five member names, and exclude cash-management detail rows. Generate/read a random stable UUID from ignored private configuration without deriving it from PII.

- [ ] **Step 8: Add browser capture and CLI orchestration tests, then implementation**

Test `captureHoldings(page)` against a fake page that emits a complete structured JSON response containing a six-digit code and holdings fields. It must reject a login redirect and a response whose declared total differs from extracted rows. Then expose `requestHoldings` from `openAuthenticatedSession`, collect holdings before transactions, and pass both into package output.

- [ ] **Step 9: Run collector regression suite**

Run: `pnpm test`

Expected: existing v1 local diagnostic output tests and new v2 upload package tests pass.

- [ ] **Step 10: Commit the collector slice**

```powershell
git add tools/tiantian-fund-importer
git commit -m "feat(importer): capture fund holdings and build v2 package"
```

---

### Task 2: Backend bounded ZIP parser and normalized package models

**Files:**
- Create: `backend/portfolio/imports/__init__.py`
- Create: `backend/portfolio/imports/package.py`
- Create: `backend/portfolio/imports/tiantian.py`
- Create: `tests/unit/test_tiantian_import_package.py`
- Modify: `backend/config.py`

**Interfaces:**
- Produces: `parse_tiantian_package(path: Path, limits: ImportLimits) -> TiantianPackage`.
- `TiantianPackage` exposes `manifest`, `positions`, `transactions`, `review_required`, and `excluded_summary` as immutable validated models.

- [ ] **Step 1: Write a failing valid-package test using Python `zipfile`**

```python
def test_parser_returns_decimal_source_models_for_a_valid_v2_package(tmp_path):
    package_path = write_synthetic_package(tmp_path, quantity="1234.56")
    parsed = parse_tiantian_package(package_path, ImportLimits())
    assert parsed.positions[0].product_code == "110022"
    assert parsed.positions[0].quantity == Decimal("1234.56")
```

The mutation this catches is returning unchecked dicts or converting financial values through float.

- [ ] **Step 2: Run and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_tiantian_import_package.py -q`

Expected: FAIL because the imports package is absent.

- [ ] **Step 3: Implement validated source models and bounded parser**

Use `ZipFile.infolist()` to reject encrypted entries, directories, non-whitelisted names, absolute/parent paths, symlinks, duplicate members, oversize totals, excessive compression ratios, hash mismatches, invalid UTF-8, unsupported schema, and record-count mismatches before constructing Pydantic models.

- [ ] **Step 4: Add one failing test per security branch before implementing it**

Cover missing manifest, unexpected member, duplicate member, `../`, backslash traversal, symlink mode bits, encrypted flag, expanded size, ratio, hash mismatch, malformed UTF-8, 20,001 transactions, and 1,001 positions. Each fixture changes one package property and asserts `ValidationError_` with a stable field error.

- [ ] **Step 5: Run package parser tests and backend regression subset**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_tiantian_import_package.py tests/unit/test_backend_common.py -q`

Expected: all pass.

- [ ] **Step 6: Commit the parser slice**

```powershell
git add backend/config.py backend/portfolio/imports tests/unit/test_tiantian_import_package.py
git commit -m "feat(portfolio): validate Tiantian import packages"
```

---

### Task 3: Holding-period and NAV-range pure algorithms

**Files:**
- Create: `backend/portfolio/imports/analytics.py`
- Create: `tests/unit/test_tiantian_import_analytics.py`

**Interfaces:**
- Produces: `derive_holding_start(transactions, current_quantity, *, as_of, tolerance_ratio=Decimal("0.000001")) -> HoldingStartResult`.
- Produces: `calculate_range_metrics(observations, *, holding_started_on) -> RangeMetricsResult`.

- [ ] **Step 1: Write RED table tests for holding episodes**

Use hand-derived literal cases for first purchase, multiple buys/partial redemption, full exit/re-entry, cash dividend, reinvestment, partial history, mismatch, same-day ambiguity, and the `max(0.01, quantity*0.000001)` tolerance.

```python
def test_full_exit_then_repurchase_starts_a_new_holding_episode():
    result = derive_holding_start(events(
        ("2025-01-01", "PURCHASE", "100"),
        ("2025-03-01", "REDEEM", "100"),
        ("2025-06-10", "PURCHASE", "40"),
    ), Decimal("40"), as_of=date(2026, 8, 28))
    assert result.started_on == date(2025, 6, 10)
    assert result.status == "RECONCILED"
```

- [ ] **Step 2: Verify RED, implement minimum replay, verify GREEN**

Run the individual test first; implement immutable dataclasses and Decimal replay; run the full analytics file.

- [ ] **Step 3: Write RED literal tests for NAV range**

Cover most-recent repeated high/low, current at high/low, flat range, zero/negative NAV exclusion, missing interval, and quality reasons. Expected percentages are literal Decimal values, not calculated with production helpers.

- [ ] **Step 4: Verify RED, implement range calculation, verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_tiantian_import_analytics.py -q`

Expected: all cases pass with no float values.

- [ ] **Step 5: Commit algorithm slice**

```powershell
git add backend/portfolio/imports/analytics.py tests/unit/test_tiantian_import_analytics.py
git commit -m "feat(portfolio): derive holding periods and NAV ranges"
```

---

### Task 4: Migration 021, readback, RLS, and compatibility

**Files:**
- Create: `migrations/021_tiantian_fund_import_and_position_metrics.sql`
- Modify: `scripts/migrate.py`
- Modify: `tests/unit/test_migration_runner.py`
- Create: `tests/unit/test_tiantian_import_schema_contract.py`

**Interfaces:**
- Produces the tables/columns/constraints from spec §11 and grants required by Tasks 5–7.
- Preserves legacy MANUAL import item constraints and confirmation paths.

- [ ] **Step 1: Write failing migration discovery/readback tests**

Assert version 21 is discovered after 20 and readback queries require all four new tables, `TIANTIAN_FUND_ZIP`, `POSITION_SNAPSHOT`, API grants, Worker grants, FORCE RLS, and the two background job types.

- [ ] **Step 2: Verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_migration_runner.py tests/unit/test_tiantian_import_schema_contract.py -q`

Expected: FAIL because migration 021 and readback checks are absent.

- [ ] **Step 3: Implement additive migration 021**

Create `portfolio_source_bindings`, `portfolio_import_source_records`, `portfolio_source_transactions`, and `position_period_metrics`; extend import batches/items and job runs; replace affected CHECK constraints; update the confirmation-protection trigger so actionable snapshot items require the correct effect while UNCHANGED items do not; add composite tenant foreign keys, partial/unique indexes, RLS policies, comments, grants, and append-only guards.

- [ ] **Step 4: Extend migration readback**

Read back exact columns, constraints, FORCE RLS flags, API/Worker privileges, and absence of browser direct writes. Raise a named runtime error for each missing contract.

- [ ] **Step 5: Run unit contracts and local migration when database is available**

Run unit tests above, then run `scripts/migrate.py --dry-run` against the configured development DSN. If local PostgreSQL is running, migrate an isolated development database and run readback; do not target production during development.

- [ ] **Step 6: Commit migration slice**

```powershell
git add migrations/021_tiantian_fund_import_and_position_metrics.sql scripts/migrate.py tests/unit/test_migration_runner.py tests/unit/test_tiantian_import_schema_contract.py
git commit -m "feat(db): add source imports and position metrics schema"
```

---

### Task 5: Preview persistence, comparison, and batch reads

**Files:**
- Create: `backend/portfolio/imports/repository.py`
- Create: `backend/portfolio/imports/service.py`
- Create: `tests/unit/test_tiantian_import_service.py`
- Create: `tests/unit/test_tiantian_import_repository.py`

**Interfaces:**
- Produces: `preview_tiantian_import(pool, ctx, upload, account_id, account_name, idempotency_key) -> dict`.
- Produces: `get_import_batch(conn, tenant_id, batch_id)`, `list_import_batches(...)`.
- Uses Task 2 parser and Task 4 schema.

- [ ] **Step 1: Write RED authorization and no-ledger preview tests**

Test OWNER/ADMIN/EDITOR accepted, VIEWER rejected before file parsing, valid preview returns DRAFT summary, parser failure leaves no batch, and preview repository never inserts `ledger_events`.

- [ ] **Step 2: Verify RED and implement authorization/idempotency shell**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_tiantian_import_service.py -q`

- [ ] **Step 3: Write RED comparison tests**

Use literal source/current rows to assert NEW, UNCHANGED, CHANGED, CLOSED, and CONFLICT. Assert older `source_as_of`, active binding to a different account, same external ID/different hash, invalid cost, and unresolved code block confirmation while partial history only warns.

- [ ] **Step 4: Implement preview transaction**

Resolve/create target account, resolve standard fund instruments without trusting names, create/reuse PENDING/ACTIVE binding, compute active-account fact hash, insert DRAFT/items/staging rows, and write count-only audit. Parse the ZIP outside the database transaction and delete the temporary file in `finally`.

- [ ] **Step 5: Implement batch read/list DTOs and run tests**

Run both new backend test files plus existing portfolio import command tests.

- [ ] **Step 6: Commit preview slice**

```powershell
git add backend/portfolio/imports tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py
git commit -m "feat(portfolio): preview Tiantian import batches"
```

---

### Task 6: Transactional confirm, cancellation, and per-fund baseline replacement

**Files:**
- Modify: `backend/portfolio/imports/repository.py`
- Modify: `backend/portfolio/imports/service.py`
- Modify: `tests/unit/test_tiantian_import_service.py`
- Modify: `tests/unit/test_tiantian_import_repository.py`
- Modify: `tests/unit/test_projection_integrity.py`

**Interfaces:**
- Produces: `confirm_tiantian_import(..., expected_version, decisions, idempotency_key) -> dict`.
- Produces: `cancel_tiantian_import(...) -> dict` and a durable data-job row.

- [ ] **Step 1: Write RED first-import semantics test**

Assert one positive POSITION_SNAPSHOT produces one ADJUSTMENT with only an INSTRUMENT leg, imports source history, activates the binding, creates one PENDING job, and never emits BUY/SELL/DIVIDEND.

- [ ] **Step 2: Verify RED, implement first confirm, verify GREEN**

Lock batch and binding, recompute account fact hash, merge source rows with conflict detection, emit event effects, update version/status, rebuild projections, audit counts, and insert the job in one transaction.

- [ ] **Step 3: Write RED replacement cases**

Test CHANGED emits REVERSAL for the previous active baseline plus a new ADJUSTMENT; CLOSED emits only REVERSAL; UNCHANGED emits neither; no-change/new-history confirms without ledger effects; an empty existing snapshot closes all positions only with `confirmEmpty=true`.

- [ ] **Step 4: Implement replacement cases and projection replay tests**

Ensure the existing projection query excludes reversed effects and the replacement average cost equals the authoritative snapshot exactly.

- [ ] **Step 5: Write and implement RED concurrency/idempotency tests**

Cover stale `If-Match`, changed account hash, concurrent binding lock, same command replay, different content with same key, partial transaction failure rollback, DRAFT cancellation, and confirmed cancellation rejection.

- [ ] **Step 6: Run portfolio regression suite**

Run all `test_*portfolio*`, `test_projection_integrity.py`, and the new import tests.

- [ ] **Step 7: Commit confirmation slice**

```powershell
git add backend/portfolio/imports tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py tests/unit/test_projection_integrity.py
git commit -m "feat(portfolio): reconcile confirmed source snapshots"
```

---

### Task 7: HTTP API, feature flag, and recoverable import commands

**Files:**
- Modify: `backend/portfolio/api.py`
- Modify: `backend/config.py`
- Create: `tests/unit/test_tiantian_import_api.py`
- Modify: `tests/unit/test_http_observability.py`

**Interfaces:**
- Exposes the endpoints in spec §15 and stable project-wide error codes through field errors while retaining the converged top-level error vocabulary.

- [ ] **Step 1: Write RED route behavior tests**

Use a FastAPI test app with real routing and dependency overrides. Assert multipart preview, `Idempotency-Key`, `If-Match`, upload bounds, 401/403, 409 stale preview, GET restore/list, cancel, and metrics retry.

- [ ] **Step 2: Verify RED and implement thin routes**

Do not parse ZIPs or write SQL in route handlers. Stream UploadFile into a uniquely created temp file with byte count enforcement, call service methods, and always close/delete.

- [ ] **Step 3: Add feature-flag and sanitized-log tests**

When disabled, mutation endpoints return a non-retryable unavailable response; reads remain available. Logs contain request/batch/count/duration fields but not filename, holdings values, account masks, or provider bodies.

- [ ] **Step 4: Run API and observability tests**

Run new API tests, HTTP observability tests, and smoke API import.

- [ ] **Step 5: Commit API slice**

```powershell
git add backend/portfolio/api.py backend/config.py tests/unit/test_tiantian_import_api.py tests/unit/test_http_observability.py
git commit -m "feat(api): expose recoverable fund import workflow"
```

---

### Task 8: Historical NAV jobs and position-period materialization

**Files:**
- Modify: `backend/integrations/market_data.py`
- Create: `backend/jobs/data_tasks.py`
- Modify: `backend/worker.py`
- Create: `tests/unit/test_tiantian_nav_jobs.py`
- Modify: `tests/unit/test_market_data.py`
- Modify: `tests/unit/test_scheduler_reliability.py`

**Interfaces:**
- Produces: `fetch_fund_nav_history(symbol, start, end, client) -> list[MarketObservation]`.
- Produces: `DataTaskConsumer.poll_once()` with leased retry semantics.
- Consumes Task 3 algorithms and writes Task 4 observation/metric tables.

- [ ] **Step 1: Write RED paginated NAV tests**

Use complete synthetic provider pages with `DWJZ`, `FSRQ`, total count, empty pages, revised rows, and transient failure. Assert official NAV only, Decimal values, inclusive date bounds, deterministic deduplication, timeout, and bounded requests.

- [ ] **Step 2: Verify RED, implement fetcher, verify GREEN**

Network access remains outside database transactions. Store source snapshot/hash/method version through existing market repository patterns.

- [ ] **Step 3: Write RED durable task lease tests**

Assert one consumer atomically claims PENDING/expired RUNNING work, another cannot double-claim it, failures schedule exponential retry, terminal failures remain visible, and successful backfill materializes metrics for only open source-bound positions.

- [ ] **Step 4: Implement data-task consumer and worker lifecycle**

Start and stop it alongside scheduler/outbox, cap concurrency, renew/finalize with a lease token, and reuse shared NAV observations across tenants.

- [ ] **Step 5: Run market/worker regression tests**

Run market data, quality, scheduler, job, and new NAV job tests.

- [ ] **Step 6: Commit worker slice**

```powershell
git add backend/integrations/market_data.py backend/jobs/data_tasks.py backend/worker.py tests/unit/test_tiantian_nav_jobs.py tests/unit/test_market_data.py tests/unit/test_scheduler_reliability.py
git commit -m "feat(worker): backfill fund NAV and materialize range metrics"
```

---

### Task 9: Position DTO metric join

**Files:**
- Modify: `backend/portfolio/repository.py`
- Modify: `backend/portfolio/service.py`
- Create: `tests/unit/test_position_period_metrics.py`

**Interfaces:**
- Adds `holdingPeriod` and `rangeMetrics` to every position without changing existing fields.

- [ ] **Step 1: Write RED DTO tests**

Assert READY renders all decimals as strings, PENDING/PARTIAL preserves nulls and reasons, non-fund positions return null metric objects, and stale official NAV never gains a realtime label.

- [ ] **Step 2: Verify RED and implement one lateral/latest join**

Select the latest metric per tenant/account/instrument using an indexed lateral join or DISTINCT ON, not one query per position. Map nested DTOs in service without computing financial percentages in the route/frontend.

- [ ] **Step 3: Run position, summary, report, chat, and risk regressions**

These consumers share `position_snapshots`; verify the new join does not change their facts.

- [ ] **Step 4: Commit DTO slice**

```powershell
git add backend/portfolio/repository.py backend/portfolio/service.py tests/unit/test_position_period_metrics.py
git commit -m "feat(portfolio): expose fund holding range metrics"
```

---

### Task 10: Frontend import wizard and history

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/portfolio.ts`
- Modify: `frontend/src/api/hooks.ts`
- Modify: `frontend/src/features/onboarding/OnboardingPage.tsx`
- Create: `frontend/src/features/onboarding/components/TiantianImportWizard.tsx`
- Create: `frontend/src/features/onboarding/components/ImportPackageStep.tsx`
- Create: `frontend/src/features/onboarding/components/ImportPreviewStep.tsx`
- Create: `frontend/src/features/onboarding/components/ImportConfirmStep.tsx`
- Create: `frontend/src/features/onboarding/components/ImportResultStep.tsx`
- Create: `frontend/src/features/onboarding/components/ImportHistory.tsx`
- Create: `frontend/src/features/onboarding/tiantian-import.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes Task 7 endpoints and uses tenant-scoped Query keys.
- Produces a recoverable wizard whose mutation idempotency key remains stable across transport retries.

- [ ] **Step 1: Write RED first-step tests**

Assert role-aware entry, target account choice, `.zip`/20 MB validation, drag/drop, explicit upload progress label, and no confirm action before a DRAFT exists.

- [ ] **Step 2: Verify RED, add DTO/API/hook contracts, implement first step**

Build FormData without setting Content-Type manually, attach Idempotency-Key, keep file state local, and map project HttpError field errors to actionable copy.

- [ ] **Step 3: Write RED preview/confirm tests**

Render a complete MSW-style synthetic response and assert all summary counts, three tabs, NEW/CHANGED/CLOSED/CONFLICT text, blocking-disabled confirmation, holding-date decision, explicit checkbox, stable double-click request count, and 409 stale refresh path.

- [ ] **Step 4: Implement preview/confirm/result/history components**

Use real components in tests. Poll only a confirmed PENDING metrics batch; distinguish ledger success from metric failure; restore DRAFT through GET after refresh; use accessible dialog/tab/focus semantics.

- [ ] **Step 5: Run onboarding and frontend type/lint tests**

Run targeted Vitest, `npm run typecheck`, and `npm run lint`.

- [ ] **Step 6: Commit wizard slice**

```powershell
git add frontend/src/api frontend/src/features/onboarding frontend/src/index.css
git commit -m "feat(web): add Tiantian fund import wizard"
```

---

### Task 11: Holdings table range metrics UI

**Files:**
- Modify: `frontend/src/features/dashboard/components/HoldingsTable.tsx`
- Create: `frontend/src/features/dashboard/components/FundRangeMetrics.tsx`
- Modify: `frontend/src/features/dashboard/DashboardPage.tsx`
- Modify: `frontend/src/test/fixtures.ts`
- Create: `frontend/src/features/dashboard/fund-range-metrics.test.tsx`
- Modify: `frontend/src/features/dashboard/dashboard.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes Task 9 nested metrics and preserves existing holding columns and add-position dialog.

- [ ] **Step 1: Write RED READY/PENDING/PARTIAL tests**

Assert compact `持有 895 天` and `区间位置 40%`, expanded high/low/distance/date values, official-NAV and dividend caveats, no zero for nulls, and visible status/reason text beyond color.

- [ ] **Step 2: Verify RED and implement expandable metric rows**

Use buttons with `aria-expanded`/`aria-controls`, keep desktop columns bounded, and render mobile detail as stacked labels. Decimal strings are formatted, not recalculated.

- [ ] **Step 3: Update dashboard import copy and run regressions**

The page header and holdings action both say “导入 / 更新持仓”; existing manual import remains available. Run dashboard and all frontend tests, typecheck, lint, and build.

- [ ] **Step 4: Commit metrics UI slice**

```powershell
git add frontend/src/features/dashboard frontend/src/test/fixtures.ts frontend/src/index.css
git commit -m "feat(web): display fund holding range metrics"
```

---

### Task 12: Documentation, full verification, real-session validation, report, and push

**Files:**
- Modify: `docs/08.WealthSail需求设计测试追溯矩阵.md`
- Modify: `docs/10.WealthSail-MVP前后端实现与系统部署规范.md`
- Modify: `docs/11.数据库升级方法.md`
- Modify: `tools/tiantian-fund-importer/README.md`
- Create: `ai-review/<timestamp>.天天基金导入实现修改报告.md`

**Interfaces:**
- Produces exact deployment commands, migration/readback evidence, privacy-safe operator workflow, requirement coverage, and final Git delivery.

- [ ] **Step 1: Update traceability and operations docs**

Map TT-FR IDs to implementation files and named tests. Add exact PowerShell/Linux commands for dry-run, migrate-to-021, readback, API/Worker restart, smoke checks, feature flag disable, and logs. Explain that production DB rollback is forward-fix after confirmed imports.

- [ ] **Step 2: Run collector against the retained interactive profile**

If login has expired, ask the user to complete the normal visible login once. Capture a current holdings snapshot and package without printing financial details. Validate only counts, completeness, member names, hashes, and successful backend preview. Never copy the private package into tracked fixtures.

- [ ] **Step 3: Run fresh full verification**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify.ps1
Push-Location tools\tiantian-fund-importer
pnpm test
Pop-Location
git diff --check
git status --short
```

Also run migration dry-run/readback against the available non-production development database. Read complete output and record exact pass/fail counts in the report.

- [ ] **Step 4: Write the implementation report**

Document changed components, why each changed, TDD red/green evidence, migration need, exact production commands, feature flag, known limitations, security controls, test evidence, and the private-data validation outcome without asset values.

- [ ] **Step 5: Review the spec line by line**

Check every TT-FR and acceptance item. Record any unmet requirement as an explicit limitation; do not mark complete while a required item is missing.

- [ ] **Step 6: Commit final docs/report and push master**

```powershell
git add docs tools/tiantian-fund-importer/README.md ai-review
git commit -m "docs: add Tiantian import operations and implementation report"
git push origin master
```

After push, report the pushed commit range, verification evidence, migration command sequence, and how the user produces/imports the ZIP.

---

## Self-Review Record

- Spec coverage: TT-FR-001–008 → Task 1; TT-FR-020–029 → Tasks 7/10; TT-FR-040–048 → Tasks 4–7; TT-FR-060–068 → Tasks 3/8/9/11; security/deployment/retention → Tasks 2/4/7/12.
- Placeholder scan: every task names exact files, interfaces, red test behavior, verification command, implementation boundary, and commit.
- Type consistency: collector v2 fields map to `TiantianPackage`; preview returns DRAFT/version; confirm uses `If-Match`; metrics use nested `holdingPeriod/rangeMetrics`; jobs use `FUND_NAV_BACKFILL/POSITION_PERIOD_METRICS_REBUILD`.
- Critical implementation concern: the retained browser profile is currently logged out. Task 1 can be implemented against synthetic structured-response fixtures; Task 12 requires one user-assisted normal login to verify the live holdings response contract before final completion.
