# Opportunity recommendation quality implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development task by task; root integrates and reviews the final branch.

**Goal:** Match positive research opportunities to distinct, well-supported off-exchange products without slow full-catalog work on page reads.

**Architecture:** Separate stable topic/product discovery from current subscription and NAV facts. Precompute discovery after research archival, store it under the original run visibility, and expose one risk-aware opportunity card with bounded pending refresh. Preserve strict trade eligibility and historical research.

**Tech Stack:** Python, FastAPI, PostgreSQL, React, TanStack Query, pytest, Vitest.

**Spec:** `ai-review/82.评审80-Gemini.md`, with the concrete cases in `ai-review/81.评审80-Gpt.md`.

## Global constraints

- No extra AI calls, provider I/O on GET, fabricated prices, or investment permission.
- Keep the 46-product NAV collection boundary separate from at most 72 topics and three distinct displayed products per topic.
- Preserve original run/snapshot clocks, RLS, archive lease fencing and bounded retry.
- Fund-name trigram index already exists in migration 068; do not recreate it.
- Retain meaningful ETF/feeder/fund-family names when deduplicating share classes.
- Review and deliver existing related priority changes; preserve unrelated working files.
- Verify each functional group before local commit; no remote push.

## Review focus

- Negative-only or ambiguous topics remain visible without becoming positive recommendations.
- A suspended direct product must not trigger a weaker broad-index recommendation.
- Share-class suffix removal must not merge independently managed products or ETF/feeder structures.
- Cache miss, stale version, archive failure and cross-tenant reads cannot trigger uncontrolled discovery or expose private topics.
- An apparently attractive entry range without fresh, continuous local NAV remains data-pending; overheating wins over an entry label.

## Task 1: Precise stable discovery

Files: `backend/investment/opportunity_fund_recall.py`, optional pure matching module, associated unit and PostgreSQL tests.

Interfaces: `discover_topic_funds(conn, topics, packet, now)` returns stable JSON topic/product associations. `enrich_topic_funds(conn, discovered, packet, now)` adds current trade facts; `recall_topic_funds` remains a compatibility wrapper.

- [x] Write and observe failing tests for compound concepts, verified-index precedence, A/C/E deduplication, suspended direct products and non-applicable geographic fallback.
- [x] Add controlled concept roots and four association levels. Deduplicate share classes before display limits, preserving alternatives and product identity.
- [x] Keep broad fallback separate in `indirectFunds`; only use explainable macro/asset relationships with no direct candidates.
- [x] Run discovery regressions and real SQL cases, inspect execution behavior, then commit.

## Task 2: Archived discovery cache

Files: new `backend/investment/opportunity_fund_cache.py`, `backend/jobs/research_archive.py`, migration 104, cache/job/SQL tests.

Interfaces: cached candidate reads keyed by run, snapshot, validated time and matching version; root reader enriches only those identities.

- [x] Write and observe failing tests for archive precomputation, cache hits, misses, bounded backfill, failed retries, stale versions and tenant visibility.
- [x] Add worker-owned cache and safe API SELECT policy; no subscription, NAV or trade permission facts are frozen in it.
- [x] Precompute after archive success, and backfill existing archived runs outside GET. Failures do not rerun AI or destroy economic archives.
- [x] Reuse name trigram index, index index-name matching as needed, and inspect `EXPLAIN (ANALYZE, BUFFERS)` on an isolated database with 72 topics.
- [x] Validate migration and real API/worker roles, then commit.

## Task 3: Polarity and live price position

Files: new `backend/macro/research_topic_polarity.py`, new `backend/investment/opportunity_fund_prices.py`, `backend/macro/research_opportunities.py`, related priority/backend tests.

- [x] Write failing tests for positive, negative, mixed, macro and undetermined topics, including negated phrases and conflicting evidence.
- [x] Expose explanatory classification. Negative topics show `relatedRiskFunds`, macro/undetermined topics remain observations, and indirect products never count as direct recommendations.
- [x] Write failing tests for out-of-pool products with local history, no/short/stale/foreign/future history, and overheated versus in-zone coordinates.
- [x] Reuse existing strict local NAV plan computation and attach `pricePosition` without granting buy permission; GET reads cached identities only.
- [x] Validate source/run boundaries, full-catalog priority and actual reader RLS, then commit.

## Task 4: Unified opportunity cards

Files: `TopicFundRecommendations.tsx`, `ResearchTopicRegistry.tsx`, `MacroContextStrip.tsx`, corresponding API types/tests.

- [x] Write failing tests for one visible card per topic, polarity labels, association tiers, alternative shares, price position and indirect/risk separation.
- [x] Present one opportunity highlight area, with the full registry as a collapsed detailed entry.
- [x] Poll only explicit topic/archive/matching pending states every three seconds, at most ten times; stop on completion, failure, unmount and identity change, retaining manual retry.
- [x] Run frontend tests, typecheck, build and desktop/mobile interaction QA; commit relevant existing priority changes with the feature.

## Final integration

- [x] Independent review; resolve reproducible findings.
- [x] Backend and frontend full suites, meaningful real PostgreSQL tests, lint/build and diff checks.
- [x] Confirm only task changes were committed and document verified behavior and remaining deployment limits.
