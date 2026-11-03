# Dynamic Research Implementation Plan

> For agentic workers: use the shared contract, write failing regressions first, and leave commits to the root coordinator.

**Goal:** Separate extensible research discovery from bounded audited fund plans without weakening risk validation or historical immutability.

**Architecture:** Immutable paged topic snapshots and a frozen per-call audit manifest; deterministic V2 acceptance emits a strictly validated formal projection and explicit pending observations. Existing historical zero-AI recovery remains separate and read-only.

**Tech Stack:** Python/Pydantic/FastAPI, PostgreSQL102/RLS, React/TanStack Query, pytest/vitest.

**Spec:** [Design](../specs/2026-11-04-dynamic-research-design.md).

## Constraints

- No AI, production/NAS access, push or deployment.
- One model call per batch; 64 discoveries, 8 formal directions, 10 scopes/50 queries,24 sources per batch; no global registry count limit.
- Preserve V1 strict rules and all frozen materials/INITIAL/history/usage/slots.
- Related scope is never an equivalent alias or risk receipt.

## Tasks

- [x] Pure topic proposal/registry/matcher, frozen task manifest and resource TDD.
- [x] V2 deterministic item acceptance, dependency closure, strict formal pass and worker single-call integration.
- [x] Migration102 immutable snapshots/entries, archive compensation, scope-safe backlog and paged API with real PG tests.
- [x] Read-only frontend topic pages, reasons/impact/measures/tasks and coverage; bind actual source identity.
- [x] Regress NONFERROUS failure shape, actual ceaa7c1c export,12/20/30 topics, budget, matching, systemic risk, database, permissions, idempotency and no additional AI.
- [x] Full backend/frontend/PG/static/docs; inspect and make local functional commits only.

Final validation and operational limits: [operations](../../dynamic-research-operations.md).
