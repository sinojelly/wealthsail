# 独立新闻、主题与执行任务契约实施计划

> For agentic workers: use test-first implementation for each independent contract, then verify the integrated pipeline before local commits.

**Goal:** 允许零正式方向保留有来源依据的新闻与动态主题，以逐动作证明执行审核任务，不改写历史。

**Architecture:** 新闻事实、动态主题、基金投资计划使用独立身份。新增版本化关联解析与逐动作执行证明，沿用严格价格、行业风险及正式发布保护。现有不可变归档与历史衍生报告承载离线结果，不新增正式终态。

**Tech Stack:** Python/Pydantic、PostgreSQL JSONB/RLS、React/TypeScript、pytest/Vitest。

**Spec:** 本计划中的数据契约与本次用户 ef47f9fd 失败案例要求。

## 全局约束

- 仅本地实现、测试、提交；禁止NAS、生产数据库、真实AI、推送和部署。
- 原FAILED/INITIAL/冻结材料/Token/调度终态不变。离线新结果具有新规则指纹与出处。
- 保持每批64个发现、8个正式方向、10范围/50计划查询、24来源、一次模型调用的资源上限；上限不是输出目标。
- 来源通过结构验收不等于引文已核实；搜索证明不等于风险不存在。

## 数据契约与兼容

1. `relatedTopicKeys` 引用实际合法注册主题；`relatedFormalDirectionKeys` 引用严格方向；`relatedMacroConcepts` 与 `candidateConceptLabels` 为只读概念。每项有界，无基金行业授权。
2. 服务器 `referenceResolution` 带 `research-event-links-v1` 版本、逐标识解析方式/未解析原因。仅精确或ASCII大小写、下划线/连字符规范化后的唯一主题可关联；碰撞不猜测。原始标识仍保留。
3. V2旧 `affectedDirectionIds` 的未注册概念进入未解析观察，不使独立合法新闻失效。真实正式方向依赖仍用于隔离与风险保护。V1旧严格接口保持兼容。
4. 新闻来源、日期、安全叙述独立验收；主题、事件和日历的证据与风险提示保留。零正式方向不制造计划、不进入跟踪/推送。
5. `research-execution-v2` 保存有界逐动作proof；可信完成search精确匹配原任务。不同ID未知动作只隔离自身；同ID冲突撤销该ID证明；无法归属的截断/坏事件触发全局proof barrier。仍绑定run、manifest、输入与原校验时钟。
6. 旧v1 COMPLETE可按旧规则读取；v1 PARTIAL不能凭残留哈希升级。只有可追溯原始JSONL可产生新的离线观察，不覆盖旧回执。
7. JSONB投影仅扩展安全字段，不改变099–102权限/不可变规则，不新增数据库终态。真实DB回归发现102条目白名单需要103扩展四个有界洞察元数据字段，权限与冻结约束保持，事务由迁移runner持有。

## 任务

### 1. 独立事件合同

Files: `research_event_links.py`、`daily_research.py`、`research_acceptance.py`、`research_acceptance_repository.py` 与专用pytest。

- [x] RED：零方向+动态主题/宏观/无关联新闻；未知16概念不授权投资；规范化唯一/碰撞；来源与日历错误仍拒绝。
- [x] GREEN：schema、Prompt、严格与收集式验收、只读证据投影。

### 2. 逐动作证明

Files: `integrations/research_execution.py`、Runner/客户端兼容及专用pytest。

- [x] RED：50/50任务+52回执+1未知动作的PARTIAL流；同ID冲突、未知ID、截断、旧v1 PARTIAL不能冒充证明。
- [x] GREEN：逐动作proof、诊断、传输摘要、研究身份绑定，预算不变。

### 3. 业务闭环与只读展示

Files: `research_batch.py`、主题安全投影/历史恢复、前端验收/主题组件及测试。

- [x] RED：64主题、0正式方向、6新闻、3日历及16未知引用；真实正式方向缺审查不能绕过；系统负面与历史风险不因关联转换消失。
- [x] GREEN：保留独立主题上下文、完整依赖诊断和覆盖计数；来源逐条核验状态、未解析关联与零方向说明，不添加投资动作。

### 4. 冻结回归与交付

- [x] 在本地冻结材料上先原规则dry-run；输入哈希/时钟核对；新结果单独保存，记录真实NAS协议验证是否有样本支持。
- [x] 运行相关RED/GREEN、后端/前端全量、Ruff、TypeScript、ESLint、构建及独立loopback PostgreSQL事务/RLS/不可变回归。
- [x] 比较Prompt/Schema/输出UTF-8字节开销；确认一次模型调用、不新增叙事AI、查询预算不扩大。
- [x] 检查差异与敏感数据，按功能依赖本地提交；原文和真实个人部署参数不进Git。

## 重点风险

- 新命名空间关联不能授予行业审核或基金资格。
- 解析失败不能删去负面事实或令HIGH风险失踪；不安全发布继续阻断。
- 旧PARTIAL材料缺少逐动作协议证据时必须说明无法确认，而非补造完成状态。
- 未收到本次冻结文件或真实CLI样本时，合成测试不得声称真实案例或NAS兼容验证通过。
- 合法只读新闻结果与旧READY报告必须隔离，历史恢复不触发交易或自动通知。
