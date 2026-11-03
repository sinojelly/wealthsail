# Research Repair Implementation Plan

> **For agentic workers:** Use the shared design and work task-by-task with failing regressions before implementation; root coordinates interfaces and final review.

**Goal:** Add bounded future deterministic correction and immutable zero-AI historical derivations without rewriting historical research.

**Architecture:** A shared pure repair engine runs the original strict validator on a copied, explicitly quarantined candidate. Future publication uses a separate safety gate. Historical candidates, independent reviews and read-only publication live in migration101 tables and never enter old READY consumers.

**Tech Stack:** Python/Pydantic/FastAPI, PostgreSQL FORCE RLS, React/TanStack Query, pytest/vitest; existing bounded offline subprocess.

**Spec:** [Design](../specs/2026-11-04-research-repair-design.md).

## Global Constraints

- No production DB, real model calls, scheduler resets, push or deployment.
- Keep original raw/checks/packet/time, strict risk checks, price anchors and READY consumers.
- No sector inference from titles or equivalence across different industry scopes.
- Only independent current active admins can review; publication is historical/read-only.
- Real-case verification requires actual exported materials; synthetic structure is labeled.

## Review Focus

- Seed ID collisions must not leak quarantined direction dependencies.
- Removing observations must not silence HIGH/systemic or persisted negative risk.
- Header dependencies and missing legacy importance must block automatic publication.
- Read-only preview cannot secretly write an admission ledger.
- Immutable candidates and review identity must survive races, revoked membership and transaction failure.

### Task 1: Shared repair engine and future integration

Files: new backend/macro/research_repair.py; daily_research validator/schema/prompt and job integration; repair and worker tests.

Interface: repair_report(raw, packet, source_checks, *, research_id, validated_at) returns JSON decision, strictPass, autoPublishAllowed, historicalPublishAllowed, formalReport/candidateRaw, originalOutcome, quarantined/retained/counts/blockers and versions.

- [x] Write RED cases for bounded 8→6 isolation, unreviewed fund refusal, scope mismatch, risk preservation, header and dependency closure.
- [x] Implement one-shot isolation and original-time full strict revalidation, without editing retained items or source/risk reviews.
- [x] Integrate only before a new research's first terminal write; keep original audit decision and model call count.
- [x] Verify prompt/source/search budgets and corrected READY metadata/readback; root commits related changes after checks.

### Task 2: Frozen export validation and offline file preview

Files: new backend/macro/research_frozen_bundle.py, scripts/research_repair_preview.py and tests.

Interface: decode_frozen_bundle(bundle, expected_run_id, *, observed_at) returns immutable packet/raw/checks/validated_at/material hashes; CLI consumes the existing artifact DTO and never connects DB or invokes AI.

- [x] RED identity/hash/size/clock/duplicate-key corruption and input mutation cases.
- [x] Implement bounded decoding and fixed child invocation; distinguish export self-consistency from server attestation.
- [x] Use real export if supplied; otherwise preserve a clearly labeled structural regression and report the limitation.

### Task 3: Historical immutable storage, permission and API

Files: migration101, new repair repository/API, fixed child repair entrypoint, migration readback and pytest.

Interfaces: GET /from/{runId}/preview, POST /from/{runId} {commandKey}, list/detail, POST /{id}/reviews and /publish under /investment/daily-research/history-repairs.

- [x] RED true read-only preview, quota/idempotency, RLS, source eligibility, distinct reviewer, stale evidence, immutable originals and rollback.
- [x] Store bounded candidate/provenance with append-only review/publication events; enforce independent review in DB.
- [x] Keep publication HISTORICAL_READ_ONLY and all old READY selectors unchanged.
- [x] Verify real isolated PostgreSQL and no provider/notification invocation, then root commits.

### Task 4: Corrected report notices and separate history UI

Files: report/acceptance metadata projection; new frontend history API/components and tests; OperationsPage related tab.

- [x] RED report-identity-bound badge and corrected-original-PARTIAL wording.
- [x] RED admin preview/generate/review/publish, ordinary published-only history and no actionable prices/tracking controls.
- [x] Implement safe readonly projections, evidence time and isolation annex; validate tenant cache identities and command retries.
- [x] Run frontend full/typecheck/lint/build; root commits.

### Task 5: Final validation and operating guide

- [x] Full backend, relevant isolated PostgreSQL, frontend, Ruff/compile/TypeScript/docs/migration discovery/readback.
- [x] Inspect staged diff/credentials, preserve user file, make local functional commits only.
- [x] Document migration101 order, manual independent approval flow, current-vs-historical boundaries and actual real-case verification status.
