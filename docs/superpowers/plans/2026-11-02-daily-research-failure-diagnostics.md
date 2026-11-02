# Daily research failure diagnostics

> Execute with tests first, preserving the existing pricing, validation and daily cost barrier.

**Goal:** A failed research run identifies its safe stage, reason, fixed rule and array index in logs, PostgreSQL and the report UI, without a second model invocation.

**Architecture:** A dependency-free closed metadata module is shared by validators, worker, repository and optional audit. Terminal diagnostics are inserted atomically with the existing immutable result. The public API only exposes diagnostics belonging to the current execution. Independent, bounded diagnostic snapshots survive oversized raw audit payloads.

**Technology:** Python/pytest, PostgreSQL migration 096/asyncpg, React/TypeScript/Vitest.

**Scope:** User attachment “WealthSail 每日 AI 投研失败诊断改造方案”, P0 + P1. No strategy or model changes, no production deployment, no automatic retry, no AI usage status changes. Historical rows remain nullable. No raw model/exception text in ordinary logs or the failure UI.

## Execution and verification

- [x] Add failing tests for closed metadata, ten validator rules and outer array indexes; implement typed errors while preserving schema classification.
- [x] Add failing repository and real isolated PostgreSQL tests; implement migration 096 and one terminal INSERT with safe public projection. Preserve RLS, roles, unique daily slot and immutability.
- [x] Add failing worker tests; log and persist safe metadata, log/rethrow persistence failures, preserve cancellation, lease handling and consumed-day protection.
- [x] Add failing optional audit tests; startup warning for disabled/unwritable storage, independent atomic small snapshot, bounded permissions and retention.
- [x] Add failing UI tests; fixed Chinese explanations, current research run identity, safe unknown-field fallback and old-report compatibility.
- [x] Run focused tests, full backend/frontend suites, Ruff, TypeScript/build and repository verification. Update operator migration/audit instructions. Review diff and commit only related files locally.

**Review focus:** No exception text in structured diagnostics; schema failures remain schema failures; current pipeline diagnostics never borrow an older report; persistence failure never triggers another inference; audit failure never stops daily research. Real PostgreSQL verification uses only an owned loopback cluster.

**Operational limit:** This improves subsequent failures; it cannot reconstruct a missing rule for the November 2 NAS failure from the screenshot. Deployment requires rebuilt images plus migration 096.

## Verification record (2026-11-02)

- Default full pytest: **4492 passed, 206 skipped**, six existing python-jose UTC deprecation warnings. The first full run exposed only the migration-tip assertion still fixed at 095; after updating it to 096 the complete suite passed.
- Real owned PostgreSQL 18.1: **177 related tests passed**, including runtime roles, existing RLS configuration, immutable terminal insert, consumed-day barrier, nullable history and index boundaries. A fresh temporary database was upgraded through 095, an old-contract result inserted, then 096 applied by the existing runner with READBACK OK. The provisional test migration was not reconciled by rewriting checksums. The temporary server was stopped after validation.
- Frontend full Vitest: **579 passed across 65 files**; TypeScript, ESLint, production build and **6 smoke configuration tests** passed.
- CI Ruff scope (`backend tests scripts`), Python compile, conflict-marker scan, documentation contracts and migration discovery passed; **93 migration files**, latest version **096**.
- Regression tests first demonstrated missing safe diagnostics, missing database columns, oversize-summary loss and missing UI details. Final review also added RED-to-GREEN coverage for expiry during persistence cancellation and both database/UI index upper bounds.
- Logs are retained under ignored `artifacts/failure-diagnostics/`, particularly `diag-backend-full-final.log`, `verification-v2.txt`, `diag-frontend-all-final.log` and the final lint/typecheck/build logs. No real model calls, production database access, image deployment or remote push occurred. The user's unrelated untracked deployment notes were preserved.
