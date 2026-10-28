# 基金买卖区间 v6

`actionable-zones-v6` 使用同基金真实单位净值、近两年高低跨度和历史分位生成买卖区间。预估盈利是价格区间之间的算术空间，并非收益或命中率回测。

## 样本与同步

- 后台同步保留550个真实交易样本，查询覆盖900个自然日；完整交易日历路径额外预留披露延迟。已有浅历史会补齐，完整后恢复增量同步。
- 算法与默认“近2年”图共用最新披露净值日回溯24个月、最多500点的窗口与选源规则，至少120点才计算历史锚点。两年实际交易日可能少于500，不补造样本。
- `market.market_observations`保存逐日NAV事实；`market.fund_research_nav_histories`和`market.fund_nav_chart_histories`保存带来源证明的历史。仓库没有`fund_nav_daily`表。
- 图表覆盖两年起点不等同于可执行历史完整；来源证明、缺口、未来数据与币种身份仍需校验。净值日期超过10天或来源核查超过36小时也仅供参考，不产生买入就绪信号。

## 支撑、阻力和盈利

真实历史价格线性插值得到p30/p40/p45/p75/p92等分位，另计算最低价`troughLow`、最高价`peakHigh`及跨度。`span(r)=troughLow+r×(peakHigh-troughLow)`。

中高位（近一年分位至少65%，或现价高于跨度60%）买区：

```text
high = min(MA60, MA200或MA60, p45, span(0.45))
low  = max(troughLow, min(high-0.8s, p30, span(0.32)))
```

中低位买区：

```text
high = min(MA20, close+0.3s, p40, span(0.35))
low  = max(troughLow×0.98, min(bollLower, close-0.6s, high-0.6s))
```

卖区：

```text
minTarget = max(high×1.12, MA200或0, p75, span(0.60))
cap       = peakHigh×0.992
sellHigh  = min(cap, max(minTarget+1.2s, p92, span(0.82)))
sellLow   = min(minTarget, sellHigh-0.8s)
```

显示价格舍入后仍保证卖区上沿不超出封顶值。预估盈利下限=`(sellLow-high)/high×100`，上限=`(sellHigh-low)/low×100`，按实际显示价格计算并保留两位小数。现价低于买区上沿时另算当前至卖区上沿空间。

历史峰值封顶可能与12%最低空间发生冲突：按真实封顶保留可计算参考并标PARTIAL；无法形成高于买区的有效卖区时不输出伪目标或盈利。页面对依据不足的结果不展示可执行盈利区间。

## 旧报告的实时返回

每次读取机会页，服务端从本地最新历史批量重算旧v4/v5计划，返回v6价格、状态和盈利字段，无需重新生成整份日报。没有有效本地历史时清除旧点位并说明不可用。GET不等待外部行情、不写回原日报。

研究原文与日期保留为`researchAsOf/researchMethodVersion`；`asOf`和`priceRefresh`表示本次价位的净值日期与计算时间。前端分别展示研究日期与价位更新日期。已有个人冻结版本保持原记录，新建跟踪按页面同一套实时规则验证并冻结。
