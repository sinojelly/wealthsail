# Standard Report Job Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让既有租户和运维迁移创建的个人租户都安全、幂等地获得 PRE_MARKET、INTRADAY、FUND_1430、POST_MARKET 四个标准报告任务。

**Architecture:** 将四任务的名称、Cron 和 session 定义集中到一个 Python 模块，供个人租户迁移脚本和受控回填脚本复用；数据库邀请函数仍在 migration 033 中原子初始化。回填命令只接受 migration/admin DSN，默认只读预检，遇到部分、禁用、改时或重复配置整体停止，不覆盖人工配置。

**Tech Stack:** Python 3.12、asyncpg、PostgreSQL、pytest、现有 forward-only migration/运维命令模式。

**Spec:** `docs/10.WealthSail-MVP前后端实现与系统部署规范.md` 第 6 章；`docs/18.WealthSail租户用户账户与资产作用域设计.md` 第 8 章；migration 033/035 的权限边界。

## Global Constraints

- 四个合法 session 只能是 `PRE_MARKET / INTRADAY / FUND_1430 / POST_MARKET`。
- 回填不得修改已有任务；任何非完整、非标准状态都必须停止并要求人工复核。
- 默认模式不得写数据库；`--apply` 必须同时确认数据库目标、租户 UUID 和租户名称。
- API、browser 和 authenticated 角色不得获得 `workflow.jobs` INSERT 权限。
- 写入四个任务和一条审计记录必须处于同一事务，并在事务内回读验证。

---

### Task 1: Canonical report-job definition

**Files:**
- Create: `scripts/standard_report_jobs.py`
- Test: `tests/unit/test_standard_report_job_backfill_command.py`

**Interfaces:**
- Produces: `STANDARD_REPORT_JOBS: tuple[StandardReportJob, ...]`
- Produces: `canonical_job_rows() -> list[tuple[str, str, str]]`

- [ ] **Step 1: Write the failing test**

```python
def test_canonical_schedule_contains_four_unique_sessions():
    assert [job.session for job in STANDARD_REPORT_JOBS] == [
        "PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"
    ]
    assert len({job.name for job in STANDARD_REPORT_JOBS}) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_standard_report_job_backfill_command.py -q`

Expected: import fails because `scripts.standard_report_jobs` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class StandardReportJob:
    name: str
    cron: str
    session: str

STANDARD_REPORT_JOBS = (
    StandardReportJob("pre_market_brief", "0 30 8 * * 1-5", "PRE_MARKET"),
    StandardReportJob("intraday_tracking", "*/5 9-14 * * 1-5", "INTRADAY"),
    StandardReportJob("fund_1430_estimate", "0 30 14 * * 1-5", "FUND_1430"),
    StandardReportJob("post_market_review", "0 30 15 * * 1-5", "POST_MARKET"),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_standard_report_job_backfill_command.py -q`

Expected: PASS.
### Task 2: Controlled four-job backfill command

**Files:**
- Create: `scripts/backfill_standard_report_jobs.py`
- Test: `tests/unit/test_standard_report_job_backfill_command.py`

**Interfaces:**
- Consumes: `STANDARD_REPORT_JOBS`
- Produces: `BackfillRequest(tenant_id: UUID, tenant_name: str)`
- Produces: `backfill_standard_report_jobs(conn, request, apply: bool) -> dict`

- [ ] **Step 1: Write failing behavior tests**

```python
async def test_apply_inserts_all_four_jobs_and_one_audit_record():
    result = await backfill_standard_report_jobs(conn, request, apply=True)
    assert result["status"] == "APPLIED"
    assert {row["report_session"] for row in conn.jobs} == {
        "PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"
    }
    assert len(conn.audit_rows) == 1

async def test_partial_existing_schedule_aborts_without_writes():
    with pytest.raises(RuntimeError, match="manual review"):
        await backfill_standard_report_jobs(partial_conn, request, apply=True)
```

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_standard_report_job_backfill_command.py -q`

Expected: FAIL because the command is missing.

- [ ] **Step 3: Implement preflight/apply/readback**

The command must lock the exact ACTIVE tenant, load all four sessions, return `READY` for zero rows, `ALREADY_APPLIED` only for four exact canonical rows, and raise for every other state. Apply uses one multi-row INSERT and one `REPORT_JOBS_BACKFILLED` audit row whose details list all four sessions.

- [ ] **Step 4: Add CLI boundary tests and implementation**

The tests launch `python scripts/backfill_standard_report_jobs.py --help`, reject runtime DB roles, require migration 033, and require exact apply confirmations. The command connects with `statement_cache_size=0` and never prints the DSN.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_standard_report_job_backfill_command.py -q`

Expected: all command tests pass.

### Task 3: Personal-tenant migration creates schedules atomically

**Files:**
- Modify: `scripts/migrate_user_to_personal_tenant.py`
- Modify: `tests/unit/test_personal_tenant_migration_command.py`

**Interfaces:**
- Consumes: `STANDARD_REPORT_JOBS`
- Preserves: `migrate_user(conn, request, apply: bool) -> dict`

- [ ] **Step 1: Write the failing test**

```python
async def test_apply_initializes_all_standard_report_jobs():
    result = await migrate_user(conn, request, apply=True)
    assert result["reportSchedulesInitialized"] is True
    assert conn.inserted_sessions == {
        "PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"
    }
```

- [ ] **Step 2: Verify RED**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_personal_tenant_migration_command.py -q`

Expected: missing result field and no workflow.jobs insertion.

- [ ] **Step 3: Implement the transactional insert**

After tenant/member creation and before session revocation, insert the four canonical jobs inside the existing transaction. Include `reportSchedulesInitialized: true` and the sessions in `PERSONAL_TENANT_CREATED` audit details.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_personal_tenant_migration_command.py tests/unit/test_standard_report_job_backfill_command.py -q`

Expected: PASS and the source tenant financial-fact counts remain unchanged.

### Task 4: Operator documentation and verification

**Files:**
- Modify: `docs/11.数据库升级方法.md`
- Modify: `docs/18.WealthSail租户用户账户与资产作用域设计.md`

- [ ] **Step 1: Document exact preflight/apply/readback commands**

Document Jelly's tenant UUID/name placeholders, exact target confirmations, the four-row SQL readback, audit readback, and environment-variable cleanup. State that no report is sent merely by inserting schedules; the Worker triggers them only when due.

- [ ] **Step 2: Run verification**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_standard_report_job_backfill_command.py tests/unit/test_personal_tenant_migration_command.py tests/unit/test_migration_runner.py -q`

Expected: PASS.
