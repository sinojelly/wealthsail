# 每日研究模型输入审计与安全精简

2026-11-07，本地离线，未访问 NAS、生产数据库或 AI。基线为 `ecf3f902ac3c18be2b80b99448b4bfebb17849d1`，样本为本地忽略目录的 `artifacts/research-recovery/ecbfb663-frozen.json`。报告只含字段名、数量和字节统计，不提交冻结正文、Prompt、网页或模型输出。

## 同一冻结样本的实际对照

| 项目 | 旧版 UTF-8 字节 | 新版 UTF-8 字节 |
| --- | ---: | ---: |
| `build_prompt()` 完整文本 | 592,170 | 403,055 |
| 其最后一行实际模型 JSON | 571,818 | 390,280 |
| 自然语言指令（含分隔换行） | 20,352 | 12,775 |
| 独立传入 CLI 的 `REPORT_SCHEMA` | 16,266 | 16,266 |

Prompt 减少 **31.94%**。字节下降不等于 Token 或运行时间按同一比例下降；CLI 会话累计 Token 包含后续工具结果和推理，不能从初始 Prompt 大小反推。故障运行的 589,635 字节属于另一研究，本报告不能把它当作本样本旧值。Schema 独立传入 `--output-schema`，未加进 Prompt 字节，也未删减任何正式验收字段。CLI 隔离指令、平台上下文与运行中工具响应不在此初始 Prompt 口径内。

直接调用旧 `model_packet(原冻结包)` 为 570,924 字节，和最终 JSON 相差 894 字节：`build_prompt()` 在投影前应用冻结时点安全保护、构建行业合同，并加入身份注册表与分配元数据。最终比较使用真实旧 `build_prompt()`，不以直接函数调用值代替。

## 链路和兼容边界

`research_inputs.prepare_research_inputs()` 准备公开新闻、ETF 与净值；`make_research_packet()` 形成完整公开原包；`research_topics.freeze_discovery_packet()` 固定研究身份、版本、窗口、行业分配及精确任务；`build_prompt()` 应用原时点保护后创建模型阅读投影；`codex_research` 将 Prompt 和独立输出 Schema 送入单次 CLI 会话。研究执行、来源核验、价格复制和最终验收继续使用原始 Packet。

本次只为新 Prompt 使用 `daily-model-input-v2`，Prompt 版本为 `daily-research-input-v22`。`model_packet()` 的默认 v1 仍保留，未知投影版本拒绝。冻结发现合同、任务哈希、预算版本、原始 Packet、原始模型结果和历史终态都未改写；历史报告不需要按新版输入重做推理。既有历史冻结离线重验继续执行。

## 字段决策

| 输入 | 模型需要保留 | 精简方式与安全依据 |
| --- | --- | --- |
| `quantitativeScan` | 45 只实际扫描基金的身份、日期、口径、当前价、完整度、双轨 ID/状态、逐轨入场/退出/阻断状态、申购限制、行业义务、RSI/中期均线/趋势/高位及相对强弱缺口 | 删除服务器计算的买区/止损/目标/细碎分位几何、展示色调和重复技术解释；最终所选计划仍从原包按标的及轨道复制，不从精简输入重算价格 |
| `previousFundPlans` | 16 个旧计划的同市场/同轨/同口径身份、日期、来源、关键旧坐标及风险 | 删除旧指标、信号及未选轨道，避免第二份完整量化表；Decimal 版本比较仍读原包 |
| 新闻多个入口 | 所有 144 条已选催化、7 份文档、候选来源与全部机构/公司事实，原有日期、来源 ID、未知和风险 | 仅完全相同的字面文本使用 `sharedText` 引用；不按 URL、标题相似度或哈希交集合并经济事实，来源各自日期和身份不变 |
| `alphaPublic` 共同元数据 | 来源、实际检索时刻、事实核对状态、阶段等 | 同集合全体完全一致的字段放入明确的 Defaults；逐项缺省继承，任何不同值仍逐项保留；不升级核验状态 |
| 宏观观察 | 全部 100 组、全部原始观察点、来源时刻差异、频率/单位/定义断点 | 只把完全一致的 metadata 提成精确字典引用，未抽样或裁剪历史点；序列、趋势和当前宏观值也保留 |
| 历史事件/问题/方向/日历/行业风险 | 全部原投影允许的事件、未解决问题、反方、阶段、原来源、未解除风险 | 不删除事实；可重复的文字经可靠 ID 读取；`previousIndustryRiskStates` 和完整行业合同不做文本替换 |
| 来源结果 | `sourceResults`、`alphaPublic.sourceResults`、逐基金动态净值成功/短历史/失败状态 | 不把不同采集阶段的状态误当重复，不删失败或未知 |
| 行业审核 | 冻结 taskId、industryId、kind、完整 query、版本、窗口、选择及延期风险列表、每基金审核资格 | 10 范围/50 查询不变；精确 query 不替换、不截断。每基金义务已在扫描逐项保留，去掉重复顶层逐基金表；延期范围仅不再复制其非执行查询词，延期行业/风险/缺口仍保留 |
| Prompt | 五类核查、来源/日期、公司报告期、未知、风险继承、合法身份/价格保护、结构化引用 | 按事实来源、经济研究、基金机会、行业风险、输出合同五部分整理；不要求填满 64 主题，不增加模型会话，不把软搜索预算说成硬拦截 |

`previousResearch`、`previousValueDirections`、`previousQuestions` 与 `previousUpcoming` 有时间/风险连续性价值，没有因重复嫌疑整体删除。`sourceResults` 的两个入口含不同采集结果，没有做跨入口强制合并。`documents`/`candidateIndex`/新闻/机构数据之间只有完全相同文本共用一份正文，各事实和引用身份仍可单独读取。

## 重复统计

统计字符串只计长度至少 64 UTF-8 字节的完全相同字面值。旧版 50 个重复值、187 次额外出现、29,935 字节额外字面文本；新版仍字面重复 4 个值、44 次额外出现、3,098 字节。新版引用表含 40 份字面文本；剩余重复有些属于必须原样保留的行业查询及身份/口径合同，未为指标继续压缩。

跨新闻/文档/机构入口，原先 175 条含文本记录中 158 个精确不同文本，额外重复 17 次。新版物理存储的记录文本无精确重复；还原 `textRef` 后仍为同样的 175/158/17，证明这些事实仍然可读，不能把物理去重误写成删掉 17 条证据或获得更多独立来源。

## 体积 Top 20

以下排序包括顶层和主要子字段；父子相互重叠，不能累加。元素数是该对象属性数、数组长度或聚合的同名字段数量。旧版列按原序列化空格格式计，新版列按实际紧凑 JSON 格式计。

### 旧版

| 排名 | 字段 | UTF-8 字节 | 元素数 |
| ---: | --- | ---: | ---: |
| 1 | `alphaPublic` | 220,270 | 7 |
| 2 | `quantitativeScan` | 160,103 | 5 |
| 3 | `quantitativeScan.items` | 159,912 | 45 |
| 4 | `alphaPublic.catalysts` | 142,364 | 144 |
| 5 | `alphaPublic.observations` | 73,991 | 100 |
| 6 | `institutionalSignals` | 55,081 | 6 |
| 7 | `previousFundPlans` | 36,591 | 16 |
| 8 | `previousResearch` | 31,254 | 12 |
| 9 | `institutionalSignals.etfFlows` | 29,425 | 32 |
| 10 | `industryCycleResearch` | 23,980 | 19 |
| 11 | `institutionalSignals.corporateActions` | 18,607 | 18 |
| 12 | `previousFundPlans[*].tracks` | 17,922 | 16 |
| 13 | `previousResearch[*].sources` | 17,341 | 12 |
| 14 | `previousValueDirections` | 11,094 | 8 |
| 15 | `industryCycleResearch.candidates` | 11,031 | 10 |
| 16 | `previousFundPlans[*].indicators` | 8,821 | 16 |
| 17 | `institutionalSignals.researchViews` | 6,568 | 6 |
| 18 | `series` | 6,481 | 10 |
| 19 | `industryCycleResearch.planReviewEligibility` | 5,552 | 45 |
| 20 | `candidateIndex` | 5,198 | 16 |

### 新版

| 排名 | 字段 | UTF-8 字节 | 元素数 |
| ---: | --- | ---: | ---: |
| 1 | `alphaPublic` | 168,013 | 10 |
| 2 | `alphaPublic.catalysts` | 110,739 | 144 |
| 3 | `quantitativeScan` | 85,344 | 5 |
| 4 | `quantitativeScan.items` | 85,165 | 45 |
| 5 | `alphaPublic.observations` | 50,415 | 100 |
| 6 | `institutionalSignals` | 30,368 | 7 |
| 7 | `previousResearch` | 28,717 | 12 |
| 8 | `industryCycleResearch` | 17,644 | 18 |
| 9 | `previousResearch[*].sources` | 15,324 | 12 |
| 10 | `industryCycleResearch.candidates` | 13,682 | 10 |
| 11 | `institutionalSignals.etfFlows` | 13,602 | 32 |
| 12 | `sharedText` | 11,577 | 40 |
| 13 | `previousValueDirections` | 10,810 | 8 |
| 14 | `institutionalSignals.corporateActions` | 10,660 | 18 |
| 15 | `previousFundPlans` | 8,133 | 16 |
| 16 | `series` | 5,829 | 10 |
| 17 | `institutionalSignals.researchViews` | 5,595 | 6 |
| 18 | `candidateIndex` | 4,332 | 16 |
| 19 | `dynamicNavSourceResults` | 4,278 | 46 |
| 20 | `documents` | 3,797 | 7 |


## 零 AI 验证和复现

运行 `python scripts/audit_daily_research_input.py --frozen artifacts/research-recovery/ecbfb663-frozen.json --baseline-ref ecf3f90`。脚本仅读取显式本地 Git 基线和冻结文件，核对完整性、SHA-256、原包未变；重建旧模块与旧 Prompt；标准输出只有聚合统计，错误只输出安全异常类别。样本不在时不访问生产补齐。

本批测试覆盖字面去重还原、共用 URL 的不同事实、原发布日期差异、宏观定义断点/全部点、任务身份/完整查询不变、风险不解除、旧版投影与 Schema 兼容、原包不变、真实样本至少 30% 精简。既有价格复制/版本比较/申购限制/行业资格/反方审核与三个真实冻结样本继续做离线回归。具体最终测试总数见本轮交付记录。

模型仍可能在单会话中重复搜索、打开网页或产生高累计 Token；本批 Prompt 只能指导复用，不能保证外部 CLI 具有预执行硬限额。相同预算和运行时口径的实际 Token、缓存、执行耗时、合格来源/机会数与超时率，须后续受控灰度测量，不能承诺从 15 分钟变为 2–3 分钟。
