# Production Manual Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable explicit administrator research in production with fresh inputs, safe diagnostics, idempotency and independent automatic slots.

**Architecture:** Extend the existing refresh queue with MANUAL mode and a shared persistent model-dispatch guard. Keep the existing collection, validation, immutable terminal results and report UI; all execution gates use one mode policy.

**Tech Stack:** Python, FastAPI, asyncpg/PostgreSQL, pytest, React/TypeScript/Vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-production-manual-research-design.md`.

## Global Constraints

- `DAILY_RESEARCH_MANUAL_ENABLED` defaults to false and is independent of automatic scheduling.
- Only active OWNER/ADMIN actors enqueue manual work; no client-provided tenant, prices or mode.
- MANUAL never consumes/resets the scheduled daily slot; DEBUG remains limited to development/test.
- Model uncertainty never causes automatic retry or time-based release of model occupancy.
- Preserve pricing, validation, cache boundaries, prior valid reports and scheduled-only automatic delivery.

## Review Focus

- Concurrent automatic/manual claims must not both dispatch, including across processes.
- Expired observation windows must not unlock a possibly active model.
- Old command keys and stale roles must not mint fresh paid invocations.
- Manual inputs must use their exact fresh batch; no fallback to scheduled/debug cached sources.
- Current failure diagnostics and retained report provenance must not mix execution identities.

---

### Task 1: Mode policy, authority and queue consumer

Files: `backend/config.py`, `backend/macro/research_modes.py`, `backend/investment/research_debug_api.py`, `backend/jobs/research_debug.py`, `backend/jobs/alpha_runtime.py` and dedicated tests.

- [x] Write and observe failing tests for production manual gates, independent automatic switch, old DEBUG isolation, current membership and runtime startup.
- [x] Implement `research_request_mode_allowed(settings, mode)` and `preferred_research_request_mode(settings)`; queue the frozen mode and dispatch MANUAL through `manual_request_id`.
- [x] Verify focused tests and review authority boundaries.

### Task 2: Migration, request queue and model guard

Files: `migrations/097_manual_research_model_dispatch.sql`, `backend/macro/research_debug_repository.py`, `backend/macro/model_dispatch_repository.py`, dedicated unit/real PostgreSQL tests.

- [x] Write failing tests for MANUAL constraints, permanent command identity, mode/source matching, concurrent claims and expiry without takeover.
- [x] Implement `lock_model_dispatch(conn)`, `create_model_guard(conn, run_id, now)` and `release_model_guard(conn, run_id, owner_token, now)`; nullable release time has one global active guard.
- [x] Upgrade an owned local PostgreSQL database through 097 and verify grants, RLS, historical compatibility and scheduled-slot isolation.

### Task 3: Fresh sources, model execution and report projection

Files: `backend/macro/daily_repository.py`, `backend/macro/alpha_repository.py`, `backend/jobs/alpha_public.py`, `backend/jobs/research_inputs.py`, `backend/jobs/daily_research.py` and dedicated tests.

- [x] Write failing tests for independent manual execution, fresh exact batch, quality failure before model, busy dispatch waiting, normal-return release and uncertainty retention.
- [x] Add `manual_request_id` alongside compatible DEBUG parameters; bind real MANUAL source/run modes and transact model lock/run/guard atomically before releasing the DB connection.
- [x] Make newer manual execution state/diagnostics visible without changing retained report provenance or automatic schedule facts.
- [x] Verify focused tests and existing scheduled/debug behavior.

### Task 4: Production button and regression verification

Files: existing daily research refresh API/types/components, dedicated Vitest tests and NAS upgrade docs.

- [x] Observe failing tests for manual copy/cost disclosure, same-key reload/retry, waiting states, success refresh and failure retention.
- [x] Implement the formal button and mode labels using existing capability/polling; keep safe fixed diagnosis mappings.
- [x] Run full backend/frontend suites, real PostgreSQL checks, Ruff, TypeScript, lint/build, smoke/docs/migration checks; inspect diffs and locally commit functional batches.

## Verification record (2026-11-02)

- Full default backend suite: **4551 passed, 225 skipped**, six existing python-jose UTC deprecation warnings. Log: `artifacts/manual-research/backend-full.log`.
- Final related suite with actual owned PostgreSQL 18.1: **229 passed**, including 19 new real database scenarios. It includes the real production manual Worker path with fake collectors/model, real source-quality and report validation, fresh batch/report replacement, command replay, validation failure retention and global guard behavior. Concurrent real scheduled/manual execution reached a maximum of one mocked model call at a time and made zero HTTP requests. Log: `artifacts/manual-research/postgres-final.log`.
- Fresh database upgraded through 096, then 097 using the existing migration runner. The old readback initially rejected the new request-mode grant; after narrowly extending its allowlist, the same runner skipped the applied migration and returned READBACK OK. Pre097 requests, batches and runs remained DEBUG. No checksum or migration-ledger reconciliation occurred.
- Frontend final full Vitest: **599 passed across 66 files**. TypeScript, ESLint, production build and **6 smoke configuration tests** passed. Logs: `artifacts/manual-research/frontend-*-late-ack-final.log`.
- CI Ruff scope, Python compile, conflict scan, docs/local links and migration discovery passed; **94 migration files**, latest **097**.
- Additional RED-to-GREEN regressions cover normal model return followed by cancellation during AI usage bookkeeping (local and remote), manual-only optional-audit startup, explicit clearing of a stale disabled-mode submission after a confirmed 403, and a delayed acknowledgement from an unmounted component erasing a newer unresolved key. No network error silently changes command identity; only a matching acknowledgement clears its key.
- Validation changed only test fixtures where necessary: mock transaction/guard dependencies, precise owned-test database allowlist, cleanup of their own new guard foreign keys, and current required industry-review coverage in a fixed DEBUG model fixture. Real validators remain enabled in PostgreSQL end-to-end tests.
- The owned PostgreSQL server was stopped after final validation. Real Codex execution, production data access, NAS deployment and remote pushes were not performed. User-owned untracked deployment notes remain untouched. Deployment requires migration 096/097, rebuilt backend/frontend images and the same manual switch in API/Worker.
