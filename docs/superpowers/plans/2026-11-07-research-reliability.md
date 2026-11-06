# Daily Research Reliability Implementation Plan

> **For agentic workers:** Use the supplied three-batch execution order, with independent implementation/review and local commits after verification. Steps use checkboxes for recovery across sessions.

**Goal:** Reduce redundant model input/work while making one-shot research termination, occupation recovery and cost diagnosis reliable.

**Architecture:** Keep immutable server packets and historical acceptance contracts authoritative. Introduce an explicitly versioned model-reading projection; separate CLI, cleanup, RPC, snapshot and whole-request deadlines; release guards only from authenticated invocation-bound exit evidence. Extend independent safe observations without changing search execution evidence or investment qualification.

**Tech Stack:** Python/asyncio, FastAPI/httpx, PostgreSQL, Codex JSONL, React/TypeScript.

**Spec:** User-supplied “WealthSail 每日 AI 研究耗时、Token 消耗与超时可靠性专项优化”, sections I–XI. This plan records its execution boundaries; results and deployment instructions belong in `docs/daily-research-reliability.md`.

## Global Constraints

- No NAS, production database, real AI calls, push or deployment. Preserve existing untracked Review, patch and operations files.
- Default one model invocation; never retry inference after timeout. Keep immutable terminal records and previous valid reports.
- Keep 10 scopes / 50 exact planned queries / five risk kinds, all source/date/identity/price/risk validation, 64-topic upper bound and historical offline acceptance.
- Keep full frozen packets unchanged. Model-only compression cannot recalculate prices or truncate risk facts to meet a size target.
- Unknown usage/exit/tool observation remains unknown. Ordinary logs contain only closed enums, bounded numbers and timestamps.
- Verify and locally commit each batch, with concrete validation and external limitations in commit messages.

## Review Focus

- A model finishes while post-call account sampling fails: preserve output/usage and release only proven exited invocation.
- HTTP disconnect, late reply, runner restart or mismatched identity: never release another/current guard or retry inference.
- Detached/stubborn descendants: cleanup proof requires isolated child ownership and actual reaping, not elapsed time or a lone process scan.
- Shared URL, different news facts or changed macro definitions: preserve distinct facts, source times and definition breaks.
- Replayed events, missing tool operands or unknown token fields: do not invent counts, completeness or investment qualification.

## Batch 1 — input audit and safe projection

**Files:** `backend/macro/model_projection.py`, `backend/macro/daily_research.py` (prompt only), focused new projection/audit helpers, `scripts/` audit CLI, related tests, `docs/daily-research-input-audit.md`.

**Interface:** Existing frozen packet -> explicitly versioned model packet / prompt; unchanged server packet -> existing validation and deterministic price copying. Legacy projection remains reproducible.

- [x] Measure local `artifacts/research-recovery/ecbfb663-frozen.json` without AI: legacy prompt 592170 bytes, JSON projection 570924, schema 16266. This is not the failed 589635-byte input.
- [x] Record sorted Top 20 fields, duplicates and retention rationale before changing production projection.
- [x] Add failing invariance, distinct-fact, exact-query, risk/date/identity and historical compatibility tests.
- [x] Compact quantitative/previous plans, redundant fact representations, macro metadata references and prompt instructions without changing raw/schema acceptance contracts. All macro points remain.
- [x] Run zero-AI old/new sample comparison and historical/acceptance tests; report byte reductions separately from tokens/time; review and commit only batch-1 files. Root independently verified 121 tests and the 31.94% comparison; implementer exercised 238 related regressions.

## Batch 2 — deadlines and trusted exit

**Files:** `backend/integrations/research_lifecycle.py`, isolated Linux supervisor, `backend/integrations/codex_research.py`, `backend/integrations/codex_client.py`, `backend/codex_runner.py`; root-owned `backend/jobs/daily_research.py`, `backend/jobs/research_debug.py`, `backend/macro/research_diagnostics.py`, request-budget helper and related tests.

**Interfaces:** `research_deadlines(cli_seconds)` returns CLI/cleanup/runner/RPC/worker budgets; `ResearchLifecycle(identity).snapshot()` and `sanitize_lifecycle(state, identity)` bind `{runId, ownerToken, callId}`. `codex_client.research(... identity=..., on_exit=..., trace_usage=False)` lets Worker wrap account observation outside actual model timing. Status/cancel are authenticated and never start a model.

- [x] Reproduce same-budget outer timeout and confirmed-exit failure cases with mocked transport and fixture subprocesses.
- [x] Keep default CLI 900 seconds and supported 30–1200 range. Use cleanup15, runner-response5, RPC15, recovery20, worker5 margins (900 -> runner920 -> RPC935 -> worker960), independently bounded account snapshots.
- [x] Separate operation elapsed time from account sampling; ensure manual35-minute and frozen automatic total budgets cannot silently widen or dispatch work that cannot fit. Reserve existing 24-source/3-concurrent/25-second verification (200 seconds) plus 30 seconds postprocessing, and independent snapshots (52 seconds).
- [x] Implement isolated process ownership/reaping, identity-bound safe terminal evidence, status/cancel recovery, old-error compatibility and fail-closed unknown/restart behavior.
- [x] Add six precise diagnostic origins, guarded release on proof even when business validation fails, and immutable terminal preservation.
- [x] Fault-inject normal/timeout/504/RPC/outer/disconnect/restart/stubborn/detached-child/snapshot/validation/late/wrong-owner cases; review and commit batch 2.

## Batch 3 — actual work, progress and overlap

**Files:** `backend/integrations/research_execution.py`, `backend/macro/research_workload.py`, safe progress/usage projection as justified, `backend/investment/opportunity_fund_nav.py`, related frontend diagnostics/types and tests, final operator report.

**Interfaces:** `ExecutionObserver.progress()` / `statistics()` expose only closed bounded metadata; existing receipt-v2 remains authoritative for exact execution matching. Workload accepts old and new timing/observation versions. Optional NAV warming defers only for active nonterminal model work; existing critical price collection remains enabled.

- [x] Add failing tests for replayed events, real duplicate query/page actions, unknown operands, safe progress, token unknown and active-model NAV deferral.
- [x] Count observed search/open/find separately from planned/matched tasks; expose exact repetition as cost diagnostics only, with soft-budget wording and no fake hard cap.
- [x] Carry safe live progress through authenticated Runner status; persist/project through existing safe metadata where possible, documenting unavailable observations rather than fabricating them.
- [x] Preserve deterministic scans/previous valid reports on failed inference; do not accept partial JSON or inherit absent-risk conclusions across dates.
- [x] Verify backend/frontend suites and isolated database boundaries, review all changes, document rollout/rollback and real external risks, then commit batch 3.

## Execution Record

- Baseline `ecf3f90`; tracked working tree clean, existing untracked files preserved.
- Ruling: execute the user's supplied three batches directly without another approval gate; all work remains local and reversible.
- Ruling: no new migration unless existing JSON diagnostics cannot safely represent required progress; current diagnostic reason columns already accept bounded enum tokens.
- Ruling: resource co-location is visible in code, but no claim is made that NAV overlap caused the reported NAS timeout.
- Batch 1: same frozen sample 592170 -> 403055 prompt bytes (31.94%); actual JSON 571818 -> 390280; instructions 20352 -> 12775; schema unchanged 16266. The 570924 direct legacy projection excludes build_prompt preprocessing and is a separate measurement.

- Batch 2: independent root verification 304 passed / 3 Linux-only skips plus 3 isolated loopback PostgreSQL cases. The Linux real detached-descendant and parent-SIGKILL fixtures are delivered but unexecuted on Windows. No real AI, migration or production access.

- Batch 3: Worker now binds original usage call/run, uses independent local/remote progress monitors, preserves observed work on failures/account interruption, and projects only identity-sanitized lifecycle metadata. Backend 5520 passed/363 skipped; frontend 799 passed; isolated DB 46 passed; other local integration/e2e 33 passed/5 skipped. Final client pending-cancel observation fix verified with 99 focused tests; frontend typecheck/ESLint/build passed.
