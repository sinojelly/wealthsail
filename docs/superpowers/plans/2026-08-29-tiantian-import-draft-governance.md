# 天天基金导入草稿治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户无需重新上传 ZIP 即可查看、确认或取消草稿，并让相同 ZIP 的恢复、已确认拦截和原子重新解析具有可解释、可审计、并发安全的确定性行为。

**Architecture:** 保留 `DRAFT / CONFIRMED / CANCELLED` 三态，把新鲜度作为运行时派生值；由后端在读取、重复上传和确认三个入口统一计算。重复策略由服务层编排，数据库仓储在同一事务和行锁下完成旧草稿取消与新草稿落库；前端只消费显式的 `freshness`、`availableActions` 和 `previewDisposition`，不自行猜测业务状态。

**Tech Stack:** Python 3.12、FastAPI、asyncpg、PostgreSQL/Supabase、React 19、TypeScript、TanStack Query、Vitest、pytest。

**Spec:** `docs/17.WealthSail导入批次状态幂等与草稿治理设计.md`

## Global Constraints

- 第一阶段只实现草稿发现、恢复、确认、取消、确定性 `AUTO`、显式 `RESTORE / REPARSE` 和原子替换。
- 不实现 `logical_package_hash`、7 天自动过期、取消批次明细压缩和治理监控。
- 服务端不持久化原始私密 ZIP；重新解析必须由用户重新选择原 ZIP。
- `freshness` 只允许 `FRESH / STALE`，不能把来源绑定冲突混入新鲜度。
- `validationStatus`、来源绑定状态、`freshness` 必须分别表达；只有 `DRAFT + VALID + FRESH` 且绑定可用时才允许确认。
- `CONFIRMED / CANCELLED` 不可逆；重新解析必须新建 batch id，不能原地复活旧批次。
- 所有数据库变更使用前向迁移并纳入 `scripts/migrate.py` readback；不得修改已经发布的历史迁移。
- 每个生产代码变更先写失败测试，按 Red-Green-Refactor 迭代。
- 保留工作区中用户已有的未跟踪 `docs/16.WealthSail系统架构五视图.md` 和 `docs/images/`，不得纳入提交。

## File Map

- `migrations/030_import_draft_governance.sql`：批次取消原因、替代链和约束、索引、权限。
- `scripts/migrate.py`：迁移后 readback，验证 030 的列、约束和外键。
- `backend/portfolio/imports/freshness.py`：纯函数形式的新鲜度、原因和可用动作计算。
- `backend/portfolio/imports/repository.py`：读取新鲜度上下文、状态筛选、锁定重复批次、原子替换草稿。
- `backend/portfolio/imports/service.py`：真实预览引擎版本、重复策略矩阵、API DTO 装饰、确认前新鲜度重检。
- `backend/portfolio/api.py`：`duplicatePolicy` 表单字段和 `status=DRAFT` 查询参数。
- `frontend/src/api/types.ts`、`portfolio.ts`、`hooks.ts`：新契约和查询参数。
- `frontend/src/features/onboarding/components/TiantianImportWizard.tsx`：待处理草稿优先展示、重新解析选择和 disposition 提示。
- `frontend/src/features/onboarding/components/ImportConfirmStep.tsx`：过期草稿阻断和原因/动作。
- `docs/14.天天基金数据导入.md`、`docs/17.WealthSail导入批次状态幂等与草稿治理设计.md`、`docs/11.数据库升级方法.md`：用户操作、实施状态和升级命令。

---

### Task 1: 数据库保存取消原因与草稿替代链

**Files:**
- Create: `migrations/030_import_draft_governance.sql`
- Modify: `scripts/migrate.py`
- Modify: `tests/unit/test_migration_runner.py`
- Create: `tests/unit/test_import_draft_governance_schema_contract.py`

**Interfaces:**
- Produces: `ledger.portfolio_import_batches.terminal_reason text NULL`
- Produces: `ledger.portfolio_import_batches.supersedes_batch_id uuid NULL`
- Constraint: `terminal_reason IN ('USER_CANCELLED', 'SUPERSEDED_BY_REPARSE', 'EXPIRED')`
- Constraint: `supersedes_batch_id` 是同租户自引用外键 `(tenant_id, supersedes_batch_id) -> (tenant_id, id)`，且不能等于自身。

- [ ] **Step 1: 写迁移契约失败测试**

```python
def test_030_migration_adds_terminal_reason_and_supersession_chain() -> None:
    sql = (ROOT / "migrations/030_import_draft_governance.sql").read_text("utf-8")
    for token in (
        "terminal_reason",
        "USER_CANCELLED",
        "SUPERSEDED_BY_REPARSE",
        "EXPIRED",
        "supersedes_batch_id",
        "FOREIGN KEY (tenant_id, supersedes_batch_id)",
        "CHECK (supersedes_batch_id IS NULL OR supersedes_batch_id <> id)",
    ):
        assert token in sql
```

- [ ] **Step 2: 运行测试并确认因 030 不存在而失败**

Run: `python -m pytest tests/unit/test_import_draft_governance_schema_contract.py tests/unit/test_migration_runner.py -q`

Expected: FAIL，指出 `030_import_draft_governance.sql` 缺失或最新迁移版本仍为 29。

- [ ] **Step 3: 实现前向迁移与 readback**

```sql
ALTER TABLE ledger.portfolio_import_batches
    ADD COLUMN terminal_reason text,
    ADD COLUMN supersedes_batch_id uuid;

ALTER TABLE ledger.portfolio_import_batches
    ADD CONSTRAINT portfolio_import_batches_terminal_reason_check
        CHECK (terminal_reason IS NULL OR terminal_reason IN
               ('USER_CANCELLED', 'SUPERSEDED_BY_REPARSE', 'EXPIRED')),
    ADD CONSTRAINT portfolio_import_batches_not_self_superseding_check
        CHECK (supersedes_batch_id IS NULL OR supersedes_batch_id <> id),
    ADD CONSTRAINT fk_portfolio_import_batches_supersedes
        FOREIGN KEY (tenant_id, supersedes_batch_id)
        REFERENCES ledger.portfolio_import_batches (tenant_id, id);
```

迁移同时更新终态一致性约束：`DRAFT / CONFIRMED` 的 `terminal_reason` 必须为空，`CANCELLED` 必须非空；将既有 `CANCELLED` 回填为 `USER_CANCELLED` 后再启用约束。API 角色只获得这两个列的必要更新/插入权限。`scripts/migrate.py` readback 查询 `information_schema.columns`、`pg_constraint`，失败时给出可执行错误。

- [ ] **Step 4: 运行迁移契约测试**

Run: `python -m pytest tests/unit/test_import_draft_governance_schema_contract.py tests/unit/test_migration_runner.py -q`

Expected: PASS。

- [ ] **Step 5: 提交迁移切片**

```powershell
git add migrations/030_import_draft_governance.sql scripts/migrate.py tests/unit/test_import_draft_governance_schema_contract.py tests/unit/test_migration_runner.py
git commit -m "feat(import): 增加草稿取消原因与替代链"
```

### Task 2: 统一派生新鲜度与可用动作

**Files:**
- Create: `backend/portfolio/imports/freshness.py`
- Create: `tests/unit/test_tiantian_import_freshness.py`
- Modify: `backend/portfolio/imports/repository.py`
- Modify: `tests/unit/test_tiantian_import_repository.py`

**Interfaces:**
- Produces: `FreshnessResult(freshness: Literal['FRESH', 'STALE'], reasons: tuple[str, ...])`
- Produces: `evaluate_freshness(stored_account_hash, current_account_hash, stored_engine_version, current_engine_version, source_as_of, latest_confirmed_source_as_of) -> FreshnessResult`
- Produces: `available_actions(status, validation_status, freshness, binding_status) -> tuple[Literal['VIEW','CONFIRM','CANCEL','REPARSE'], ...]`
- Produces: `repository.load_freshness_context(conn, tenant_id, batch_id, for_update=False) -> asyncpg.Record | None`
- Produces: `repository.list_import_batches(conn, tenant_id, *, source_type: str, status: str | None, cursor: datetime | None, limit: int) -> dict[str, Any]`

- [ ] **Step 1: 写纯函数和仓储失败测试**

```python
def test_engine_change_makes_draft_stale() -> None:
    result = evaluate_freshness(
        stored_account_hash="facts-v1",
        current_account_hash="facts-v1",
        stored_engine_version="tiantian-preview/3",
        current_engine_version="tiantian-preview/4",
        source_as_of=datetime(2026, 8, 1, tzinfo=UTC),
        latest_confirmed_source_as_of=None,
    )
    assert result.freshness == "STALE"
    assert result.reasons == ("PREVIEW_ENGINE_CHANGED",)

def test_fresh_valid_draft_can_view_confirm_cancel_and_reparse() -> None:
    assert available_actions("DRAFT", "VALID", "FRESH", "ACTIVE") == (
        "VIEW", "CONFIRM", "CANCEL", "REPARSE"
    )
```

仓储测试必须断言 `status='DRAFT'` 进入 SQL 条件而不是在 Python 过滤，并断言查询返回 `target_account_state_hash`、`parser_version`、`latest_confirmed_source_as_of` 和绑定状态。

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/unit/test_tiantian_import_freshness.py tests/unit/test_tiantian_import_repository.py -q`

Expected: FAIL，指出 `freshness.py` 或新参数不存在。

- [ ] **Step 3: 实现确定性纯函数和最小仓储查询**

```python
@dataclass(frozen=True, slots=True)
class FreshnessResult:
    freshness: Literal["FRESH", "STALE"]
    reasons: tuple[str, ...]

def evaluate_freshness(*, stored_account_hash: str, current_account_hash: str,
                       stored_engine_version: str, current_engine_version: str,
                       source_as_of: datetime,
                       latest_confirmed_source_as_of: datetime | None) -> FreshnessResult:
    reasons: list[str] = []
    if stored_account_hash != current_account_hash:
        reasons.append("ACCOUNT_FACTS_CHANGED")
    if stored_engine_version != current_engine_version:
        reasons.append("PREVIEW_ENGINE_CHANGED")
    if latest_confirmed_source_as_of and latest_confirmed_source_as_of > source_as_of:
        reasons.append("NEWER_SOURCE_SNAPSHOT_CONFIRMED")
    return FreshnessResult("STALE" if reasons else "FRESH", tuple(reasons))
```

`available_actions` 必须始终允许查看；仅 DRAFT 允许取消/重新解析；确认还要求 `VALID + FRESH + binding in (PENDING, ACTIVE)`。

- [ ] **Step 4: 运行并重构**

Run: `python -m pytest tests/unit/test_tiantian_import_freshness.py tests/unit/test_tiantian_import_repository.py -q`

Expected: PASS；纯函数不依赖数据库或 FastAPI。

- [ ] **Step 5: 提交新鲜度切片**

```powershell
git add backend/portfolio/imports/freshness.py backend/portfolio/imports/repository.py tests/unit/test_tiantian_import_freshness.py tests/unit/test_tiantian_import_repository.py
git commit -m "feat(import): 统一派生草稿新鲜度与可用动作"
```

### Task 3: 扩展读取 API 并在确认前重检新鲜度

**Files:**
- Modify: `backend/portfolio/imports/service.py`
- Modify: `backend/portfolio/imports/repository.py`
- Modify: `backend/portfolio/api.py`
- Modify: `tests/unit/test_tiantian_import_service.py`
- Modify: `tests/unit/test_tiantian_import_repository.py`
- Modify: `tests/unit/test_tiantian_import_api.py`

**Interfaces:**
- Produces DTO fields: `freshness`, `freshnessReasons`, `availableActions`, `terminalReason`, `supersedesBatchId`。
- Produces query: `GET /v1/portfolio/imports?sourceType=TIANTIAN_FUND_ZIP&status=DRAFT`。
- Changes: `confirm_draft(conn, *, tenant_id: str, batch_id: str, expected_version: int, decisions: dict[str, date], confirm_empty: bool, idempotency_key: str, command_content_hash: str, current_preview_engine_version: str)` 在批次和绑定行锁、账户 advisory lock 内重检三类 stale 原因。
- Defines: `PREVIEW_ENGINE_VERSION = "tiantian-preview/3.0.0"`，替代宽泛的 `PARSER_VERSION`。

- [ ] **Step 1: 写读取与确认失败测试**

```python
async def test_confirm_rejects_stale_engine_under_lock() -> None:
    with pytest.raises(VersionConflictError, match="预览引擎已升级"):
        await confirm_draft(
            conn,
            tenant_id=TENANT_ID,
            batch_id=BATCH_ID,
            expected_version=1,
            decisions={},
            confirm_empty=False,
            idempotency_key="confirm-1",
            command_content_hash="hash",
            current_preview_engine_version="tiantian-preview/3.0.0",
        )
```

API 测试断言 `status=CONFIRMED` 返回 422，`status=DRAFT` 被传给 service；详情/列表测试断言新鲜度、原因和动作字段完整。

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/unit/test_tiantian_import_api.py tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py -q`

Expected: FAIL，指出读取 DTO 缺字段或 `confirm_draft` 缺版本参数。

- [ ] **Step 3: 实现读取装饰和锁内确认校验**

```python
PREVIEW_ENGINE_VERSION = "tiantian-preview/3.0.0"

async def _decorate_import_detail(conn, tenant_id: str, detail: dict[str, Any]) -> dict[str, Any]:
    context = await repository.load_freshness_context(
        conn, tenant_id=tenant_id, batch_id=detail["batchId"]
    )
    result = await _evaluate_context_freshness(conn, tenant_id, context)
    return {
        **detail,
        "freshness": result.freshness,
        "freshnessReasons": list(result.reasons),
        "availableActions": list(available_actions(
            detail["status"], detail["validationStatus"],
            result.freshness, detail["sourceBinding"]["status"]
        )),
    }
```

确认路径不得信任详情 API 的旧结果，必须在写事务中重新读取 `target_account_state_hash / parser_version / source_as_of / latest confirmed source_as_of`；任一 stale 原因返回稳定 409，且不写账本、不改批次状态。

- [ ] **Step 4: 运行后端相关测试**

Run: `python -m pytest tests/unit/test_tiantian_import_api.py tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py -q`

Expected: PASS。

- [ ] **Step 5: 提交读取和确认切片**

```powershell
git add backend/portfolio/api.py backend/portfolio/imports/service.py backend/portfolio/imports/repository.py tests/unit/test_tiantian_import_api.py tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py
git commit -m "feat(import): 暴露草稿新鲜度并阻断过期确认"
```

### Task 4: 实现重复策略矩阵与原子重新解析

**Files:**
- Modify: `backend/portfolio/imports/service.py`
- Modify: `backend/portfolio/imports/repository.py`
- Modify: `backend/portfolio/api.py`
- Modify: `tests/unit/test_tiantian_import_service.py`
- Modify: `tests/unit/test_tiantian_import_repository.py`
- Modify: `tests/unit/test_tiantian_import_api.py`

**Interfaces:**
- Consumes: Task 2 的 `FreshnessResult` 与 Task 3 的 DTO 装饰。
- Produces request field: `duplicatePolicy: Literal['AUTO','RESTORE','REPARSE'] = 'AUTO'`。
- Produces response field: `previewDisposition: Literal['CREATED','RESTORED_DRAFT','ALREADY_CONFIRMED','REPARSED','USER_DECISION_REQUIRED']`。
- Produces: `repository.replace_draft_atomically(conn, *, tenant_id: str, old_batch_id: str, expected_old_version: int, target: TargetAccount, package: TiantianPackage, plan: PreviewPlan, package_hash: str, account_fact_hash: str, idempotency_key: str) -> str`。
- Produces cancel metadata: 用户取消写 `USER_CANCELLED`；重解析替代写 `SUPERSEDED_BY_REPARSE`，新批次 `supersedes_batch_id=old_batch_id`。

- [ ] **Step 1: 写完整策略矩阵失败测试**

```python
def test_auto_restores_a_fresh_valid_draft() -> None:
    assert choose_duplicate_action(
        policy="AUTO", status="DRAFT", freshness="FRESH",
        validation_status="VALID", binding_status="ACTIVE",
    ) == "RESTORED_DRAFT"

def test_auto_returns_already_confirmed_without_replaying() -> None:
    assert choose_duplicate_action(
        policy="AUTO", status="CONFIRMED", freshness="FRESH",
        validation_status="VALID", binding_status="ACTIVE",
    ) == "ALREADY_CONFIRMED"

def test_auto_reparses_a_stale_valid_draft() -> None:
    assert choose_duplicate_action(
        policy="AUTO", status="DRAFT", freshness="STALE",
        validation_status="VALID", binding_status="ACTIVE",
    ) == "REPARSED"

def test_auto_keeps_an_invalid_draft_for_user_decision() -> None:
    assert choose_duplicate_action(
        policy="AUTO", status="DRAFT", freshness="STALE",
        validation_status="INVALID", binding_status="ACTIVE",
    ) == "USER_DECISION_REQUIRED"

def test_explicit_restore_rejects_stale_draft() -> None:
    with pytest.raises(VersionConflictError, match="DRAFT_RESTORE_NOT_ALLOWED"):
        choose_duplicate_action(
            policy="RESTORE", status="DRAFT", freshness="STALE",
            validation_status="VALID", binding_status="ACTIVE",
        )

def test_explicit_reparse_can_replace_an_invalid_draft() -> None:
    assert choose_duplicate_action(
        policy="REPARSE", status="DRAFT", freshness="FRESH",
        validation_status="INVALID", binding_status="ACTIVE",
    ) == "REPARSED"
```

把每一行拆成具名测试，分别断言仓储调用次数、旧批次是否保留、disposition 和异常码；不得只测纯决策函数。另加并发仓储测试，模拟两次 REPARSE 竞争时只有一个旧版本可以更新。

- [ ] **Step 2: 运行失败测试**

Run: `python -m pytest tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py tests/unit/test_tiantian_import_api.py -q`

Expected: FAIL，现有实现总是返回活动批次，不理解 policy/disposition，也没有替代链。

- [ ] **Step 3: 实现策略解析和原子替换**

```python
DuplicatePolicy = Literal["AUTO", "RESTORE", "REPARSE"]

def choose_duplicate_action(*, policy: DuplicatePolicy, status: str,
                            freshness: str, validation_status: str,
                            binding_status: str) -> str:
    if status == "CONFIRMED":
        return "ALREADY_CONFIRMED"
    if policy == "RESTORE":
        if freshness != "FRESH":
            raise VersionConflictError("DRAFT_RESTORE_NOT_ALLOWED")
        return "RESTORED_DRAFT"
    if policy == "REPARSE":
        return "REPARSED"
    if binding_status == "DISABLED" or validation_status == "INVALID":
        return "USER_DECISION_REQUIRED"
    return "RESTORED_DRAFT" if freshness == "FRESH" else "REPARSED"
```

服务层必须先完成 ZIP 安全解析、基金主数据验证和新 plan 计算，再进入数据库事务。REPARSE 事务按顺序：锁来源绑定与旧批次；校验旧版本仍是 DRAFT；写旧批次 `CANCELLED + SUPERSEDED_BY_REPARSE`；创建带 `supersedes_batch_id` 的新 DRAFT；写审计。任一步失败由事务回滚，旧草稿保持原样。

- [ ] **Step 4: 覆盖唯一索引竞争并通过测试**

Run: `python -m pytest tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py tests/unit/test_tiantian_import_api.py -q`

Expected: PASS；断言 `CONFIRMED` 重放不调用正式账本写入，AUTO 新解析无效时不取消旧草稿。

- [ ] **Step 5: 提交幂等与重解析切片**

```powershell
git add backend/portfolio/api.py backend/portfolio/imports/service.py backend/portfolio/imports/repository.py tests/unit/test_tiantian_import_api.py tests/unit/test_tiantian_import_service.py tests/unit/test_tiantian_import_repository.py
git commit -m "feat(import): 实现确定性重复策略与原子重新解析"
```

### Task 5: 前端契约和待处理草稿优先体验

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/portfolio.ts`
- Modify: `frontend/src/api/hooks.ts`
- Modify: `frontend/src/api/portfolio-import.test.ts`
- Modify: `frontend/src/features/onboarding/components/TiantianImportWizard.tsx`
- Modify: `frontend/src/features/onboarding/components/ImportConfirmStep.tsx`
- Modify: `frontend/src/features/onboarding/tiantian-import.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes DTO: `freshness`, `freshnessReasons`, `availableActions`, `previewDisposition`, `terminalReason`, `supersedesBatchId`。
- Produces: `listTiantianImports({ status?: 'DRAFT', cursor?: string, limit?: number })`。
- Produces: `previewTiantianImport(file, target, idempotencyKey, duplicatePolicy: 'AUTO'|'RESTORE'|'REPARSE')`。
- UI copy: “待处理草稿”“继续核对”“取消草稿”“选择原 ZIP 重新解析”“已恢复已有草稿”“该 ZIP 已导入，不会重复写入”。

- [ ] **Step 1: 写 API 契约和页面行为失败测试**

```tsx
it("without uploading lists pending drafts before the upload form", async () => {
  mocks.historyHook.mockReturnValue(queryResult({ items: [draft], nextCursor: null }));
  renderWithSession(<TiantianImportWizard />);
  expect(await screen.findByRole("heading", { name: "待处理草稿" })).toBeVisible();
  expect(screen.getByRole("button", { name: /继续核对/ })).toBeEnabled();
  expect(screen.getByText(/2026-08-29/)).toBeVisible();
});

it("stale draft cannot confirm and asks for the original zip", async () => {
  renderWithSession(<TiantianImportWizard />, staleDraft);
  expect(screen.getByRole("button", { name: "确认导入持仓基线" })).toBeDisabled();
  expect(screen.getByText(/选择原 ZIP 重新解析/)).toBeVisible();
});
```

API 测试断言 multipart 中存在 `duplicatePolicy=REPARSE`，列表请求存在 `status=DRAFT`。

- [ ] **Step 2: 运行失败测试**

Run: `pnpm --dir frontend test -- src/api/portfolio-import.test.ts src/features/onboarding/tiantian-import.test.tsx`

Expected: FAIL，类型/文案/按钮不存在。

- [ ] **Step 3: 实现 DRAFT-first 页面和显式重新解析**

页面加载两组查询：`status=DRAFT` 用于顶部“待处理草稿”，无状态查询用于底部历史。草稿卡展示账户、快照日、更新时间、计数、VALID/INVALID、FRESH/STALE 及中文原因。打开草稿后根据 `availableActions` 决定按钮；点击重新解析只切换上传区到 `REPARSE` 模式并要求选择 ZIP，不假装服务端保存原文件。

`previewDisposition` 的成功提示必须区分五种结果；`USER_DECISION_REQUIRED` 保持旧草稿可见并提供“继续旧草稿”和“重新解析”选择，不自动关闭或覆盖对话框。

- [ ] **Step 4: 运行前端测试、类型检查和 lint**

Run: `pnpm --dir frontend test -- src/api/portfolio-import.test.ts src/features/onboarding/tiantian-import.test.tsx`

Expected: PASS。

Run: `pnpm --dir frontend typecheck`

Expected: PASS。

Run: `pnpm --dir frontend lint`

Expected: PASS。

- [ ] **Step 5: 提交前端闭环**

```powershell
git add frontend/src/api/types.ts frontend/src/api/portfolio.ts frontend/src/api/hooks.ts frontend/src/api/portfolio-import.test.ts frontend/src/features/onboarding/components/TiantianImportWizard.tsx frontend/src/features/onboarding/components/ImportConfirmStep.tsx frontend/src/features/onboarding/tiantian-import.test.tsx frontend/src/styles.css
git commit -m "feat(frontend): 增加待处理草稿与重新解析闭环"
```

### Task 6: 文档、错误提示与全链路回归

**Files:**
- Modify: `docs/14.天天基金数据导入.md`
- Modify: `docs/17.WealthSail导入批次状态幂等与草稿治理设计.md`
- Modify: `docs/11.数据库升级方法.md`
- Modify: `tests/unit/test_tiantian_import_schema_contract.py`
- Modify: `tests/unit/test_tiantian_import_api.py`

**Interfaces:**
- Documents deployment: `python scripts/migrate.py --target 30` 后自动 readback。
- Documents recovery: 无 ZIP 可继续/取消草稿；REPARSE 必须重选 ZIP；相同已确认包不会重复入账。
- Documents stable errors: `DRAFT_RESTORE_NOT_ALLOWED`、`DRAFT_STALE_REPARSE_REQUIRED`、`IMPORT_REPARSE_CONFLICT`。

- [ ] **Step 1: 增加文档契约测试**

```python
def test_governance_docs_match_public_api_contract() -> None:
    design = (ROOT / "docs/17.WealthSail导入批次状态幂等与草稿治理设计.md").read_text("utf-8")
    for token in ("duplicatePolicy", "previewDisposition", "availableActions", "terminal_reason", "supersedes_batch_id"):
        assert token in design
```

- [ ] **Step 2: 更新文档状态和部署步骤**

将 docs/17 状态从“待确认”改为“第一阶段已实现”，列出已实现/第二阶段未实现边界；docs/14 增加用户页面操作和错误恢复；docs/11 增加 030 的执行、readback、回滚限制和验证 SQL，不能只写“执行 migration”。

- [ ] **Step 3: 运行后端全量质量门禁**

Run: `python -m pytest -q`

Expected: 所有测试 PASS，仅保留仓库既有 skip。

Run: `python -m ruff check backend tests scripts`

Expected: PASS。

Run: `python -m compileall -q backend scripts`

Expected: PASS。

- [ ] **Step 4: 运行前端全量质量门禁**

Run: `pnpm --dir frontend test`

Expected: PASS。

Run: `pnpm --dir frontend typecheck`

Expected: PASS。

Run: `pnpm --dir frontend lint`

Expected: PASS。

Run: `pnpm --dir frontend build`

Expected: PASS 并生成 `frontend/dist`。

- [ ] **Step 5: 检查差异、提交文档并同步远端**

```powershell
git diff --check
git status --short
git add docs/11.数据库升级方法.md docs/14.天天基金数据导入.md docs/17.WealthSail导入批次状态幂等与草稿治理设计.md tests/unit/test_tiantian_import_schema_contract.py tests/unit/test_tiantian_import_api.py
git commit -m "docs(import): 完成草稿治理实施与部署说明"
git fetch origin
git rebase origin/master
git push origin master
```

Expected: 推送成功，且未跟踪的 `docs/16.WealthSail系统架构五视图.md`、`docs/images/` 仍未被暂存。

## Self-Review Result

- Spec coverage: 第一阶段的无上传草稿列表、直接确认/取消、动态新鲜度、确认重检、三种 duplicate policy、五种 disposition、AUTO 矩阵、原子 REPARSE、替代链、前端中文提示、数据库/后端/前端/并发测试均已映射到任务。
- Explicit exclusions: 逻辑包哈希、自动过期、明细压缩和治理监控只保留在第二阶段，不在本计划中偷偷扩展。
- Placeholder scan: 计划不含 `TBD`、`TODO` 或未定义接口；参数名与设计文档保持 `duplicatePolicy`、`previewDisposition`、`freshnessReasons`、`availableActions` 一致。
- Type consistency: 后端 snake_case 仅用于 Python/SQL，公开 DTO 使用 camelCase；`FreshnessResult` 和策略枚举在 Task 2/4 定义后由 Task 3/5 消费。
- Transaction safety: 新 ZIP 在数据库事务前完成安全解析和外部主数据处理；旧草稿终止、新草稿创建和审计在同一事务中，失败时不会破坏旧草稿。
