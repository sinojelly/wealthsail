# 每日AI研究可靠性与可配置调度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复Review 61确认的问题，默认每天北京时间07:00执行，并允许配置每天/周一至周五及HH:MM时间。

**Architecture:** 保留串行采集、指定批次与模型唯一槽；在现有配置和时间函数中统一调度，在模型执行前安全恢复及按令牌续租。启动窗口仅约束新领取/恢复，已启动流水线另受7200秒运行上限、当日和数据质量约束。

**Tech Stack:** Pydantic Settings、Python asyncio、PostgreSQL、pytest、React/TypeScript、Vitest。

**Spec:** 用户最新要求与[流水线方案](../../34.工作日每日AI投研流水线与旧简报停用方案.md)；Review 61作为待验证审查输入。

## Global Constraints

- `DAILY_RESEARCH_SCHEDULE_MODE=DAILY`默认每天；`WEEKDAYS`明确仅周一至周五。
- `DAILY_RESEARCH_SCHEDULE_TIME=07:00`默认；严格24小时HH:MM，统一Asia/Shanghai，API/Worker保持一致。
- 不把大陆行政调休日历、A股交易日历与周一至周五混为一谈；本次不增加未经维护的在线日历依赖。
- `DAILY_RESEARCH_PIPELINE_TIMEOUT_SECONDS=7200`默认，范围600..14400；原模型超时独立保留。
- 只修正尚未生产部署的095；不改093/094或生产迁移账本，不删除每日执行记录，不调用真实模型或消息。
- 先失败回归再实现；按依赖验证和本地提交，不提交用户的分析报告和部署参数。

## Review Focus

- 周末默认每天执行，WEEKDAYS跳到周一；08:15及UTC日期转换必须一致。
- 采集取消后恢复只复用已有批次；FAILED终止、CLAIMED等待，模型槽后绝不自动重调。
- 正确令牌且未过期才能续租；失去所有权停止后续派发，终态不能复活。
- 启动窗口后完成准备仍可派发，但跨日、总运行超时、输入过期必须阻断。
- 095 DDL与账本原子提交；旧已应用迁移事务包装冻结，不能盲目改写历史文件。

### Task 1: 迁移原子边界

**Files:** `migrations/095_daily_research_pipeline.sql`、迁移静态和真实数据库测试。

- [x] 写顶层事务扫描失败回归，区分DO函数体与SQL顶层；094以后禁止事务控制，旧包装仅允许冻结集合。
- [x] 删除095内部BEGIN/COMMIT，保留DDL/RLS/授权，确认失败回归转绿。
- [x] 在全新隔离PostgreSQL应用迁移，注入账本失败验证DDL同回滚，再本地提交。

### Task 2: 时间配置与API

**Files:** `backend/config.py`、`backend/macro/daily_repository.py`、`backend/investment/opportunity_service.py`、env模板、NAS/本地初始化脚本及对应测试。

**Interfaces:** `scheduled_for(now, schedule_time='07:00')`、`next_run(now, schedule_mode='DAILY', schedule_time='07:00')`、`is_scheduled_day(now, schedule_mode='DAILY')`；API输出`schedule={mode,time,timezone,startWindowMinutes}`。

- [x] 写每天周末、WEEKDAYS、08:15、自定义启动窗口、非法时间/模式及环境变量贯通的失败回归。
- [x] 实现统一计算与API元数据；执行记录保留领取时真实计划时间，不因同日改env重写。
- [x] 更新公开模板/初始化默认值，定向测试通过后与流水线调用集成，再本地提交。

### Task 3: 恢复、心跳与运行期限

**Files:** `backend/jobs/daily_research.py`、`backend/macro/daily_pipeline_repository.py`及流水线测试。

**Interfaces:** `claim_pipeline(...,schedule_time=...)`、带旧令牌检查的续租和模型前释放；每日模型槽仍为最终成本屏障。

- [x] 先测采集取消/死亡后恢复、FAILED/CLAIMED批次策略、槽后重启不重复模型。
- [x] 先测长阶段续租、旧令牌/过期令牌拒绝、心跳丢失阻止派发、任务取消清理心跳。
- [x] 先测启动窗口后完成准备允许、跨日/总时限/数据失效阻断，按失败用例实施。
- [x] 完成定向、真实数据库和后端全量回归后本地提交。

### Task 4: 界面、文档与验收

**Files:** 前端API类型和日报/价值方向组件、状态测试、需求/方案/追溯/部署文档。

- [x] 先测每天/WEEKDAYS/08:15及旧响应，前端移除硬编码工作日07:00，保留真实触发时间。
- [x] 同步需求、验收、部署配置与大陆工作日历边界，记录Review 61逐项结论。
- [x] 前端全量、类型/lint/build、后端全量、文档校验与最终独立审查通过后本地提交。

## 实施裁定

- 新用户要求取代上一版周一至周五默认；默认改为每天07:00，WEEKDAYS保留明确的周内模式。
- 大陆调休日历可以依据国务院年度安排维护，但不同于交易日；本次提供明确的两种稳定模式，不把周一至周五伪称为大陆行政工作日。
- Review建议的恢复只限模型槽前；已有FAILED采集批次自动终止，不能为恢复而删除或覆盖批次。

## 实施与验证记录（2026-10-31）

- 迁移静态回归首次精确失败于095的BEGIN/COMMIT；删除后通过。真实PostgreSQL在自建UUID数据库注入账本INSERT失败，验证095 DDL与账本同回滚，再正常迁移。未修改093/094及生产账本。
- 新日程18项初始失败；实现DAILY/WEEKDAYS/HH:MM后转绿，追加API、NAS/本地配置生成贯通及原计划窗口回归。UTC持久计划转回北京时间后再领取模型槽，防止原07:00误登记为23:00。
- 正常取消释放模型前租约，过期COLLECTING/VALIDATING_INPUT可安全重领。新增服务级回归覆盖07:10重启遇旧租约、每60秒等待至07:30后一次接管；终态、已消费槽和窗口截止不再等待。
- 独立审查复现成功COMMIT后与WAITING_SOURCES释放后的心跳竞态、同日改env导致原08:15计划在09:10误判MISSED。分别补红测后修复：释放/终态前停心跳，sync、service及API均优先原计划时间。
- 最终后端全量3957通过、162跳过；6项既有python-jose弃用告警。最终真实PostgreSQL选定17项全通过，含迁移、角色、并发、冻结批次、模型防重及结果事务；Ruff、格式和差异检查通过。
- 前端新增场景先7失败、20通过，修复后定向27通过；最终63文件484项通过，类型检查、ESLint及生产构建通过。首轮一个既有异步加载超时独立通过，限制并发重跑全量通过，未修改超时断言。
- Playwright替身验证每天周六07:00、周一至周五下周一08:15、旧响应中性回退及390px手机详情，控制台错误与横向溢出均为0；本次Vite已关闭。
- Windows PowerShell长路径会拆分Write-Error诊断，导致19项文档行为断言失败；改为完整stderr输出后20项全部通过，校验规则未削弱。文档总检75项需求、210项测试定义、217个本地链接，无失败。
- 分项本地提交，未推送；用户的59/60/61分析报告和个人部署记录保留。临时PostgreSQL验证后关闭，真实NAS、真实模型、数据源及微信端到端未执行，仍须按方案部署验收。
