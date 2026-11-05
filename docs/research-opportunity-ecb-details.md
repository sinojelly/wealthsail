# ecbfb663：逐主题与基金观察核对

仅依据本次冻结材料、原验证时刻2026-11-05 23:52:55.691526北京时间。以下是原时点的只读技术资料，不是实时价格或买入建议。21项完整claims、unresolvedClaims、proposedScopes及各审核范围排序见本地ignored分析文件 `artifacts/research-opportunity/ecb-identity-analysis.json`。

## 21项主题

全部为NEW_TOPIC / INDUSTRY_IDENTITY_PENDING；全部原claims为限制/风险句，0项确定行业身份，0项已核实同范围基金关联。下表相关代码仅为模型声明，不能替代身份和审核。

| key | 标题 | 风险句数量 | 声明相关范围 | 原来源引用 | 已核对来源数 |
| --- | --- | ---: | --- | --- | ---: |
| ai-hardware-earnings | AI硬件：经营改善与海外估值重估的预期差 | 3 | SEMICONDUCTOR_AI, MEMORY, COMMUNICATION | s01, s03, s04, s05, s06, s22 | 0 |
| ai-cloud-cash-conversion | 算力租赁：从合同与Token分成走向现金验证 | 2 | AI, POWER | s02, s06 | 0 |
| financial-policy-transmission | 金融：稳市、并购和资管向经营传导 | 2 | FINANCE_POLICY, BROKER, BANK, FINTECH | s02, s17 | 0 |
| industrial-metals-repricing | 稀土有色：配置、许可与供给恢复分开验证 | 3 | RARE_EARTH, NONFERROUS | s07, s08 | 0 |
| physical-ai-commercialization | 机器人：标准、应用与现金约束共同验证 | 3 | ROBOT, ROBOTICS_MOBILITY | s10, s21 | 0 |
| smart-driving-orders | 智能驾驶：合资合作向车型和订单传导 | 2 | ROBOTICS_MOBILITY | s09 | 0 |
| innovative-drug-validation | 创新药：销售利润增长能否转为持续现金 | 3 | INNOVATIVE_DRUG, DRUG | s11, s02 | 0 |
| healthcare-clinical-translation | 医药临床：人体研究突破向后续试验传导 | 1 | HEALTHCARE_CLINICAL, DRUG | s12 | 0 |
| gold-energy-divergence | 黄金油气：供给风险与实际利率分别定价 | 3 | GOLD, GLOBAL_ENERGY | s13, s14, s15, s18, s06 | 1 |
| satellite-defense-delivery | 航天军工：低位技术等待独立交付证据 | 3 | AEROSPACE, DEFENCE, SATELLITE | s15, s16 | 0 |
| broad-market-liquidity-repair | 宽基：政策托底与需求偏弱中的分散观察 | 3 | BROAD | s02, s17 | 0 |
| consumer-policy-transmission | 消费：补贴与信贷支持等待终端兑现 | 2 | CONSUMPTION | s17 | 0 |
| dividend-exit-review | 红利低波：高分位退出评估与坐标冲突核对 | 2 | DIVIDEND | 无 | 0 |
| power-grid-investment | 电网：中标向合同、交付与回款传导 | 2 | POWER | s23 | 0 |
| solar-storage-repair | 光储：份额增加与盈利修复观点待报表验证 | 2 | POWER | s02 | 0 |
| battery-demand-validation | 电池：股权安排与消费政策等待需求验证 | 2 | SOLID_BATTERY, ROBOTICS_MOBILITY | 无 | 0 |
| us-rates-and-trade | 海外利率与选举：等待正式政策及通胀验证 | 1 | US_TECH, GOLD, FINANCE_POLICY | s18, s19, s14, s06 | 1 |
| life-science-cross-border-risk | 生命科学服务：清单诉讼与海外客户修复 | 1 | DRUG, HEALTHCARE_CLINICAL | s16 | 0 |
| shipping-cycle-validation | 航运：通航风险与运价盈利分别验证 | 1 | 无 | s15 | 0 |
| chemical-dye-pricing | 染料：中间体提价能否转为成交与毛利 | 1 | CHEMICAL | 无 | 0 |
| ai-content-monetization | AI内容：主题活跃与收入贡献落差 | 1 | GAMING | s02 | 0 |

## 39只有至少一条完整参考轨的基金

32只双轨有效，7只单轨有效；“CANDIDATE/入参考区”仅为量化状态，不是投资资格。本案所有基金均缺同范围五类可信审核，所有申购状态均UNKNOWN。已核对来源支持主题不代表其全部推演已核实。6只价格不完整及1只未扫描仍保存在46只完整目录入口。

| 代码 | 名称 | 确认净值 | 净值日期 | 左轨技术/参考状态 | 右轨技术/参考状态 | 规范范围或未确认原分类 |
| --- | --- | --- | --- | --- | --- | --- |
| 320007 | 诺安成长混合A | 2.3660 | 2026-11-05 | INSUFFICIENT / UNAVAILABLE | WATCH / WATCH | ACTIVE_GROWTH |
| 008281 | 国泰CES半导体芯片行业ETF联接A | 2.0198 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：SEMICONDUCTOR |
| 005609 | 富国军工主题混合A | 1.6580 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 161129 | 易方达原油A类人民币 | 1.9146 | 2026-11-04 | WATCH / WATCH | WATCH / WATCH | 未确认：None |
| 008298 | 华夏中证银行ETF联接A | 1.7540 | 2026-11-05 | INSUFFICIENT / UNAVAILABLE | WATCH / WATCH | BANK |
| 000051 | 华夏沪深300ETF联接A | 1.5878 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | BROAD |
| 012733 | 012733 易方达中证人工智能主题ETF联接A | 1.6500 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | AI |
| 011035 | 嘉实中证稀土产业ETF联接A | 1.0412 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | RARE_EARTH |
| 004432 | 南方有色金属ETF联接A | 1.4870 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | NONFERROUS |
| 004069 | 南方中证全指证券公司ETF联接A | 1.1093 | 2026-11-05 | INSUFFICIENT / UNAVAILABLE | CANDIDATE / AWAY | BROKER |
| 013477 | 华宝中证金融科技主题ETF发起式联接A | 0.7858 | 2026-11-05 | CANDIDATE / AWAY | CANDIDATE / AWAY | FINTECH |
| 018344 | 华夏中证机器人ETF发起式联接A | 1.0880 | 2026-11-05 | CANDIDATE / AWAY | CANDIDATE / AWAY | ROBOT |
| 003017 | 广发中证军工ETF联接A | 1.0293 | 2026-11-05 | CANDIDATE / TECHNICAL_IN_ZONE | WATCH / WATCH | DEFENCE |
| 008086 | 华夏中证5G通信主题ETF联接A | 1.9892 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | COMMUNICATION |
| 009179 | 嘉实中证主要消费ETF发起联接A | 0.9106 | 2026-11-05 | INSUFFICIENT / UNAVAILABLE | WATCH / WATCH | CONSUMPTION |
| 000216 | 华安黄金ETF联接A | 3.0545 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | GOLD |
| 162411 | 华宝标普油气上游股票人民币A | 0.9328 | 2026-11-04 | WATCH / WATCH | WATCH / WATCH | GLOBAL_ENERGY |
| 270042 | 广发纳斯达克100ETF联接人民币(QDII)A | 7.7714 | 2026-11-04 | INSUFFICIENT / UNAVAILABLE | WATCH / WATCH | US_TECH |
| 021607 | 南方上证科创板芯片ETF发起联接A | 2.7079 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：SCI_TECH_CHIP |
| 004854 | 广发中证全指汽车指数A | 1.2407 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：MOBILITY |
| 000962 | 天弘中证500ETF联接A | 1.3640 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：BROAD_500 |
| 110020 | 易方达沪深300ETF联接A | 1.7030 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 202015 | 南方沪深300ETF联接A | 1.3769 | 2026-11-05 | CANDIDATE / TECHNICAL_IN_ZONE | WATCH / WATCH | 未确认：None |
| 001632 | 001632 天弘中证食品饮料ETF联接C | 1.8895 | 2026-11-05 | INSUFFICIENT / UNAVAILABLE | WATCH / WATCH | 未确认：None |
| 011103 | 011103 天弘中证光伏产业指数C | 0.6467 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 161725 | 161725 招商中证白酒指数(LOF)A | 0.5926 | 2026-11-05 | INSUFFICIENT / UNAVAILABLE | WATCH / WATCH | 未确认：None |
| 001361 | 景顺长城中证科技传媒通信150ETF联接A | 1.1260 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 001629 | 天弘中证计算机ETF联接A | 0.7151 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 016185 | 广发电力ETF联接A | 1.0906 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 018027 | 嘉实中证智选航空航天科技ETF发起联接A | 0.8358 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 024194 | 永赢国证商用卫星通信产业ETF发起联接A | 1.1353 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 007300 | 国联安中证半导体ETF联接A | 3.1488 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 014880 | 天弘中证机器人ETF发起联接A | 1.0677 | 2026-11-05 | CANDIDATE / AWAY | CANDIDATE / AWAY | 未确认：None |
| 000218 | 国泰黄金ETF联接A | 3.1859 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：None |
| 023530 | 永赢国证通用航空产业ETF发起联接A | 0.8183 | 2026-11-05 | CANDIDATE / TECHNICAL_IN_ZONE | WATCH / WATCH | 未确认：None |
| 018094 | 博时中证机器人ETF发起式联接A | 1.1187 | 2026-11-05 | CANDIDATE / AWAY | CANDIDATE / AWAY | 未确认：EVENT_ROBOT |
| 008887 | 华夏国证半导体芯片ETF联接A | 1.5207 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：EVENT_MEMORY |
| 000307 | 易方达黄金ETF联接A | 2.8951 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：EVENT_GOLD_ENERGY |
| 012862 | 汇添富中证电池主题ETF发起式联接A | 0.6144 | 2026-11-05 | CANDIDATE / AWAY | WATCH / WATCH | 未确认：EVENT_SOLID_BATTERY |

所有条目均固定actionable=false/investmentEligible=false；价格沿用服务器原始坐标，没有根据主题文本生成或重新折价。完整观察DTO见本地 `artifacts/research-opportunity/ecb-fund-observation.json`。
