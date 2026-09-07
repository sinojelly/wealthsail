# Corporate Actions MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist manually imported corporate actions and show the current day plus the next three days in personalized pre-market reports.

**Architecture:** Extend the existing governed `market.corporate_actions` fact table instead of creating a duplicate reference table. A migration-owner CSV command validates and append-imports revisions, while report queries select the latest revision before applying the four-day window and deterministic renderers format full and abbreviated output.

**Tech Stack:** PostgreSQL migrations, Python 3.12, asyncpg, pytest, deterministic report renderers.

**Spec:** `docs/superpowers/specs/2026-09-07-corporate-actions-mvp-design.md`

## Global Constraints

- Do not add an AI/model call.
- Keep the 08:30 macro and 08:35 personalized scheduling boundary unchanged.
- Runtime roles remain read-only for corporate actions; imports require `MIGRATION_DATABASE_URL`.
- Show report-date through report-date-plus-three-calendar-days inclusively.
- WeCom output shows at most three actions and remains within 200 characters.
- Missing data says “暂无已录入公司行动”; never infer that no market action exists.

---

### Task 1: Evolve the governed corporate-action contract

**Files:**
- Create: `migrations/057_corporate_actions_ingestion.sql`
- Modify: `scripts/migrate.py`
- Test: `tests/unit/test_migration_runner.py`
- Test: `tests/unit/test_migration_sql_syntax.py`

**Interfaces:**
- Produces: `action_date`, `summary`, `source_event_id`, `source_ref`, new action types, latest-revision identity and unchanged runtime read boundary.

- [x] Write a migration contract test that executes the migration in the SQL syntax suite and verifies the new columns, constraints, source row and effective privileges through readback.
- [x] Run the focused migration tests and verify they fail because migration 057 and readback checks do not exist.
- [x] Add migration 057 with deterministic backfills, constraints, index, source registration and grants.
- [x] Extend migration readback to require the new columns while preserving the API-read-only boundary.
- [x] Run the focused migration tests until green.

### Task 2: Add safe idempotent CSV ingestion

**Files:**
- Create: `scripts/import_corporate_actions.py`
- Create: `tests/unit/test_corporate_actions_import.py`
- Create: `docs/examples/corporate-actions.example.csv`

**Interfaces:**
- Consumes: migration 057 table contract and `MIGRATION_DATABASE_URL`.
- Produces: `parse_csv(path) -> list[CorporateActionInput]`, deterministic source IDs, `run(args) -> dict` counts, and an `--apply` write gate.

- [x] Write tests for UTF-8 BOM parsing, aliases for the four supported Chinese action names, invalid dates/types, deterministic IDs, ambiguous instruments, dry-run, duplicate skip and revision append.
- [x] Run the importer tests and verify they fail because the command does not exist.
- [x] Implement pure parsing and validation first, then the async database resolver and transactional insert.
- [x] Add an executable sample CSV covering all four target action categories.
- [x] Run importer tests until green.

### Task 3: Read latest events across the four-day window

**Files:**
- Modify: `backend/reports/repository.py`
- Modify: `backend/reports/service.py`
- Test: `tests/unit/test_pre_market_context_p2.py`
- Test: `tests/unit/test_pre_market_report.py`

**Interfaces:**
- Produces: `get_pre_market_corporate_actions(conn, instrument_ids, start_date, end_date)` returning `actionDate`, optional lifecycle dates, summary and source metadata.

- [x] Write query-boundary tests proving inclusion of day 0/day 3, exclusion of day 4, and latest-revision selection before date filtering.
- [x] Write a context test proving held and watched IDs share the query and no transient AkShare action is merged.
- [x] Run the focused tests and verify the old one-day signature and merge behavior fail.
- [x] Implement the CTE latest-revision query and pass `report_date + timedelta(days=3)` from the context builder.
- [x] Remove the on-demand AkShare merge from the production report path without deleting the adapter.
- [x] Run context and repository tests until green.

### Task 4: Render full and abbreviated upcoming actions

**Files:**
- Modify: `backend/reports/pre_market.py`
- Modify: `backend/reports/pre_market_personalized.py`
- Test: `tests/unit/test_pre_market_report.py`
- Test: `tests/unit/test_pre_market_personalized.py`
- Test: `tests/unit/test_personalized_report_push.py`

**Interfaces:**
- Consumes: normalized four-day corporate-action dictionaries.
- Produces: date-aware full text and a maximum-three-action WeCom summary with total-count suffix.

- [x] Write failing tests for today/future labels, all four action labels, honest empty state, held/watchlist separation, full-detail retention, three-item folding and the 200-character cap.
- [x] Run the focused renderer tests and verify failures reflect the existing hard-coded “今日” behavior.
- [x] Add shared date-aware formatting helpers and use them in both the standard and personalized renderers.
- [x] Ensure only same-day ex-rights events use technical-price-adjustment language.
- [x] Run all pre-market and delivery tests until green.

### Task 5: Document operations and verify the feature

**Files:**
- Modify: `docs/11.数据库升级方法.md`
- Modify: `docs/20.WealthSail累计收益与标准报告任务设计及运维.md`

**Interfaces:**
- Documents: migration command, CSV dry-run/apply commands, correction revisions, SQL fallback example and E2E verification.

- [x] Document migration 057 and safe CSV import with expected output.
- [x] Document a parameterized SQL example and the 08:35 four-day display semantics.
- [x] Run formatting/static checks, focused tests and the complete backend suite.
- [x] Review the diff against every spec acceptance item.
- [ ] Commit the verified implementation and push the agreed branch.
