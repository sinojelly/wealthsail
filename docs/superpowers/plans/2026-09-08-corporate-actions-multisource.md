# 多源公司行动自动抓取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每个 A 股交易日 06:00，为所有用户的 A 股和场内基金持仓同步公司行动，并让盘前简报展示可审计、带质量状态的真实事件。

**Architecture:** 以 `market.corporate_actions` 作为唯一事实表。Worker 调度全局同步任务，协调器收集持仓标的后调用 AkShare 与 baostock；结果通过受控数据库函数幂等写入并保留来源与质量。盘前简报只消费该表中有效事件，场外基金继续通过人工 CSV 导入。

**Tech Stack:** Python 3.12、asyncpg、AkShare、baostock、PostgreSQL、pytest、Asia/Shanghai 调度。

**Spec:** `docs/superpowers/specs/2026-09-08-corporate-actions-multisource-design.md`

## 全局约束

- 自动范围只包括 A 股和场内基金；场外基金不自动抓取。
- 每个交易日 06:00 执行；周末、法定休市日不得执行。
- 所有持久化写入必须经迁移新增的受控函数，Worker 不获得表的直接写权限。
- 自动事件按标的、除权日去重；来源冲突时保留审计信息并标记为 `CONFLICT`。
- 任一数据源异常都不能阻塞 Worker 的其它报告、行情和 outbox 工作。
- 既有人工 CSV 导入与研报页面结构保持兼容。

---

## Task 1：扩展数据库契约、权限与迁移校验

**Files:**

- Create: `migrations/058_corporate_actions_multisource_sync.sql`
- Modify: `scripts/migrate.py`
- Modify: `tests/unit/test_migration_runner.py`
- Create: `tests/unit/test_corporate_actions_migration_contract.py`

- [ ] **Step 1: 写入失败的迁移契约测试。**

  断言最新迁移为 058，SQL 为 `market.corporate_actions` 添加 `quality_status` 与 `verified_by`，注册两个数据源，并创建仅由迁移所有者写表的受控函数。断言 Worker 获得函数 `EXECUTE`，却没有表的 `INSERT`、`UPDATE` 或 `DELETE` 权限。

- [ ] **Step 2: 运行该测试，确认当前失败。**

  Run: `pytest tests/unit/test_migration_runner.py tests/unit/test_corporate_actions_migration_contract.py -q`

  Expected: 失败原因只涉及缺少 058 迁移或相应 SQL 契约。

- [ ] **Step 3: 新增迁移。**

  迁移应：

  1. 为现有行回填 `quality_status = 'FRESH'`，默认 `verified_by = ARRAY['MANUAL']::text[]`；
  2. 校验质量状态只能为 `FRESH`、`STALE`、`UNAVAILABLE`、`CONFLICT`；
  3. 为 `market.data_sources` 注册 `AKSHARE_CORPORATE_ACTIONS` 与 `BAOSTOCK_CORPORATE_ACTIONS`；
  4. 创建 `market.upsert_corporate_action_sync(...)`，以 `source_event_id + revision` 实现幂等写入，安全合并 `verified_by`，并记录 `source_ref`；
  5. 只向 Worker 角色授予该函数的 `EXECUTE`，保持公司行动表的直接写权限被撤销。

- [ ] **Step 4: 扩展迁移读回校验。**

  在 `scripts/migrate.py` 的读回阶段验证字段、质量约束、两个数据源、函数和运行时授权，失败时给出明确错误。

- [ ] **Step 5: 再次运行测试。**

  Run: `pytest tests/unit/test_migration_runner.py tests/unit/test_corporate_actions_migration_contract.py -q`

  Expected: 全部通过。

- [ ] **Step 6: 提交迁移契约。**

  ```powershell
  git add migrations/058_corporate_actions_multisource_sync.sql scripts/migrate.py tests/unit/test_migration_runner.py tests/unit/test_corporate_actions_migration_contract.py
  git commit -m "feat：建立公司行动多源同步数据库契约"
  ```

## Task 2：封装 baostock 分红查询

**Files:**

- Create: `backend/integrations/baostock_adapter.py`
- Create: `tests/unit/test_baostock_adapter.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 编写失败测试。**

  覆盖证券代码转换：`600519 -> sh.600519`、`000001 -> sz.000001`、`510300 -> sh.510300`；无法识别的代码返回空值。模拟 baostock 登录、分页读取、登出，验证同一批查询只登录和退出一次，且将除权日、登记日、派息日映射为规范字段。

- [ ] **Step 2: 运行测试，确认失败。**

  Run: `pytest tests/unit/test_baostock_adapter.py -q`

  Expected: 因适配器尚不存在而失败。

- [ ] **Step 3: 实现适配器。**

  提供 `to_baostock_code(symbol: str) -> str | None` 和批次上下文管理器。使用 `query_dividend_data(code, year, yearType='operate')` 查询当前年与下一年。将 baostock 登录、返回码、分页或字段异常转为领域异常，不让 baostock 对象泄漏到上层。

- [ ] **Step 4: 添加依赖并运行测试。**

  在 `requirements.txt` 固定 `baostock==0.9.3`，然后运行：

  `pytest tests/unit/test_baostock_adapter.py -q`

  Expected: 全部通过。

- [ ] **Step 5: 提交适配器。**

  ```powershell
  git add backend/integrations/baostock_adapter.py tests/unit/test_baostock_adapter.py requirements.txt
  git commit -m "feat：新增 baostock 公司行动数据适配器"
  ```

## Task 3：实现双源归一化、合并与质量判定

**Files:**

- Create: `backend/integrations/corporate_actions.py`
- Create: `tests/unit/test_corporate_actions_integration.py`

- [ ] **Step 1: 写入失败测试。**

  覆盖：AkShare 与 baostock 的同一 `symbol + ex_date` 合并为一条；来源写入 `verified_by`；仅一个可用源时仍为 `FRESH`；两个适用源都失败时产生 `UNAVAILABLE` 同步结果而非伪造行动事件；baostock 对场内基金返回 `UNSUPPORTED` 不视作失败；冲突类型或关键日期时标记 `CONFLICT`。

- [ ] **Step 2: 运行测试，确认失败。**

  Run: `pytest tests/unit/test_corporate_actions_integration.py -q`

- [ ] **Step 3: 实现领域模型和协调器。**

  定义不可变的 `CorporateAction`、`ProviderResult`、`SyncResult`。实现 AkShare A 股抓取器与 baostock 抓取器；将数据归一化到 `CASH_DIVIDEND`、`EX_RIGHTS`、`RIGHTS_ISSUE`、`OTHER`。以 `symbol + ex_date` 合并，生成稳定的 `auto-corporate-actions:<market>:<symbol>:<ex-date>` 事件键，并在异常中保存来源可用性状态。

- [ ] **Step 4: 运行测试。**

  Run: `pytest tests/unit/test_corporate_actions_integration.py -q`

  Expected: 全部通过；网络调用全部由 mock 替代。

- [ ] **Step 5: 提交协调器。**

  ```powershell
  git add backend/integrations/corporate_actions.py tests/unit/test_corporate_actions_integration.py
  git commit -m "feat：实现公司行动双源抓取与质量合并"
  ```

## Task 4：添加全局同步任务与受控写入

**Files:**

- Create: `backend/jobs/corporate_actions_sync.py`
- Create: `tests/unit/test_corporate_actions_sync_job.py`

- [ ] **Step 1: 写入失败测试。**

  断言任务仅收集未关闭持仓中 A 股与场内基金的去重标的；场外基金被跳过；每个归一化事件调用受控 SQL 函数一次；两个源都不可用时仅记录来源快照和日志、不创建行动行；任意一个标的失败时其余标的继续处理。

- [ ] **Step 2: 运行测试，确认失败。**

  Run: `pytest tests/unit/test_corporate_actions_sync_job.py -q`

- [ ] **Step 3: 实现任务。**

  提供 `sync_corporate_actions(pool, now)`。查询 `portfolio.positions` 和 `reference.instruments`，过滤 `STOCK`、`ETF` 等首期支持类型；调用 Task 3 协调器；使用 Task 1 的 `market.upsert_corporate_action_sync`；写出带扫描数、成功数、不可用源数和异常数的结构化日志。

- [ ] **Step 4: 运行测试。**

  Run: `pytest tests/unit/test_corporate_actions_sync_job.py -q`

  Expected: 全部通过。

- [ ] **Step 5: 提交同步任务。**

  ```powershell
  git add backend/jobs/corporate_actions_sync.py tests/unit/test_corporate_actions_sync_job.py
  git commit -m "feat：新增公司行动全局同步任务"
  ```

## Task 5：注册交易日 06:00 Worker 调度

**Files:**

- Modify: `backend/jobs/scheduler.py`
- Modify: `backend/worker.py`
- Create: `tests/unit/test_corporate_actions_schedule.py`

- [ ] **Step 1: 写入失败测试。**

  断言 `CORPORATE_ACTIONS_REFRESH` 的唯一计划为 Asia/Shanghai 06:00；交易日 06:00 正好触发；同日第二次不重复；周末及交易日历中的休市日不触发；Worker 收到此会话时执行全局同步并跳过租户报告运行器。

- [ ] **Step 2: 运行测试，确认失败。**

  Run: `pytest tests/unit/test_corporate_actions_schedule.py -q`

- [ ] **Step 3: 扩展计划和 Worker 分支。**

  在 `SCHEDULES` 增加 `SessionSchedule("CORPORATE_ACTIONS_REFRESH", time(6, 0), "macro")`。复用现有交易日历守卫；在 `Worker._execute_session` 为该会话调用 `sync_corporate_actions` 后返回，确保不创建或触碰 `workflow.jobs`。

- [ ] **Step 4: 运行测试。**

  Run: `pytest tests/unit/test_corporate_actions_schedule.py -q`

  Expected: 全部通过。

- [ ] **Step 5: 提交调度集成。**

  ```powershell
  git add backend/jobs/scheduler.py backend/worker.py tests/unit/test_corporate_actions_schedule.py
  git commit -m "feat：在交易日六点调度公司行动同步"
  ```

## Task 6：将质量和来源纳入盘前简报

**Files:**

- Modify: `backend/reports/repository.py`
- Modify: `backend/reports/pre_market.py`
- Modify: `tests/unit/test_pre_market_report.py`
- Modify: `tests/unit/test_reports_repository.py`

- [ ] **Step 1: 写入失败测试。**

  验证仓储返回 `quality_status`、`verified_by` 与日期范围内的行动；盘前正文按当天与未来三天展示最多三条，其余写“等 N 条”；`UNAVAILABLE` 和 `CONFLICT` 不伪装为已确认公司行动；没有可展示记录时明确显示“暂无已录入公司行动”。

- [ ] **Step 2: 运行测试，确认失败。**

  Run: `pytest tests/unit/test_pre_market_report.py tests/unit/test_reports_repository.py -q`

- [ ] **Step 3: 实现读取和格式化。**

  仅在 `FRESH` 或可审计的 `STALE` 记录中展示事件，正文附上日期、类型、标的和来源；将 `STALE` 标记为“数据可能过期”。保持既有持仓影响、企业微信摘要和审计页字段不变。

- [ ] **Step 4: 运行测试。**

  Run: `pytest tests/unit/test_pre_market_report.py tests/unit/test_reports_repository.py -q`

  Expected: 全部通过。

- [ ] **Step 5: 提交报告展示。**

  ```powershell
  git add backend/reports/repository.py backend/reports/pre_market.py tests/unit/test_pre_market_report.py tests/unit/test_reports_repository.py
  git commit -m "feat：在盘前简报展示多源公司行动"
  ```

## Task 7：补充部署手册并完成端到端验证

**Files:**

- Modify: `docs/11-运行维护手册.md`（若实际文件名不同，以仓库现有 11 文档为准）
- Modify: `README.md` 或对应运维文档索引
- Modify: `scripts/verify.ps1`（仅在缺少必要测试入口时）

- [ ] **Step 1: 记录运维验证步骤。**

  写明 058 的 dry-run、执行迁移、确认 Worker 权限、手动运行一次同步、查询质量状态、检查次日 06:00 日志，以及人工 CSV 仍用于场外基金的操作示例。不得将数据库凭据写入文档。

- [ ] **Step 2: 执行局部与全量验证。**

  Run: `pytest tests/unit/test_baostock_adapter.py tests/unit/test_corporate_actions_integration.py tests/unit/test_corporate_actions_sync_job.py tests/unit/test_corporate_actions_schedule.py tests/unit/test_corporate_actions_migration_contract.py tests/unit/test_pre_market_report.py tests/unit/test_reports_repository.py -q`

  Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`

  Expected: 局部测试与既有后端、前端验证均通过。

- [ ] **Step 3: 检查迁移计划。**

  在已配置生产迁移连接的部署环境运行：

  `python scripts/migrate.py --environment production --confirm-target <已审计数据库主机:端口/数据库> --dry-run`

  Expected: 仅计划 058，且读回校验描述公司行动多源契约。

- [ ] **Step 4: 提交文档和验证入口。**

  ```powershell
  git add docs README.md scripts/verify.ps1
  git commit -m "docs：补充公司行动自动同步运维说明"
  ```

## 最终自检

- [ ] `git diff --check` 无输出。
- [ ] 所有新增 Python 文件通过项目格式化与静态检查入口。
- [ ] 单元测试不访问真实 AkShare 或 baostock 网络。
- [ ] 058 迁移在空库与已有 057 数据的库上均可安全执行。
- [ ] Worker 直接写 `market.corporate_actions` 的权限仍为拒绝。
- [ ] 周末与交易日历休市日不会调度同步任务。
- [ ] 自动抓取不支持的场外基金不会误报抓取成功。
- [ ] 每个任务完成后均以中文提交信息提交；最终只在用户要求时推送远端。
