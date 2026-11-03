# 动态研究发现与风险审核分批契约

本次按用户授权先设计后实现；不推送、不部署、不连接生产或NAS、不实际调用模型。保留101历史只读恢复能力和原始不可变记录。

## 数据契约与架构选择

采用独立主题目录，而非扩大valueDirections的maxItems或自动循环调用模型。旧valueDirections仅是一个批次的正式计划投影，最多8方向；新topicCatalog每批最多64个发现条目、原始输出仍最多2MiB。跨研究批次注册总数不设8/10等逻辑上限，数据库按不可变快照分页读取（每页最多30）。每批最多一次模型调用、10个审核范围、50个五类查询计划、24来源。翻页不执行模型；预算不足生成明确的待审任务，下一个获授权批次可以选择积压范围继续核查。

TopicProposal为key/title/thesis/industryClaims/relatedIndustryIds/sourceIds/unknowns/pendingTasks；不接受模型投资许可或价格。主题身份是服务器生成稳定ID，模型key只是出处。标准产品行业仍由server fund identity/catalog得出。匹配结果带注册表版本、依据和STANDARD/STRICT_ALIAS/COMPOSITE/NEW_TOPIC/UNMATCHED；新闻RARE_EARTH_METALS与NONFERROUS仅相关，不是等价审核。来源与原文归属不支持的匹配保持待核验。

服务器主题状态为FORMAL、OBSERVATION_ONLY、PENDING_REVIEW、QUARANTINED。每条包含原因、影响、已采取措施、待补证任务、sourceIds、审核状态、匹配记录及investmentEligible。investmentEligible只由完整严格校验结果决定；主题只读接口整体actionable=false，无plans/价格/跟踪origin。预算、事实审核和执行凭证分别展示：五项结构核查不是独立搜索执行凭证，searchExecutionVerified缺失仍明确false。

输入冻结researchDiscovery v2与auditManifest：选中范围、全部待审范围、原90日窗口、注册表版本、资源预算、范围/窗口去重任务ID。Prompt、validator、registry均消费同一个冻结manifest，不能各自重算后漂移。五类风险不裁剪，FOUND不改NOT_FOUND，UNVERIFIED不改NOT_FOUND。

## 正式与观察的兼容

V1严格validate_report接口与历史规则保持。V2先逐项归档主题及问题，只有同范围且有确定性证据才修复关联。缺审核基金的整个方向移入待审，相关计划永不放行；高重要性/系统性负面、失效来源、历史风险或影响其他保留方向的缺口阻止自动正式发布。事件/日历依赖按原引用闭包隔离，原文及反方附件保留。

合格正式投影完整执行公开strict validator、风险合并、历史风险保护、价格和评分。V2候选覆盖规则区分“本批计划但未完成的任务”与“正式计划必需审核”：前者形成显式覆盖缺口，后者必须完整。不能把待审主题或相关行业当作已审核来绕过要求。只有独立通过的正式部分可READY，并附部分覆盖标记；没有安全正式结果则继续FAILED与观察成果。旧READY下游只接严格正式投影，永不消费主题注册表。

## 迁移102与故障隔离

102新增不可变主题快照及条目。源run的租户/公共范围、原validated_at、三材料哈希从服务器归档派生；客户端不能写tenant、证据或投资许可。快照带规则/代码版本、内容摘要、状态统计和预算覆盖；条目按ordinal稳定游标分页。每快照正文总量有界，超限拒绝并保留原冻结材料，不静默topN。

正式结果与原归档待办继续原子入队；主题目录作为待办有限旁存，由归档补偿在独立事务落盘。主题存档失败不撤销已严格READY。数据库ENABLE/FORCE RLS，worker窄写权限、API只读安全投影和当前有效成员校验，无任意删除权限。旧FAILED/INITIAL、101衍生、usage、调度和pipeline不更新。

新批次可读取同权限范围的待审行业任务，保留来源run和证据窗口；后续补证是新时间/新批次，不把新材料塞回历史证据时钟。没有执行新研究时仅保存积压，不自动追加AI。

## 零AI历史兼容

沿用冻结哈希/身份/大小/证据时钟检查、固定受限子进程、配额、幂等、独立管理员审核及历史只读发布。V1真实ceaa7c1c仍按原契约得到6方向/8计划/2事件/1日历；旧材料不能套V2宽松规则伪造审核。新代码可以额外解释主题状态，但原INITIAL和原衍生报告绝不覆盖。历史输出始终HISTORICAL_READ_ONLY，不进入旧READY交易、跟踪、提醒或机会路径。

## 风险与分阶段验收

主要风险是把匹配当审核、把分页预算重复计额、跨租户读积压、遗漏高重要性依赖、以及让附加存档连带正式结果。分别以独立匹配/审核字段、冻结manifest、源范围RLS、依赖闭包和原归档补偿隔离。

阶段1：纯契约、匹配、任务分页与12/20/30主题TDD。阶段2：V2确定性验收、Prompt/schema与首次终态接线。阶段3：102存档、积压读取、API及独立PG。阶段4：前端主题分页/覆盖与历史只读兼容。阶段5：两次真实失败场景、预算/权限/事务/单AI调用回归，全量/静态，本地功能提交。
