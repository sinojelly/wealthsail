# 天天基金申赎状态同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每个交易日盘前生成前，同步所有当前持有场外基金的真实申购和赎回状态。

**Architecture:** 纯适配器一次读取天天基金结构化状态列表，保守映射为 `OPEN` 或 `SUSPENDED`。交易日 06:05 的全局 Worker 按参考标的去重同步，再经 `SECURITY DEFINER` 函数保留状态生效区间；现有盘前报告继续只读 `reference.fund_profiles`。

**Tech Stack:** Python 3.12、asyncio、AkShare、asyncpg、PostgreSQL、pytest、Ruff。

**Spec:** `docs/superpowers/specs/2026-09-09-fund-subscription-status-design.md`

## Global Constraints

- 只使用 AkShare `fund_purchase_em` 的天天基金公开结构化状态列表，不抓基金分红或公告正文。
- 只处理 `OFF_EXCHANGE_FUND` 的正数量、活跃持仓，跨租户按参考标的去重。
- “暂停”或“限制”映射 `SUSPENDED`；“开放”或“正常”映射 `OPEN`；未知状态不写入。
- Worker 不得直接 INSERT、UPDATE 或 DELETE `reference.fund_profiles`；仅能执行受控函数。
- 同步不创建或修改 `workflow.jobs`；单只基金失败不得阻断报告。
- 缺失、未知或抓取失败绝不写成 `OPEN`。
- 每个任务必须先写并运行失败测试，再写最小实现、验证并提交；提交说明使用中文。

---

### Task 1：建立基金申赎状态的受控数据库写入契约

**Files:**
- Create: `migrations/061_tiantian_fund_subscription_status.sql`
- Modify: `tests/unit/test_migration_runner.py`
- Modify: `tests/unit/test_migration_sql_syntax.py`

**Interfaces:**
- Produces: `reference.upsert_fund_profile_status(UUID, TEXT, TEXT, DATE, TEXT, JSONB, TIMESTAMPTZ) RETURNS BOOLEAN`。
- Produces: 活跃数据源 `TIANTIAN_FUND_STATUS`。

- [ ] **Step 1: Write the failing contract test**

```python
def test_061_limits_worker_to_the_controlled_fund_profile_function() -> None:
    migration = (root / "migrations" / "061_tiantian_fund_subscription_status.sql").read_text(encoding="utf-8")
    assert "TIANTIAN_FUND_STATUS" in migration
    assert "CREATE OR REPLACE FUNCTION reference.upsert_fund_profile_status" in migration
    assert "SECURITY DEFINER" in migration
    assert "SET search_path = reference, pg_catalog" in migration
    assert "GRANT EXECUTE ON FUNCTION reference.upsert_fund_profile_status" in migration
    assert "GRANT INSERT ON reference.fund_profiles TO wealthsail_worker" not in migration
```

- [ ] **Step 2: Verify red**

Run `python -m pytest tests/unit/test_migration_runner.py tests/unit/test_migration_sql_syntax.py -q`.

Expected: the test fails because migration 061 is absent.

- [ ] **Step 3: Add migration 061**

Add source `TIANTIAN_FUND_STATUS`; add `source_ref TEXT`, `source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb`, and `retrieved_at TIMESTAMPTZ` to `reference.fund_profiles`. Implement this signature:

```sql
reference.upsert_fund_profile_status(
    p_instrument_id UUID, p_purchase_status TEXT, p_redemption_status TEXT,
    p_effective_from DATE, p_source_ref TEXT, p_source_metadata JSONB,
    p_retrieved_at TIMESTAMPTZ
) RETURNS BOOLEAN
```

The fixed-search-path `SECURITY DEFINER` function rejects null inputs, invalid states, blank source references, non-object metadata, and a missing or inactive source. If both latest statuses are unchanged it refreshes source metadata and returns `FALSE`; otherwise it closes the previous open interval on the preceding day, inserts the new status interval and returns `TRUE`. Revoke Worker table writes and grant only function execution.

- [ ] **Step 4: Verify green and commit**

Run `python -m pytest tests/unit/test_migration_runner.py tests/unit/test_migration_sql_syntax.py -q`; then commit the migration and its tests with `feat：建立基金申赎状态受控写入契约`.

### Task 2：实现天天基金状态适配器

**Files:**
- Create: `backend/integrations/fund_subscription_status.py`
- Create: `tests/unit/test_fund_subscription_status_integration.py`

**Interfaces:**
- Produces: `FundSubscriptionStatus(symbol, purchase_status, redemption_status, source_ref, metadata)`。
- Produces: `async fetch_fund_subscription_status(symbols, *, provider=None) -> dict[str, FundSubscriptionStatus]`。
- Consumes: AkShare `fund_purchase_em` only; it has no database access.

- [ ] **Step 1: Write failing behavior tests**

```python
@pytest.mark.asyncio
async def test_fetch_filters_held_symbols_and_maps_verified_statuses() -> None:
    statuses = await fetch_fund_subscription_status(["110022", "012733"], provider=_Provider())
    assert statuses["110022"].purchase_status == "SUSPENDED"
    assert statuses["110022"].redemption_status == "OPEN"
    assert statuses["012733"].purchase_status == "OPEN"
```

Fixtures include `限制大额申购`, unknown text, unrelated code and no rows. Assert the unknown-status fund is omitted, never represented as `OPEN`.

- [ ] **Step 2: Verify red**

Run `python -m pytest tests/unit/test_fund_subscription_status_integration.py -q`.

Expected: import failure because the adapter does not exist.

- [ ] **Step 3: Implement the minimal pure adapter**

Call `provider.fund_purchase_em` in a bounded `asyncio.to_thread`/`asyncio.wait_for` operation. Normalize six-digit `基金代码`, `申购状态`, `赎回状态`, `下一开放日`, and fund name. Preserve the raw purchase/redemption values, next-open date and name in metadata; use `akshare/fund_purchase_em` as `source_ref`. Provider failures and changed columns return an empty mapping.

- [ ] **Step 4: Verify green and commit**

Run `python -m pytest tests/unit/test_fund_subscription_status_integration.py -q` and `python -m ruff check backend/integrations/fund_subscription_status.py tests/unit/test_fund_subscription_status_integration.py`; commit with `feat：接入天天基金申赎状态数据源`.

### Task 3：实现全局同步任务与交易日 06:05 调度

**Files:**
- Create: `backend/jobs/fund_subscription_status_sync.py`
- Modify: `backend/jobs/scheduler.py`
- Modify: `backend/worker.py`
- Modify: `tests/unit/test_corporate_actions_schedule.py`
- Create: `tests/unit/test_fund_subscription_status_sync.py`

**Interfaces:**
- Consumes: Task 2 adapter and Task 1 function.
- Produces: `sync_fund_subscription_status(pool, now, *, fetcher=None, writer=None) -> FundSubscriptionStatusSyncStats`.
- Produces: `FUND_SUBSCRIPTION_STATUS_REFRESH` at 06:05 Asia/Shanghai.

- [ ] **Step 1: Write failing sync and schedule tests**

```python
@pytest.mark.asyncio
async def test_sync_deduplicates_held_funds_and_persists_recognized_statuses() -> None:
    stats = await sync_fund_subscription_status(pool, shanghai_0605, fetcher=fake_fetcher, writer=fake_writer)
    assert fetched_symbols == [("110022",)]
    assert persisted == [("fund-1", "SUSPENDED", "OPEN")]
    assert stats.changed == 1

def test_subscription_status_refresh_is_due_at_six_oh_five() -> None:
    assert due_at("FUND_SUBSCRIPTION_STATUS_REFRESH", shanghai_0605)
    assert not due_at("FUND_SUBSCRIPTION_STATUS_REFRESH", shanghai_0600)
```

The connection fake contains the same fund across two tenants and one omitted unknown status. Add a Worker assertion that this phase calls only the global sync, not `refresh_market` or `runner.run_session`.

- [ ] **Step 2: Verify red**

Run `python -m pytest tests/unit/test_fund_subscription_status_sync.py tests/unit/test_corporate_actions_schedule.py -q`.

Expected: missing sync module and phase failures.

- [ ] **Step 3: Implement global synchronization**

Load only latest positive `ledger.position_snapshots` for active `OFF_EXCHANGE_FUND` instruments and ticker aliases, deduplicate by `instrument_id`, and fetch once. Persist each recognized status by calling:

```python
await conn.fetchval(
    "SELECT reference.upsert_fund_profile_status($1::uuid, $2, $3, $4, $5, $6::jsonb, $7)",
    instrument_id, status.purchase_status, status.redemption_status,
    now.date(), status.source_ref, json.dumps(status.metadata, ensure_ascii=False), now,
)
```

Count `scanned`, `available`, `changed`, `unchanged`, `unavailable`, and `failed`. Isolate failures per instrument. Add `SessionSchedule("FUND_SUBSCRIPTION_STATUS_REFRESH", "基金申赎状态同步", minute=5, hour=6)` and a Worker early-return branch that logs the aggregate and never creates report jobs.

- [ ] **Step 4: Verify green and commit**

Run `python -m pytest tests/unit/test_fund_subscription_status_sync.py tests/unit/test_corporate_actions_schedule.py tests/unit/test_pre_market_report.py tests/unit/test_pre_market_personalized.py -q`; commit with `feat：交易日同步基金申赎状态`.

### Task 4：补充运维文档并执行全量验证

**Files:**
- Modify: `docs/11.数据库升级方法.md`
- Modify: `scripts/migrate.py`
- Modify: `tests/unit/test_migration_runner.py`

**Interfaces:**
- Consumes: migration 061, Worker logs and the existing migration runner.
- Produces: credential-free deployment and troubleshooting instructions.

- [ ] **Step 1: Extend migration readback for the new privilege boundary**

Add a migration-runner readback query that verifies the 061 function exists,
the Worker has `EXECUTE` on it, and the Worker lacks direct `INSERT`,
`UPDATE`, and `DELETE` rights on `reference.fund_profiles`. Add an executable
unit test asserting this readback contract.

- [ ] **Step 2: Document production verification**

Add migration 061 purpose, dry-run and execution commands, Worker restart, expected `dispatching FUND_SUBSCRIPTION_STATUS_REFRESH` and `fund_subscription_status_sync_complete` log markers, read-only effective-profile query, and administrator-triggered PRE_MARKET personalized report verification. State that an unavailable or unknown source status is omitted rather than assumed open.

- [ ] **Step 3: Run full quality gate**

Run `python -m ruff check backend tests/unit` and `python -m pytest -q`.

Expected: Ruff exits 0 and all tests pass; record warnings without calling them test failures.

- [ ] **Step 4: Final review, commit, and push**

Run `git diff --check` and `git status --short`. Commit the runbook with `docs：补充基金申赎状态同步运维说明`, then `git push origin master`. Do not stage user-owned `.tmp/`, `.trae/`, or `docs/learning-plan.md`.
