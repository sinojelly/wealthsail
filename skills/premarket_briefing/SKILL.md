---
name: premarket_briefing
description: 每日盘前 08:30 自动聚合隔夜外盘、标的异动与投资论点，生成结构化 If-Then 操盘剧本并通过企业微信推送到手机
version: v2.0.0
---

# 盘前推演与 If-Then 操盘剧本生成 SOP (08:30 - 09:15)

## 1. 目标与定位
在交易日早盘 `08:30`，为投资者提供一份**有时态、有来源证据、有严格 If-Then 预案、无虚假脑补**的盘前推演报告，协助投资者在开盘前于券商 App 预设条件单，打破情绪化盘中冲动。

---

## 2. 思考链路 (Chain of Thought - CoT)

```mermaid
flowchart TD
    Step1[1. 识别当前 Tenant 身份与持有标的] --> Step2[2. 检索标的有效投资论点 theses 与出局条件]
    Step2 --> Step3[3. 抓取隔夜美股/中概/A50/大宗快照并打标 FRESH/STALE]
    Step3 --> Step4[4. 校验数据质量: 若关键数据不可用则输出 ABSTAIN 说明]
    Step4 --> Step5[5. 计算关键技术支撑位、阻力位与估值安全边际]
    Step5 --> Step6[6. 组装结构化 If-Then 剧本: 明确触发价、预期动作与仓位比例]
    Step6 --> Step7[7. 校验风控政策: 确保任何买入建议不突破单标的与大类仓位上限]
    Step7 --> Step8[8. 生成 Markdown 微信卡片并原子写入 reports 与 outbox]
```

---

## 3. 输出 JSON Schema 约束

LLM 生成的结构化输出必须严格符合以下 JSON 契约：

```json
{
  "report_session": "PRE_MARKET",
  "trade_date": "YYYY-MM-DD",
  "data_quality_status": "FRESH",
  "macro_summary": "隔夜美股纳指收涨0.8%，富时A50期指微跌0.15%，国内政策聚焦新质生产力。",
  "items": [
    {
      "item_type": "ACTION_PLAN",
      "symbol": "600519",
      "asset_name": "贵州茅台",
      "support_price": 1380.00,
      "resistance_price": 1460.00,
      "if_then_plan": {
        "if_break_above": "若早盘放量冲高突破 1460 阻力位，持有不动，不追高；",
        "if_pullback_below": "若缩量回调至 1380-1390 支撑区间企稳，可按计划补仓 100 股（消耗可用资金约 20%）；",
        "if_invalidation": "若放量跌破 1350 止损底线，触发论点失效审查，准备逢反弹分批减仓。"
      },
      "severity": "INFO"
    }
  ],
  "policy_check": {
    "passed": true,
    "current_equity_pct": 52.5,
    "equity_limit_pct": 60.0
  }
}
```

---

## 4. 企业微信富文本卡片模板 (Markdown)

```markdown
### 🌅 WealthSail 盘前操盘剧本 | 2026-08-23
> **数据时态**：2026-08-23 08:30:00 (FRESH) | **组合净值**：506,000 元

#### 🌐 隔夜宏观与外盘风向
• 美股纳指 +0.82%，富时中国 A50 期指 -0.12%，离岸人民币汇率 7.2150 运行平稳。
• 大宗商品：Brent 原油 $82.5/桶，上海金现货 568.2 元/克。

#### 🎯 持仓标的 If-Then 操盘预案
• **【贵州茅台 (600519)】**（成本 1400.00 元 | 最新 1420.00 元）
  - **支撑位**：`1380.00` 元 | **阻力位**：`1460.00` 元
  - 🟢 **低吸计划**：若盘次回调至 **1380 ~ 1390 元** 企稳，建议补仓 **100 股**（约占总资金 2.7%）。
  - 🔴 **防守出局**：若有效跌破 **1350.00 元** 止损底线，触发投资论点失效，执行减仓。
  - ⚪ **突破应对**：若直接冲高突破 1460 元，持有不动，严禁盘中追高。

---
💡 *提示：您可依据上述点位提前在券商 App 预埋条件单。盘中触及点位时系统将推送即时告警卡片。*
```
