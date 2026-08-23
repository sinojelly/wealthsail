# WealthSail Documentation Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inconsistent 01–05 documents with an evolvable L0–L4 product/design/test set and add data, security, and traceability documents for the approved China-market MVP.

**Architecture:** Documentation is organized from stable product intent to stable domain/data/security contracts, then implementation and verification. Requirement IDs are defined in the PRD and indexed by one traceability matrix; later documents reference those IDs instead of restating conflicting requirements.

**Tech Stack:** Markdown, Mermaid, PostgreSQL/Supabase design, FastAPI/Pydantic design, Hermes WeCom Adapter design, PowerShell document validation.

**Spec:** `docs/superpowers/specs/2026-08-23-wealthsail-docs-and-prototype-restructure-design.md`

## Global Constraints

- MVP assets are A-share stocks, exchange-traded ETF/LOF funds, and off-exchange public funds.
- MVP reports are pre-market, intraday, 14:30 fund, and post-market interactive review.
- MVP supports Web, WeCom, and 2–3 isolated users.
- Requirements use L0–L4 plus Must/Should/Could and value/effort/risk/confidence scoring.
- L0 tenant, account, instrument, ledger, source, and model identifiers remain stable through L4.
- Database evolution uses versioned contracts and expand–migrate–contract migrations.
- No production example may expose a secret, give an Agent a service-role key, or present hard-coded market values as current data.
- All test cases start as `NOT_RUN` unless this repository contains fresh reproducible execution evidence.
- External media is governed as evidence with rights, source, timestamp, quotation, and injection controls; it is not converted directly into trading instructions.

---

### Task 1: Rewrite the product requirements as the single scope authority

**Files:**

- Modify: `docs/01.WealthSail投研平台需求文档.md`
- Reference: `ai-review/01.WealthSail需求设计与测试Review报告.md`

**Interfaces:**

- Consumes: approved spec sections 2–4 and 11.
- Produces: stable IDs `PRD-*`, `FR-*`, `DATA-*`, `SEC-*`, `NFR-*`, and `SC-*` used by Tasks 2–6.

- [ ] **Step 1: Replace the document outline**

Use this exact top-level order:

```text
0 文档控制与阅读指南
1 产品问题、目标用户与 JTBD
2 产品原则、成功指标与非目标
3 L0–L4 路线和排序方法
4 L0 可信底座需求
5 L1 核心 MVP 功能需求
6 四时段用户旅程
7 正常、替代与异常场景
8 L2–L4 增量需求
9 非功能需求
10 数据、安全、隐私与合规约束
11 发布门槛、风险和依赖
12 术语表
```

- [ ] **Step 2: Define one primary persona and the MVP job**

State that the primary persona is a China-market self-directed investor with 1–3 securities/fund accounts who is willing to maintain theses and review decisions weekly. Define the job as reducing research time and avoidable decision errors under a stated risk budget. Explicitly exclude guaranteed returns, high-frequency trading, automatic execution, and public paid advisory from MVP.

- [ ] **Step 3: Define the priority model and route each requirement**

For every requirement row include:

```text
ID | Level | Must/Should/Could | Value | Effort | Risk | Confidence | Acceptance summary
```

Use `Score = Value × Confidence ÷ (Effort + Risk)` for within-level ordering. Security, privacy, ledger correctness, and compliance are hard gates even when their score is lower.

- [ ] **Step 4: Specify L0 and L1 requirements**

Include Must requirements for tenant isolation, instrument master, immutable ledger, source snapshots, data status, audit, idempotency, three asset types, four report sessions, Web/WeCom, thesis/exit rules, basic benchmark/risk, decision journal, and abstention.

- [ ] **Step 5: Specify four end-to-end journeys and abnormal branches**

For each session define trigger, data cutoff, inputs, deterministic calculations, LLM role, output schema, WeCom behavior, user actions, expiry, retry/idempotency, audit, and fail-closed behavior. Include market holiday, source stale/conflict, fund estimate unavailable, duplicate report, WeCom offline, user switch, and unconfirmed post-market draft.

- [ ] **Step 6: Define measurable success and release gates**

Include research time saved, report open/acknowledge rate, planned-trade adherence, turnover/cost, benchmark-relative return, drawdown, data freshness, citation coverage, abstention, false-alert rate, job reliability, and cross-tenant security. Do not define investment alpha as an MVP release gate.

- [ ] **Step 7: Run PRD consistency checks**

Run:

```powershell
rg -n "三核|四核|100%满足|100% 满足|全品类.*P0|自动重写.*Skill" "docs/01.WealthSail投研平台需求文档.md"
rg -o "(PRD|FR|DATA|SEC|NFR|SC)-[A-Z0-9-]+" "docs/01.WealthSail投研平台需求文档.md" | Sort-Object | Group-Object | Where-Object Count -gt 1
```

Expected: the first command finds only historical/non-goal explanations if any; the second command prints no duplicate requirement definitions.

- [ ] **Step 8: Commit the PRD**

```powershell
git add -- "docs/01.WealthSail投研平台需求文档.md"
git commit -m "docs(prd): define the L0-L4 WealthSail product scope"
```

### Task 2: Rewrite the evolvable architecture and decision record

**Files:**

- Modify: `docs/02.WealthSail投研平台全栈技术选型与架构评估.md`

**Interfaces:**

- Consumes: PRD IDs and stable core constraints from Task 1.
- Produces: module boundaries, ports, event contracts, deployment views, and technology triggers referenced by the build guide and tests.

- [ ] **Step 1: Replace the architecture outline**

Use:

```text
1 Context and quality attributes
2 Architecture principles
3 Modular-monolith component view
4 Runtime and deployment view
5 Data and event flows for four sessions
6 Agent and deterministic-engine boundary
7 Technology options and ADR decisions
8 Evolution from L0/L1 to L4
9 Reliability, observability, cost, and operations
10 Risks and proof-of-concept gates
```

- [ ] **Step 2: Define module ownership**

Document Identity & Tenancy, Portfolio Ledger, Market/Fund Data, Research & Evidence, Risk & Policy, Reports & Conversation, Workflow & Notification, Evaluation & Governance. State that modules may not read another module's private tables and expose typed application services/events instead.

- [ ] **Step 3: Define replaceable ports**

Use versioned contracts named `MarketDataProvider`, `FundDataProvider`, `ResearchModel`, `MessagingChannel`, `DurableWorkflow`, and `SecretStore`. Document that Hermes/OpenClaw are adapters, not the transaction or tenant boundary.

- [ ] **Step 4: Define four-session workflows and failure behavior**

Add Mermaid sequence/flow diagrams showing source snapshots, deterministic calculations, evidence-linked model synthesis, report validation, database commit, Outbox, WeCom delivery, acknowledgement, and post-market decision recording.

- [ ] **Step 5: Replace the technology scorecard**

Compare plain FastAPI/Pydantic + Worker, Hermes, OpenClaw, LangGraph, OpenAI Agents SDK, and DBOS/Restate/Temporal by correctness, HITL recovery, tenancy/security, WeCom, observability, cost, operations, and replaceability. Record modular monolith + FastAPI/PostgreSQL/Worker as MVP core and Hermes WeCom as one channel/runtime adapter.

- [ ] **Step 6: Define evolution mechanics**

Document stable IDs, versioned JSON/event schemas, expand–migrate–contract, rebuildable projections, additive asset-specific tables, contract tests, and evidence-based service extraction. Give concrete L2–L4 examples: fund look-through as new projection, media ingestion as new provider/module, and durable approval as replacement behind `DurableWorkflow`.

- [ ] **Step 7: Validate forbidden claims**

Run:

```powershell
rg -n "100%|物理隔离|几乎为零|两天完成|GIL.*瓶颈|Go.*保证" "docs/02.WealthSail投研平台全栈技术选型与架构评估.md"
```

Expected: no unsupported certainty claim.

- [ ] **Step 8: Commit the architecture**

```powershell
git add -- "docs/02.WealthSail投研平台全栈技术选型与架构评估.md"
git commit -m "docs(architecture): design an evolvable modular MVP"
```

### Task 3: Add the canonical data and source-governance design

**Files:**

- Create: `docs/06.WealthSail数据模型与数据源治理设计.md`

**Interfaces:**

- Consumes: PRD data/security IDs and architecture module/event boundaries.
- Produces: canonical table names, key fields, constraints, time semantics, media schema, and migration rules used by Tasks 4–6.

- [ ] **Step 1: Document the schema map**

Define these groups and tables:

```text
identity: profiles, accounts, channel_bindings, secret_refs
reference: instruments, instrument_aliases, trading_calendars, data_sources
ledger: ledger_events, ledger_legs, position_lots, position_snapshots, account_nav_snapshots
market: source_snapshots, market_observations, corporate_actions
research: theses, recommendations, evidence_refs, model_runs
risk: investment_policies, alert_rules, alert_events
decision: decisions, order_intents, executions, evaluations
media: source_registry, media_items, transcripts, media_claims
operations: jobs, job_runs, outbox, audit_log
collections: collections, collection_items
```

- [ ] **Step 2: Define the minimum L0/L1 columns and invariants**

For each table specify primary key, `tenant_id` ownership, timestamps, status enum, source/idempotency key, foreign keys, unique constraints, check constraints, and key indexes. State which L2–L4 fields/tables are additive extensions rather than MVP columns.

- [ ] **Step 3: Provide executable schema fragments for the critical path**

Include SQL examples for tenant-scoped accounts, instruments, ledger events/legs, reports/recommendations/evidence, source snapshots, outbox, and RLS. All `user_id/tenant_id` columns must have explicit UUID types. Every tenant table shown must include RLS and a policy; `service_role` must be described as backend-only.

- [ ] **Step 4: Define time, quality, and fund-estimate semantics**

Distinguish `published_at`, `as_of`, `observed_at`, `retrieved_at`, and `revised_at`. Define `FRESH`, `ESTIMATED`, `STALE`, `CONFLICT`, `UNAVAILABLE`, and `REVISED`, including which report actions are allowed for each state.

- [ ] **Step 5: Define media evidence and rights lifecycle**

Specify creator/platform identity, rights status, origin URL, content hash, transcript timestamps, claim type, horizon, confidence, verification references, revocation, and retention. Require external content to remain untrusted input.

- [ ] **Step 6: Define migration and projection rebuild procedures**

Include expand–migrate–contract, dual-read/write only when unavoidable, background backfill, compatibility checks, rollback, and projection rebuild checkpoints. Demonstrate how HK/US stocks and a new workflow engine can be added without changing ledger identity.

- [ ] **Step 7: Check schema completeness**

Run:

```powershell
rg -n "tenant_id|instrument_id|ledger_events|ledger_legs|source_snapshots|source_registry|outbox|ENABLE ROW LEVEL SECURITY|expand.migrate.contract" "docs/06.WealthSail数据模型与数据源治理设计.md"
rg -n "user_id REFERENCES|tenant_id REFERENCES" "docs/06.WealthSail数据模型与数据源治理设计.md"
```

Expected: all required concepts are present; every SQL FK column has an explicit data type.

- [ ] **Step 8: Commit the data design**

```powershell
git add -- "docs/06.WealthSail数据模型与数据源治理设计.md"
git commit -m "docs(data): define the ledger and source governance model"
```

### Task 4: Add the security, privacy, and compliance control design

**Files:**

- Create: `docs/07.WealthSail安全隐私与合规设计.md`

**Interfaces:**

- Consumes: PRD security IDs, architecture trust boundaries, and data ownership rules.
- Produces: permission matrix, threat controls, verification requirements, and operating-mode gates used by tests.

- [ ] **Step 1: Define the operating modes and legal gate**

Separate personal self-use, closed 2–3-user pilot, and public/paid SaaS. State that public/paid specific security recommendations require legal review, suitability, disclosures, retention, complaint handling, and any necessary licensed partnership before release.

- [ ] **Step 2: Build the trust-boundary and permission matrix**

Cover browser, WeCom user, Hermes adapter, API, worker, database, service role, model provider, market provider, and media source. For each actor define allowed resource/actions and denied direct access.

- [ ] **Step 3: Define controls for the top threats**

Include IDOR/RLS bypass, service-key disclosure, binding-code theft/replay, webhook replay, prompt injection from media/news, cross-tenant cache/vector leaks, NL2Strategy sandbox escape, duplicated financial events, sensitive logging, backup exposure, and dependency compromise.

- [ ] **Step 4: Define privacy lifecycle**

Document purpose limitation, minimum collection, consent, automated-decision explanation/objection, export, correction, deletion, retention, backup deletion, processor inventory, and privacy impact assessment.

- [ ] **Step 5: Define secrets and incident operations**

Require Vault/KMS references, rotation, redaction, environment separation, least privilege, immutable audit, incident classification, notification, containment, recovery, and post-incident review.

- [ ] **Step 6: Define security release gates**

Require cross-tenant tests for all resource types, no service-role key outside the backend, WeCom replay/allowlist tests, injection evals, dependency/secret scans, backup restore, and signed risk acceptance for exceptions.

- [ ] **Step 7: Commit the control design**

```powershell
git add -- "docs/07.WealthSail安全隐私与合规设计.md"
git commit -m "docs(security): define pilot privacy and compliance controls"
```

### Task 5: Rewrite the MVP build guide against the canonical designs

**Files:**

- Modify: `docs/03.WealthSail-MVP快速构建与实战指南.md`

**Interfaces:**

- Consumes: architecture ports, data schema, security controls, and L0/L1 PRD.
- Produces: ordered implementation procedure with safe snippets and verification checkpoints.

- [ ] **Step 1: Replace the guide with phased implementation**

Use this sequence:

```text
Prerequisites and safe configuration
Local services and migration order
Identity/RLS and 2–3 test tenants
Instrument master and immutable ledger
Provider adapters and source status
Report/evidence APIs
Worker, Outbox, and four scheduled sessions
Hermes official WeCom adapter and binding
Web/React prototype integration contract
Failure drills and acceptance commands
Upgrade path to L2–L4
```

- [ ] **Step 2: Provide safe configuration examples**

Use placeholders such as `VAULT_REF=...`; state where backend-only keys live. Do not copy real keys or tell Hermes/browser code to use `service_role`. Use the current Hermes WeCom setup command and describe allowlist/pairing.

- [ ] **Step 3: Provide typed API examples**

Show authenticated FastAPI/Pydantic contracts for current portfolio, report retrieval, post-market decision confirmation, and WeCom binding. The backend derives `tenant_id` from the verified session; request bodies must not select arbitrary tenants.

- [ ] **Step 4: Provide idempotent workflow examples**

Show a transaction that inserts a ledger event/legs or report plus Outbox row with a unique idempotency key. Show retry behavior and `UNAVAILABLE/STALE/CONFLICT` response examples; never use sample prices as fallback.

- [ ] **Step 5: Document each report schedule and calendar behavior**

Define data cutoff, job key, holiday behavior, model/provider versions, validation, publication, expiry, and WeCom delivery. The 14:30 report must say estimated interval and disclose source time.

- [ ] **Step 6: Add upgrade notes**

For each implementation phase identify the L2–L4 extension point: new Provider Adapter, new projection, new module consumer, or workflow adapter. Do not suggest table rewrites or cross-module private-table reads.

- [ ] **Step 7: Scan for unsafe legacy examples**

Run:

```powershell
rg -n "service_role|shell=True|limit\(1\)|brent_price\s*=|wti\s*=|gold_spot\s*=|100% 满足|100%满足" "docs/03.WealthSail-MVP快速构建与实战指南.md"
```

Expected: `service_role` appears only in explicit backend-only warnings; all other legacy patterns are absent.

- [ ] **Step 8: Commit the guide**

```powershell
git add -- "docs/03.WealthSail-MVP快速构建与实战指南.md"
git commit -m "docs(guide): provide a safe L0-L1 build sequence"
```

### Task 6: Rebuild MVP/full tests and the traceability authority

**Files:**

- Modify: `docs/04.WealthSail-MVP功能验收与自动化测试规范.md`
- Modify: `docs/05.WealthSail-全量系统功能验收与全栈测试规范.md`
- Create: `docs/08.WealthSail需求设计测试追溯矩阵.md`
- Create: `scripts/validate_docs.ps1`

**Interfaces:**

- Consumes: every requirement ID, architecture control, table, API, workflow, and security gate from Tasks 1–5.
- Produces: executable verification catalog and a validator used for final completion evidence.

- [ ] **Step 1: Rewrite the MVP test strategy**

Organize by pure finance unit, schema/contract, data quality, PostgreSQL/RLS, workflow fault injection, Agent offline eval, WeCom contract, security, E2E, performance/stability, and recovery. Cover all L0/L1 Must requirements and the abnormal branches from the PRD. Set every case state to `NOT_RUN`.

- [ ] **Step 2: Rewrite the L2–L4 test strategy**

Cover multi-account/FX, fund look-through, media rights/revocation/injection, backtest bias, multi-Agent correlated failure, Skill candidate evaluation/canary/rollback, durable approval, new markets, and broker adapters. Define entry conditions so these tests do not imply the features exist today.

- [ ] **Step 3: Create the traceability matrix**

For each requirement use:

```text
Requirement ID | Level | Requirement | Design section | Data/API | Normal test | Abnormal/security test | Automation | Status | Evidence
```

All L0/L1 Must requirements must have at least one normal and one abnormal/security test. Evidence is empty or `—` while status is `NOT_RUN`.

- [ ] **Step 4: Add a document validator**

Implement `scripts/validate_docs.ps1` to fail when:

```powershell
$requiredDocs = 1..8
$forbiddenPatterns = @(
  '\|\s*PASS\s*\|',
  'shell=True',
  '100%满足',
  '100% 满足'
)
```

The script must also extract requirement IDs from `docs/01`, assert uniqueness at definition rows, verify every L0/L1 Must ID occurs in `docs/08`, verify Markdown relative links, and print counts and failures before returning non-zero.

- [ ] **Step 5: Run the validator and fix every failure**

Run:

```powershell
pwsh -NoProfile -File scripts/validate_docs.ps1
```

Expected: exit 0 with eight docs present, no forbidden patterns, unique requirement definitions, full L0/L1 Must traceability, and no missing local links.

- [ ] **Step 6: Commit tests, traceability, and validator**

```powershell
git add -- "docs/04.WealthSail-MVP功能验收与自动化测试规范.md" "docs/05.WealthSail-全量系统功能验收与全栈测试规范.md" "docs/08.WealthSail需求设计测试追溯矩阵.md" "scripts/validate_docs.ps1"
git commit -m "docs(test): establish risk-based acceptance traceability"
```

### Task 7: Perform documentation integration verification

**Files:**

- Modify if required: `docs/01.WealthSail投研平台需求文档.md` through `docs/08.WealthSail需求设计测试追溯矩阵.md`
- Add to documentation commit: `ai-review/01.WealthSail需求设计与测试Review报告.md`

**Interfaces:**

- Consumes: all documentation tasks.
- Produces: a self-consistent document set ready for the React implementation plan.

- [ ] **Step 1: Run the complete validator**

```powershell
pwsh -NoProfile -File scripts/validate_docs.ps1
```

Expected: exit 0.

- [ ] **Step 2: Run a manual requirement checklist**

Verify search results exist for all of:

```powershell
$terms = @('A 股股票','场内 ETF','场外公募基金','盘前简报','盘中跟踪','14:30','盘后','企业微信','2–3','L0','L4','source_registry','expand–migrate–contract')
foreach ($term in $terms) { rg -l --fixed-strings $term docs }
```

Expected: every term occurs in the canonical requirements/design documents, not only in the Review report.

- [ ] **Step 3: Inspect the final diff and sensitive patterns**

```powershell
git diff --check
git diff --stat
rg -n "(sk-[A-Za-z0-9_-]{20,}|service_role\s*=|WECOM_SECRET\s*=\S+)" docs scripts ai-review
```

Expected: no whitespace errors and no real-looking secret assignment.

- [ ] **Step 4: Commit integration corrections and the Review report**

```powershell
git add -- docs scripts ai-review
git commit -m "docs: complete the WealthSail L0-L4 design set"
```
