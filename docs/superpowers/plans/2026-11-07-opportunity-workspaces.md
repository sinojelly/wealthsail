# 投资机会四入口与经济事件阅读 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按参考文章 79 的信息架构与 78 的事件阅读设计，完成今日重点、经济事件、我的基金、市场证据四个工作区，保留既有投资与复盘契约。

**Architecture:** 主页面负责 URL、公共报告加载与跨页导航；事件使用既有只读归档投影；私人基金使用独立的本人 study-tracks 查询。展示层按职责拆分，复用净值、宏观曲线与完整研究/复盘组件，不改变算法或模型输出。

**Tech Stack:** React 19 / TypeScript / React Router / TanStack Query / Recharts / Vitest / Testing Library / Playwright。

**Spec:** [25.投资机会UI设计.md](../../25.投资机会UI设计.md)，需求追溯见 [01](../../01.WealthSail投研平台需求文档.md)、[08](../../08.WealthSail需求设计测试追溯矩阵.md)。来源：[79](../../../ai-review/79.投资机会子页面增加经济事件和我的基金.md)、[78](../../../ai-review/78.支持经济事件故事叙事.md)。79 中引用 76 为旧编号，以本次实际 78 文件为准。

## Global Constraints

- 仅本地实现、验证、按功能提交；不推送、不部署、不运行真实 Codex 研究。
- 不新增模型调用，不改 Prompt、输出 Schema、投资评分、来源校验、基金价格算法或数据库。
- `researchTab=events` 仍是今日重点，`stories` 是经济事件，`tracking` 改为我的基金，`macro` 仍是市场证据；`quality` / `researchData=1` 兼容旧证据入口。
- `studyTrack`、明确标的与 `researchOpen` 深链保留；明确研究记录进入我的基金的研究详情，不清掉原选择。
- 展示报告 ID 与本次研究 ID 分离；历史、当前安全验收成果、独立官方事实不得互相冒充。
- 事件发生时间未知时写未提供；来源发布时间、研究日期、净值确认日、估值时刻、图末点日分别标注。
- 不同研究的事件保持独立，即使事件 ID 相同也不合并为跨日故事；仅在同份研究内根据明确事件 ID、来源和证据关系去重。
- 原文引用核对不等于事实或因果认证；无价格证据不写市场已涨跌，不把观察基金变成可执行投资建议。
- 私人记录不进公共研究；同基金可合并展示，原冻结研究计划、版本、权限与真实成交记录不合并或覆盖。
- 今天只显示简短判断、有效个人提醒、最多 3 个精选机会摘要、最多 3 条事件简讯；全量基金和详细图表在所属工作区按需阅读。
- 我的基金不依赖报告成功；不把取消跟踪当卖出，不补造成本、份额、仓位或个人盈亏。
- 各任务先补可观察行为的失败测试，确认 RED 后实现 GREEN，再验证、审查、提交；不得一边运行全量测试一边改源文件。

## Review Focus

1. 旧 tracking/studyTrack URL 与键盘、前进后退仍定位本人原研究，不误跳到公共机会。
2. 本次失败/运行中但历史有效报告存在时，事件与基金匹配各自引用正确研究，私人基金照常加载。
3. 多冻结研究属于同一基金时，一张汇总卡保留全部原计划；切换图表叠加不更改其他计划。
4. 同标题、同聚合 URL 的不同事实不能合并；缺发生时间和市场反应时不能借用别的时间或涨跌数字补齐。
5. 股票/汇率等数据缺口、多基金搜索、无代码基金、不同市场同码、手机和异步刷新均有明确稳定状态。

## 实施顺序与边界

导航/首页集成由主代理维护；事件与私人基金在明确接口下可分别完成各自 TDD，不并发编辑同一文件。每个任务完成独立验证后提交，不把尚未完成的任务混入提交。跨任务集成之后运行全量前端和浏览器验收。

跨日故事关联：用户明确选择方案 1。本轮只复用已有事件、来源与当前及保留历史研究；不同研究中的事件分别归档阅读，不按 ID 相同、标题、关键词、URL 或语义推断同一故事。日期浏览仅展示已加载的可用研究日期，明确范围；历史报告链接只表示报告出处，不宣称已核实的事件进展。事件发生日期未提供时保持缺失。后续独立阶段建立可信稳定故事 ID 与可审计、可撤销的关联；本轮不新增数据库、模型调用或验收规则。

### Task 0: 收口需求设计与任务文档

**Files:** 修改 docs/01、04、08、25、28、29、news-topic-fund-recommendations.md；新增本计划。

**Interfaces:** 产出统一四入口、页面层级、字段与日期语义、兼容和验收要求；主 UI 契约以 25 为准，旧展示章节由本次更新覆盖，算法与权限条款继续有效。

- [x] 阅读两篇参考、现有接口/组件/设计，记录可复用与真实数据缺口。
- [x] 更新已有主文档、分配需求 ID 和用例，追溯先标 NOT_RUN。
- [x] 独立 builder 检查设计和计划；已知答案写回，必要的用户选择单独询问。
- [x] 检查文档链接与差异，只提交本任务文档。

### Task 1: 四入口导航与页面职责

**Files:** 新建 `frontend/src/features/fund-screener/workspace-navigation.ts` 及测试；修改 `components/MacroContextStrip.tsx`、`FundScreenerPage.tsx` 及相关导航/深链测试；新增工作区样式。

**Interfaces:** `ResearchWorkspace = "events" | "stories" | "tracking" | "macro"`；`resolveResearchWorkspace(params: URLSearchParams): ResearchWorkspace`、`workspaceParams(params, workspace): URLSearchParams` 保留无关 URL 参数。`MacroContextStrip` 继续接收既有报告加载状态和两个私人组件 render 回调。

- [x] RED：默认/旧 events→今日重点、stories→经济事件、tracking→我的基金、quality/researchData→证据；显式 studyTrack 可读；四 tab 左右/Home/End 和后退。
- [x] GREEN：增加第四入口，独立当前路由状态；我的基金显示私人内容与二级复盘，报告等待/失败不阻断私人查询。
- [x] 将完整专业宏观/报告/目录/代表池归市场证据次级折叠；保留现有目录/买卖计划直达入口，修正跳转目标。
- [x] 跑导航、报告加载、深链相关测试；审查并提交。

### Task 2: 经济事件阅读与严格来源投影

**Files:** 新建 `economic-story-model.ts`、`components/EconomicEvents.tsx`、`economic-events.css` 及模型/组件测试；主页面仅消费它们的导出接口。

**Interfaces:** `buildEconomicStories(data: OpportunityContext, today?: string): EconomicStory[]`；`EconomicEvents({data, selectedEventId?, onSelectEvent?})`；`EconomicEventTeasers({data, onOpenEvent})`（最多 3 条，使用同一故事身份）。故事持有事实摘要、阶段、原文核对状态、研究来源/日期、来源发布时间、条件传导/情景/观察/未知、证据来源和可精确关联的记录，不生成原文未提供的事实。

- [x] RED：当前安全成果＋历史 READY 分开；今日失败仍读独立官方事实；RUNNING/空归档显示实际状态；未知发生时间与未核验市场反应不被补造。
- [x] RED：同研究内明确 ID 与证据关系去重、来源保留；跨日期/跨研究即使同 ID 仍独立；同标题/同 URL 不同摘要/阶段不吞事件；部分来源、无来源、危险链接、分类和展开详情。
- [x] GREEN：按发生什么→为什么关注→条件传导→观察什么组织；默认至多 8 张，少于目标不凑数；提供阅读分类和精确来源链接。
- [x] 独立显示有程序数据的能源观察及日期；其他主要资产无证据明确缺口，禁止归因。
- [x] 小课堂只关联实际渠道与已有通用机制字典，标“通用机制”，缺关联不生成知识；同次研究之外不发模型请求。
- [x] 跑事件模型/组件、既有 economic delivery/归档相关前端测试；记录 Prompt/Schema/模型输出增量为 0 的依据；审查并提交。

### Task 3: 我的基金与私人行动提醒

**Files:** 新建 `personal-fund-model.ts`、`use-personal-funds.ts`、`components/MyFundsWorkspace.tsx`、`components/PersonalActionReminders.tsx`、`my-funds.css` 及对应测试。复用 `StudyTracking`、`FundNavTrendChart` 与 quote resource；不改 API。

**Interfaces:** `groupPersonalFunds(list: StudyTrackList, today: string): PersonalFundGroup[]`；`PersonalFundGroup` 含 key/market/code?/label/holding?/tracks[]/trackingSuppressed；`MyFundsWorkspace({actor, renderStudyTracking?})`；`PersonalActionReminders({actor,onViewFunds,excludedSellFundCodes?})`。两者共享 actor 隔离的 `['study', actor, 'list']` 查询，提醒不展示完整基金图表。

- [x] RED：持仓＋两个研究只一基金、原计划保持两份；同码跨市场不合并；无代码持仓不误用境内图；取消跟踪不隐藏持仓。
- [x] RED：disabled/ENDED/上海过期跟踪不进活动提醒；仅跟踪不算持仓；数据不足不能说没有新触发；报告 pending/error 仍读取私人记录。
- [x] GREEN：全部/已持仓/仅跟踪/历史复盘；搜索；<=4 只宽卡信息左图右，>4 只紧凑列表＋选中详情。
- [x] 多冻结计划选择独立；图仅叠所选原轨道；准确区分建立时间、计划行情基准、当前判断依据与图末点时间；不得造持仓收益。
- [x] 保留刷新、取消跟踪、研究详情及旧 studyTrack 入口；复盘继续原版本、capabilities、expectedGeneration 和成交守卫。
- [x] 今日提醒只列有效价格/风险命中与明确待核实状态；已在顶部卖出提醒出现的基金不重复堆同类提醒。
- [x] 跑新模型/组件、原 study/current-estimate/报价隔离测试；审查并提交。

### Task 4: 今日重点精选与市场证据语义

**Files:** 新建 `components/TodayFocus.tsx` 或等价摘要组件；修改 `TopicFundRecommendations.tsx`（可选 summary 展示模式）、`MacroContextStrip.tsx`、`FundScreenerPage.tsx`、`MacroEvidencePanel.tsx` 及相关测试/样式。

**Interfaces:** `TopicFundRecommendations` 新增可选 `presentation: "FULL" | "SUMMARY"`，默认 FULL 维持单独消费者；SUMMARY 初始最多 3 个主题摘要，明确来源、主要基金/风险/日期，可展开该主题原有信息与右图。只有当前研究且无有效报告时，当前观察作为唯一线索可见，不静默消失。

- [x] RED：今日市场判断最多三句，完整报告与指标移到证据；不再重复私人全量表和所有图；提醒、精选机会、3条事件简讯及跳转可达。
- [x] RED：有效报告与本次请求 RUNNING/FAILED/UNCERTAIN/STALE 各自独立；404/零匹配/资料不足原有状态保持；异步刷新不让摘要无提示替换。
- [x] GREEN：集成私人提醒、事件简讯与精选机会；完整论据/图仍在对应详情，主要风险始终可见；诊断和技术 ID 默认收起，保留显式手动研究控件。
- [x] 证据卡的 PMI/通胀/利率等补具体含义，不能从单指标推出投资资格；保留原分类、独立展开、加载/缺失/重试与36月窗口。
- [x] 跑主要页面/状态/匹配/证据回归，按真实职责更新旧三 tab/首页全表的已退休断言，保留金融与权限断言；审查并提交。

### Task 5: 全量验收、追溯与成本说明

**Files:** 更新任务状态及 docs/04、08、25 的实施/限制；浏览器脚本与截图放工作区外允许 artifacts 目录，不入 Git。

- [x] 前端 `npm test -- --maxWorkers=2` 全绿；`npm run build`、`npm run lint`、改动文件 Prettier 与 `git diff --check` 通过。
- [x] 桌面 1440/1665、手机390：四入口、事件阅读、个人筛选/多计划/复盘、证据曲线、旧深链、报告失败/刷新、无数据、来源跳转、无横向溢出及运行异常。
- [x] 检查所有 GET/切页/图表不触发模型，记录未改 Prompt/Schema/backend 的字节差为0；不以模拟样本声称真实新闻覆盖、投资资格或 NAS 已验证。
- [x] 全分支独立审查并修复真实问题；更新需求追溯为 LOCAL_PASS（NAS 保持 NOT_RUN）。
- [x] 提交最终文档/相关收尾改动，确认任务相关工作树无遗留，保留用户无关文件。

## 执行记录

- 2026-11-07：已完成源码/两篇文章/现有文档的独立审计；唯一实质外部选择是完整跨日故事关联。本文采用用户已指定的参考设计和逐任务 TDD 授权执行，常规实现选择在文档中显式记录，不另建平行产品方向。
- Ruling：明确有效的 `researchTab` 优先；只有 `studyTrack` 而没有有效 tab 时默认我的基金。用户带着保留的研究 ID 切到其他页时，不强制跳回复盘；旧显式研究入口仍可读。
- Ruling：Task 1 先提交可独立测试的 URL 导航契约；四入口页面集成随 Task 4 一次接入已经完成的事件/私人组件，避免临时空入口及重复私人展示。接口和兼容用例不变。
- Ruling：私人“下一观察条件”只在用户选择计划或进入复盘时读取 `getStudyDetail`，不批量加载全部研究详情；列表接口没有成本/份额/收益，不展示虚构值。
- 用户决定：本轮采用可靠归档阅读，不建设跨日故事链；跨研究事件不合并，缺日期不补造，可用日期浏览诚实标注仅限已加载归档，后续阶段再做可审计的可信关联。
- Task 0：独立设计检查通过；7 份主文档与本计划统一，新增 22 处本地链接目标存在，`git diff --check` 通过；NAS 未验证。
- Task 0：已提交 `ea304d7`。
- Task 1 导航契约：RED 占位实现 18 失败/5 通过；GREEN 23/23，相邻纯函数 30/30；TypeScript、文件 ESLint/Prettier 与差异检查通过。独立审查 spec/quality 均 PASS；四入口实际 UI 与键盘/历史验证随 Task 4。
- RED/GREEN、验证命令、提交与真实限制将在每个任务完成后写入这里；禁止提前标记完成。

- Task 2：模型/组件先 RED 后实现；稳定身份与关联/日期补充 RED。独立审查两项 P2（日程发生状态、事实核验标记）均经失败测试修复并复核 PASS。最终 4 文件 46/46；TypeScript、文件 ESLint/Prettier 与差异检查通过；不改后端/Prompt/Schema，真实模型输出增量 0；浏览器与全量集成见 Task 5。

- Task 2：提交 `6c94ace`。
- Task 3：模型 7、组件 10、净值日期/来源用例先 RED 后 GREEN；专项及原研究/行情回归 61/61。独立审查发现真实旧 NAV 单位与未知市场取消两项 P2，新增 11 例 RED 后修复，覆盖 4 文件 45/45；六种服务器认可单位保留原冻结线，未知身份逐研究停止或准确报错。复核 spec/quality PASS，类型/文件 lint/格式检查通过；桌面右图及手机纵排已浏览器验证，未验证真实 NAS。

- Task 3：提交 `a4ff759`。
- Task 1/4 集成：三句判断 RED 4、四入口/诊断 RED 4、摘要限量 RED 1、指标含义 RED 2、官方事实同 ID 不隐藏 RED 1；GREEN 后迁移七档旧入口测试 93/93，保留金融/权限/来源与竞态断言。首次全量 925 中 924 通过，唯一旧 review44 扫描入口已迁移并通过。摘要分类/当前价格与申购限制经两轮独立审查发现并先 RED 修复，最终四档 77/77；spec/quality 复核 PASS。
- Task 4 浏览器：1665/1440/390 四入口、右图/手机纵排、同基金两计划、五只搜索、历史日期/同 ID 不合并、报告失败私人可读、正式报告直达、无横溢/运行异常通过；仅合成替身 GET，预期 404/504 验证缺档/超时，不代表 NAS 真数据覆盖。市场判断阅读内边距先浏览器 RED 后修正。

- Task 5 最终修复批次：全分支静态审查两项 P2（完整句限定、部分停止重试）和浏览器出处深链实测问题，分别先 RED 后 GREEN。报告出处增加原报告 ID，缺档不冒充；异步挂载只揭示一次，不因同源 refetch 重开用户已收起内容。9 档 147/147、类型/文件 lint/格式/差异检查通过；三项独立复核 ADDRESSED，spec/quality PASS；最终全量验证正在冻结源码运行。

- Task 4：提交 `a9e665a`。
- Task 5：全分支审查两项 P2（完整句限定、部分取消重试）及浏览器实测出处 hash 深链问题，经同一修复批次先 RED 后 GREEN并复核通过；修复提交 `962617b`。
- Task 5：本地运行 `LOCAL-OPUI-20261107-final`（实现提交 `962617b`）：Windows / Node 24，本轮前端全量 90 文件 **951/951** 通过；`npm run build`、`npm run lint`、改动文件 Prettier 与 `git diff --check` 通过。桌面1665/1440、手机390的四入口、右侧净值图/窄屏纵排、五只搜索、多冻结计划、原报告出处链接、打印收展和报告失败仍读私人数据通过。浏览器使用本地接口替身，仅GET及模拟认证，无真实新闻/净值或账户写入；预期404/504用于缺档和超时。Browser插件不可用，使用项目已有Playwright；截图/脚本/JSON保留本地运行附件，不入Git。

本地PASS只证明前端数据投影与交互，不证明真实新闻覆盖、投资资格或NAS部署。`migration=N/A`；fixture为现有API类型的合成边界数据；model/provider=N/A，本轮未调用模型；后端、Prompt、Schema与研究验收核心差异为0。NAS端到端继续 `NOT_RUN`。
- Task 5：需求 FR-OPUI-001～010 本地追溯 LOCAL_PASS，真实 NAS 保留 NOT_RUN；用户原有 external-access 两份未跟踪文档保持原样，不推送不部署。
