# 每日研究冻结时限与异常收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结每条流水线的启动/恢复及运行截止时间，收敛过期模型前阶段，明确等待原采集任务的限制。

**Architecture:** 尚未部署NAS的095增加两个NOT NULL绝对截止时间，首次领取时按配置保存，重领只使用原值。Worker与只读API统一依据持久值；归档以过期租约和独立模型槽不存在为前提。

**Tech Stack:** PostgreSQL、Python asyncio/pytest、React/TypeScript/Vitest。

**Spec:** 用户对eb0bd8a的最新代码审查及[每日AI流水线方案](../../34.工作日每日AI投研流水线与旧简报停用方案.md)。

## Global Constraints

- 默认DAILY、07:00、Asia/Shanghai保持不变，包含周末，这是已确认的用户需求。
- `start_window_ends_at = scheduled_for + 首次配置分钟`；`runtime_deadline_at = started_at + 首次配置秒`，恢复不得改动。
- 归档覆盖COLLECTING、WAITING_SOURCES、VALIDATING_INPUT，必须窗口已截止、租约已过期、FK为空且独立SCHEDULED模型槽不存在。
- 有效owner可跨启动窗口继续；总运行期限到期不能因新配置延长，模型成本屏障和终态原子性保持。
- 保留CLAIMED原采集者等待、FAILED终止的保守策略；不增加采集接管，不改093/094或生产账本，不执行真实模型/消息。
- 先红测再修复；实际运行可选PostgreSQL测试并记录结果；按功能验证及本地提交，用户未跟踪文档保留。

## Review Focus

- 同日配置窗口从120改60或延长时，API与Worker都不得改变原截止。
- 同日配置总期限缩短或延长时，已启动任务只依据持久deadline，UTC返回值必须正确转换。
- 窗口截止的三类过期前置状态应归档MISSED；活租约及有独立模型槽但FK未同步的任务不得误归档。
- 模型前runtime先于start window到期时可领取后明确FAILED归档，不能一直轮询或调用模型。
- CLAIMED状态主文案与阶段条明确等待原任务；旧API无截止值时不伪造排障时间。

### Task 1: 冻结字段与归档

**Files:** `migrations/095_daily_research_pipeline.sql`、`backend/macro/daily_pipeline_repository.py`、repository单元/真实PG测试。

**Interfaces:** `claim_pipeline(..., schedule_time='07:00', start_window_minutes=120, pipeline_timeout_seconds=7200)`；DTO新增`startWindowEndsAt`、`runtimeDeadlineAt`，read查询独立模型槽。

- [x] 写创建/重领冻结、三阶段归档、活租约/独立模型槽保护的失败回归。
- [x] 新增095字段/约束，冻结首次值，重领守原启动窗口；runtime到期由job领取后终止。
- [x] 只读投影与写归档条件一致，执行静态迁移及全新隔离PostgreSQL验证，再本地提交。

### Task 2: Worker与API统一时限

**Files:** `backend/jobs/daily_research.py`、`backend/macro/daily_repository.py`、调度与job测试。

- [x] 写配置缩短/延长、UTC绝对deadline、runtime到期不调用模型及API只读的失败回归。
- [x] 创建时传配置，恢复/dispatch/asyncio总超时只读冻结值，移除API旧的WAITING/当前配置重算。
- [x] 更新既有fixture为新数据库真实结构，定向和全量回归后本地提交。

### Task 3: 等待文案与文档

**Files:** 前端日报状态/类型/测试；方案、验收及部署文档。

- [x] 测试明确等待原采集任务、截止时间排障与旧响应，再改最小界面。
- [x] 明确接受CLAIMED强制中断可能错过当天研究；同步冻结时限与095部署前提。
- [x] 前端全量/type/lint/build、后端全量、真实PG及文档校验通过，独立审查后本地提交。

## 验证记录（2026-10-31）

- 新增冻结时限的job/API六个回归先全部失败，实现后转绿；UTC绝对运行截止、配置缩短/延长不影响已有任务及新建配置传递均覆盖。
- Repository DTO先5失败、5通过，修复后10通过；实际PostgreSQL流水线23项通过，覆盖新建冻结、重领保留、3阶段归档、活租约和独立模型槽保护、API只读及结果原子性。
- 独立审查发现窗口关闭但旧租约有效时service直接跳到次日。服务级红测复现等待79140秒，修复后每60秒观察，过期归档且不再领取/采集。相关job/API/repository单元130项通过。
- 开启`INVESTMENT_TEST_DATABASE_URL`与`MIGRATION_ATOMICITY_TEST_ADMIN_URL`，在全新loopback临时PostgreSQL集群应用001–095后执行后端全量。首轮4114通过、5失败、31跳过；在从Git导出的eb0bd8a基线实际重跑，5个同名失败全部复现。
- 五个旧失败仅修测试：入队fixture补真实租户成员并断言API角色、07:02改用已有07:00日程预期、价格监控fixture提供550条有效净值及完整日历。无缓存场景在合法冻结后移除精确fixture缓存，原权限/持仓/14:30断言保留；未修改产品权限或校验阈值。
- 最终后端同样开启数据库可选测试执行全量：4119通过、31跳过，6项既有python-jose弃用告警；未宣称其余可选用例已通过。迁移账本故障注入真实执行，DDL与账本同回滚。
- 前端等待文案2个失败与冻结截止1个失败先红后绿，最终63文件486项通过；typecheck、ESLint、Prettier和生产构建通过。Ruff/格式/差异检查通过，文档校验75需求、210测试定义、217本地链接无失败。
- 没有运行真实模型、外部数据源、微信或NAS部署。采用本地分项提交，不推送；用户4个未跟踪文档保留。临时数据库验证后关闭。NAS仍须先应用此次完整095，再更新匹配的API/Worker/前端；如果旧095已实际部署，不能直接修改生产账本绕过校验和。

## 3d818f2审查后的两个P2边界修复（2026-10-31）

**范围：** 关闭补启动时仍观察既有任务；全天候调度跨日重启安全收敛。默认DAILY/07:00不变，不修改095、模型槽登记事务、心跳、报告终态事务，也不引入采集接管。

- [x] 服务级先复现关闭补启动遗漏既有任务、23:59跨日恢复和准备跨日误报；原新增8例中6例失败、2例通过，修复后全部通过。
- [x] 重启后`observe_only`仅观察与归档，不新领/重领或调用模型；没有既有任务等待下次计划。正常准点启动后等待数据仍继续原流程。
- [x] 真实PostgreSQL验证跨日旧任务读取、三个前置阶段过期MISSED、有效租约保护、独立模型槽/FK和模型后阶段UNCERTAIN、今日任务/模型槽优先，以及来源/模型记录和冻结值不变。
- [x] API三个跨日回归先红后绿：旧任务保留实际时间但不覆盖今日计划/下次计划，当日DEBUG结果不被旧任务遮蔽；读取不写数据库。
- [x] 前端改用“本次研究时段已结束”，旧日状态+今日计划回归先红后绿；旧任务不误称“今日错过”。
- [x] 独立只读审查发现新增轮询DB操作在异常处理之外，瞬时故障会终止服务。补两例服务级红测，加入60秒重试并保留原due/观察模式；133项相关回归通过。审查者独立重跑两例通过，最终无剩余可确认问题。

**本轮验证证据：**

- 临时loopback PostgreSQL复用隔离验证集群，迁移脚本核验001–095（095校验和未变）并完成readback。设置`INVESTMENT_TEST_DATABASE_URL`和`MIGRATION_ATOMICITY_TEST_ADMIN_URL`实际开启可选测试，不依赖Mock替代数据库验证；仓储定向60项通过（42真实PG、18 DTO）。
- 首轮后端全量4157通过、31跳过；独立审查修复及新增两例后，再次开启两组数据库测试执行全量，最终4159通过、31跳过、6项既有python-jose弃用告警。未将跳过用例计入通过。
- 前端全量63文件487项通过（`npm test -- --maxWorkers=2`），类型检查、ESLint、Prettier和生产构建通过，smoke配置安全契约6项通过。首次默认并发全量遇到1项既有异步按钮等待超时，单文件26项和低并发全量复核均通过，未改无关用例。
- Ruff全仓检查、变更Python文件格式检查及冲突标记检查通过。默认compileall因两个既有缓存目录无写权限失败，改用`.tmp`下独立`pycache_prefix`编译通过；没有修改目录权限。
- 文档最终校验75需求、210测试定义、217本地链接，0失败；`git diff --check`通过。验证后关闭本任务临时数据库。
- 未运行真实模型、外部采集、微信或NAS部署；用户4个原有未跟踪文档保留，仅做本地提交。
