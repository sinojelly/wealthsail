# ADR-002：指数估值历史不足时保留缺口，授权新源独立计算

- 状态：当前缺口处理已采用；授权长历史源接入待部署选择
- 日期：2026-10-14
- 范围：A股指数PE_TTM/PB历史、来源及计算口径
- 关联：[设计](../24.支持基金筛选的方案.md)、[验收](../../ai-review/investment-data-evidence-acceptance.md)

## 背景与证据

锁定AKShare 1.18.94的stock_index_pe_lg/stock_index_pb_lg调用乐咕index-basic-pe/pb接口，原样读取data数组，没有61条截断，也没有日期/分页参数。本次沪深300实际各返回61期，未达到既定三年/756期门槛。[官方文档](https://akshare.akfamily.xyz/data/stock/stock.html)仍描述全部历史；不能据此宣称本次已取得多年历史，也未确认覆盖不足的原因。

| 选项 | 已核查能力 | 限制 |
| --- | --- | --- |
| 当前乐咕源 | 沪深300滚动PE/PB，已联机读取 | 本次各61期，保持INSUFFICIENT |
| 中证官方历史 | stock_zh_index_hist_csindex提供滚动市盈率 | 无PB，且未完成沪深300连续覆盖实测 |
| 旧基金数据源教程 | index_value_hist_funddb曾用于估值历史 | 锁定库已不存在，不使用过时接口 |
| 理杏仁开放API | cn/index/fundamental支持多年pe_ttm.mcw与pb.mcw | 需要账户Token；来源方法独立核验 |

中证能力见[官方接口说明](https://akshare.akfamily.xyz/data/index/index.html)。理杏仁接口与参数见[开放API](https://www.lixinger.com/api/open-api/html-doc/cn/index/fundamental)，计算差异见[官方口径说明](https://www.lixinger.com/wiki/stock-collection-value-calculation)。核查未访问用户凭据或登记测试映射为正式事实。

## 决策

当前继续保存可得估值事实，显示实际区间和样本，历史不足不进入价值研究候选。不降低门槛，不补假日估值，不把不同来源/方法拼接为756个同口径观察值。

需要授权数据源时，另做独立适配器及来源登记；Token通过Worker配置注入，不能进入API响应、数据库来源URI、日志或Git。指数身份、方法、发布时间与来源核验后，按新方法版本重新生成PE/PB区间分位及资格。旧快照保留，读取不得沿用已失效配置的资格。当前不创建账户、不采购数据、不假定已有Token。

## 后续验收

授权接入须先TDD验证时间窗口、分页/分段、负PE、缺期、异常及撤销；再实测同方法至少三年/756期双指标、发布窗口和连续更新。数据覆盖验收与策略有效性验证仍独立，不能把历史接口可用当成投资价值已验证。
