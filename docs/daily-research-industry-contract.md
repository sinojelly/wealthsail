# 每日研究的基金行业审核契约

## 本次故障与可确认范围

真实失败方向 `industrial-metals-repricing` 关联了 `RARE_EARTH`、`RARE_EARTH_METALS`，但基金 004432 的服务器行业为 `NONFERROUS`。原代码将模型声明与服务器基金行业合并后统一报 `DIRECTION_INDUSTRY_REVIEW_UNKNOWN`，因此无法区分是哪一方缺失。

离线复核确认三个独立缺口：候选不直接读取 `discoveryActionableZones`；综合新闻分类与基金行业使用不同范围的代码；Prompt 按入场保护后的数据排序，验证却从原始数据和完成时间重算候选。前10项剪枝也可能丢掉基金行业。没有原失败的完整输入包，不能断言该次 `NONFERROUS` 一定被截断，也不能仅凭模型标签断言数据库行业配置错误。

## 身份、范围与审核义务

- 服务器基金价格计划的规范行业、候选查询、Prompt 的 `requiredReviewIndustryIds` 与方向校验共用 `industry_contract.py`。本次 Prompt 版本为 `daily-research-industry-plan-contract-v16`。
- 新生成价格计划从经身份核验的基金目录复制行业。历史计划缺少行业时，优先从同一基金的唯一目录行业补齐，再回退同一基金的唯一扫描行业；冲突显式失败，不将多个范围拼成同一个基金行业。
- 身份必须有有效的类型和标识。基金代码兜底要求一致的六位代码及中国基金市场，不允许指数混入、冲突代码/市场、不同基金 UUID 或缺失标识互相匹配。
- `NONFERROUS` 使用有色金属、工业金属、铜铝等中文检索；`RARE_EARTH` 使用稀土、永磁、磁材。`RARE_EARTH_METALS` 是综合新闻研究桶，只用于提示相关候选；三者不是别名，不能互相代替审核或复用不覆盖目标行业的事实。
- 新契约中选中的每只基金都必须有明确服务器行业，且对应顶层审核包含五类反方核查。不能靠方向标题或模型声明补造基金行业。方向摘要可展示多个行业，每只基金的折价、评分和历史观察只使用自身范围的依据。
- 模型提议统一放在 `valueDirections[].plans`。新报告的旧顶层 `actionablePlans` 必须为空，服务器验证后仍生成原有计划汇总。读取旧报告及未采用新契约的旧调用保持兼容。

五类风险、原文核验、发表时间与事件时间、行业主语、公司/报告期及实际回款核验均保留。未核验原文不提升为已确认事实；风险折价仍从原始服务器锚点计算，右轨价格不因行业左侧保护改写。历史风险不自动续期；单企盈利只在其事实覆盖范围内解除盈利类疑虑，不能解除其他行业的风险。

## 固定预算与覆盖缺口

本轮最多10个行业、每行业五类查询，总计最多50条计划查询；来源核验预算仍为24条，不按基金数量重复扩大。

候选优先级为历史风险、可选基金计划的行业、相关新闻、量化位置及其他目录行业，同层参考就绪状态与距买区距离。Prompt 与验证使用同一输入时钟和入场保护口径。

当重要行业超过10个时，不扩大预算：保留 `omittedIndustries`、`omittedPlanIndustryIds` 与每只计划的 `industryReviewEligible`。未覆盖基金不得入选计划，可保留观察方向；替换一个必查候选以挤入该基金仍会因覆盖缺失被拒绝。模型不能额外提交超过10条审核来绕过上限。报告保留覆盖数量与缺口列表。上述为研究任务预算；独立搜索执行回执尚未接入，`searchExecutionVerified=false` 的含义保持不变。

## 安全诊断与部署顺序

| 规则 | 含义 |
| --- | --- |
| `DIRECTION_DECLARED_INDUSTRY_UNKNOWN` | 模型声明的关联行业没有对应顶层审核 |
| `DIRECTION_PLAN_SECTOR_UNREVIEWED` | 服务器选中基金的规范行业缺少审核 |
| `INDUSTRY_REVIEW_COVERAGE_MISSING` | 其他必查候选缺少审核 |
| `INDUSTRY_REVIEW_BUDGET_EXCEEDED` | 模型审核数量超过本轮上限 |

缺失行业以独立 `failure_industry_codes` 字段保存，最多10个，仅允许标准代码闭集。未知模型标签、原始异常文本、Prompt、网页正文和个人信息不进入该字段。只有管理员接口可获得 `failureIndustryCodes`；普通报告查询返回空值。代码随该次失败执行的索引保存，不借用旧报告诊断。

未来部署需先按现有迁移流程应用 **098_daily_research_failure_industries.sql**，再切换匹配的新 API、Worker、Frontend 镜像。迁移只新增可空字段与约束，已有 FAILED/READY 记录保持原值，新列为 NULL；不重写历史报告、不重置任务、不自动重试。旧 API 可读取新增后的数据库；新 API/Worker 依赖098字段，不能先切应用再迁移。无需新增环境开关。

本次仅本地提交。未推送、部署、执行真实 AI、重跑当天任务或访问生产数据库。实际 NAS 验证应另行安排，明确人工触发及 Token 消耗后执行。

## 离线验证

`tests/unit/test_industrial_metals_research_regression.py` 固定生成两只基金的真实计算双轨价格、三种行业审核及 `industrial-metals-repricing`，经过完整报告验证与排序，覆盖缺审核拒绝、未知模型标签、审核齐全接受、五类核查不可省略、各公开副本不串风险、跨日报不串风险及跨行业盈利不解除观察。

数据库测试仅使用本机独立临时 PostgreSQL。包含098前后迁移、历史NULL、运行角色、不可覆盖终态、管理员投影及缺失代码闭集。测试结果及运行日志保存在忽略的 `artifacts/industry-plan-sector/` 和 `artifacts/manual-research/`。

2026-11-03 最终验证：默认后端全量 **4641 passed、233 skipped**（六条既有 python-jose UTC 弃用警告）；独立 PostgreSQL 相关回归 **161 passed**；前端全量 **612 passed / 66 files**。Ruff、Python编译、TypeScript、ESLint、生产构建、六项 smoke 配置测试、冲突标记、文档契约和迁移发现均通过，共95个迁移文件，最新098。测试 PostgreSQL 已停止。
