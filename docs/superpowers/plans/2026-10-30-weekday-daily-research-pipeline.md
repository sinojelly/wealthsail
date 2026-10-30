# 工作日每日 AI 投研流水线 Implementation Plan

历史实施记录：本计划当时按周一至周五07:00完成。2026-10-31用户最新需求与Review 61修订见[后续实施计划](2026-10-30-daily-research-review61.md)，当前默认已改为每天07:00，以下原始验证记录保留。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用工作日07:00的一条串行流水线生成可信AI研究，默认关闭旧简报和晨间消息，清晰展示状态与触发时间。

**Architecture:** 沿用现有Worker调度和公共采集实现，以持久流水线阶段与指定批次输入连接采集和模型调用。保留每日模型唯一槽、已有研究历史和租户权限，通过独立兼容开关隔离旧报告/消息。

**Tech Stack:** Python asyncio、PostgreSQL前向迁移、pytest、React/TypeScript、Vitest、Playwright。

**Spec:** [需求与方案](../../34.工作日每日AI投研流水线与旧简报停用方案.md)。用户要求TDD并直接实现，本计划在当前任务内执行。

## Global Constraints

- Asia/Shanghai周一至周五07:00；默认允许启动窗口120分钟，窗口不强制终止已开始的模型。
- 一个`DAILY_RESEARCH_ENABLED`控制完整研究依赖链；旧简报与晨间推送默认关闭。
- 不改已应用迁移、不删当日执行记录、不重试已消费模型槽、不发真实消息或模型调用。
- 每项先运行失败回归，再实现最小改动；验证后按依赖关系本地提交，保留用户未跟踪文件。

## Review Focus

- 采集仍CLAIMED超过10分钟、失败或无可靠输入时必须阻断模型。
- 并发/取消/重启以及预检失败不能绕过唯一槽或污染指定采集批次。
- 周末、07:00前、09:00后和周五跨周末的下一计划时间准确。
- 旧Outbox内容缺少session或存在MACRO/PERSONALIZED变体时仍不能漏发。
- 前端UTC转北京时间、实际/计划时间以及历史报告和今日失败的组合不能误导。

### Task 1: 旧简报与消息隔离

**Files:** `backend/config.py`、`backend/jobs/session_switches.py`、`backend/jobs/alpha_opportunities.py`、`backend/jobs/outbox.py`、公开env模板、初始化配置脚本及对应tests。

**Interfaces:** 提供`pre_market_reports_enabled=False`、`morning_opportunity_push_enabled=False`、`daily_research_start_window_minutes=120`；既有14:30、盘中和盘后开关保持原职责。

- [x] 测试默认关闭、旧PRE两阶段及恢复关闭、已排队消息不发送、晨间关闭不影响14:30。
- [x] 运行测试确认旧配置/发送路径不满足断言。
- [x] 修改调度及发送边界、模板和初始化默认值，不删除历史消息。
- [x] 运行定向回归、格式和差异检查；统一集成后本地提交。

### Task 2: 串行流水线与持久阶段

**Files:** `backend/jobs/daily_research.py`、`alpha_runtime.py`、`research_inputs.py`、`backend/macro/daily_repository.py`、`alpha_repository.py`、新增pipeline repository、`migrations/095_*`及对应tests。

**Interfaces:** `dailyResearch.pipeline={id,state,stage,startedAt,updatedAt,finishedAt,sourceBatchId,researchRunId,errorClass,quality}`；保留已有研究字段，增加`NON_WORKDAY`/`MISSED`调度含义；按`source_batch_id`读取冻结输入。

- [x] 测试工作日07:00、窗口边界/周末nextRun、主开关替代独立采集循环、指定批次读取。
- [x] 测试采集长等待/失败/PARTIAL质量/未来或错误批次均不会误调用模型。
- [x] 运行失败测试，实施新前向迁移、每日租约、阶段记录和一次模型成本屏障。
- [x] 测试预检不消费槽、并发只一次模型、失败/取消保留阶段及旧报告。
- [x] 运行后端定向与全量、隔离PostgreSQL校验，统一集成后本地提交。

### Task 3: 可读状态与触发时间

**Files:** `frontend/src/api/investment.ts`、`DailyResearchReport.tsx`、`MacroContextStrip.tsx`、`ValueDirections.tsx`、相应CSS和tests。

**Interfaces:** 使用Task 2的pipeline及原时间字段；实际触发优先pipeline.startedAt，否则使用研究startedAt；仅计划字段存在时明确“计划触发”。

- [x] 测试运行各阶段、成功/失败/异常、非工作日/错过时段、UTC北京时间与无时间旧数据。
- [x] 确认失败后实现统一状态组件，去除派发术语与旧08:30描述。
- [x] 测试历史报告仍可读、当前失败可见、完整/紧凑入口一致。
- [x] 运行Vitest、类型检查、lint、build及桌面/手机替身验证，再本地提交。

### Task 4: 文档与集成验收

**Files:** 本方案、需求文档、验收规范、NAS部署手册、Codex操作指南及追溯矩阵。

- [x] 将旧总开关、自然日08:30约定改为本方案，并保留旧功能作为显式启用能力的说明。
- [x] 检查迁移权限、API/Worker版本与配置一致性、异常路径和消息禁止发送路径。
- [x] 合并定向结果执行全量验证，审查差异与密钥，按功能本地提交；报告真实NAS尚待验证项。

## 实施与验证记录（2026-10-30）

各项先补充失败回归，再实现并通过定向验证。最终后端全量为3903通过、154跳过；前端63个文件共478通过，包括21个新增状态用例。Ruff、TypeScript类型检查、ESLint、生产构建与差异检查通过。既有依赖弃用、React act及Recharts测试告警不影响结果。

在独立临时PostgreSQL中实际应用全部迁移至095，9项流水线、研究、公共来源与DEBUG数据库测试通过；另以真实Worker角色验证旧PRE队列隔离及14:30等其他会话仍可领取，事务回滚后未留业务数据。直接调用研究执行函数的两项事务补验通过：报告INSERT后注入异常时报告与流水线终态同回滚，正常时两者同提交READY。没有连接真实NAS或调用真实模型、数据源和消息发送。

桌面与手机Playwright替身页面无控制台错误、无横向溢出；覆盖核验中、今日失败与保留历史报告。文档验证器检查75项需求、210项测试定义、217个本地链接，失败数0；文档验证器自身20项测试通过。

本地提交按旧开关隔离、后端串行流水线、前端状态及文档拆分；用户已有未跟踪分析报告与部署记录保留。NAS验收仍为NOT_RUN：需先应用095，再更新API/Worker/前端并重建使用新环境变量的容器，验证首次工作日自动执行；不能通过删除当日记录强制重跑。
