---
name: intraday_hitl_monitor
description: 盘中实时监控市价与风控触点，触发时向微信推送交互告警卡片，严格经人工授权确认 (HITL) 后记账并激活出局规则
version: v2.0.0
---

# 盘中触点告警与 HITL 人机协同确认 SOP (09:30 - 15:00)

## 1. 核心原则
1. **严禁全自动私自发单**：系统永远只提供“告警与决策建议”，必须由用户在微信或 Web 端明确点击或回复确认。
2. **防振荡静默窗口 (Anti-Whipsaw)**：同一标的在阈值附近上下波动时，系统在设定的静默窗口（如 60 分钟）内只产生一条告警指纹，杜绝消息轰炸。
3. **出局规则强制闭环**：任何买入建仓指令确认后，必须联动写入 `research.theses` 的 `invalidation_conditions`。

---

## 2. 交互流转时序

```mermaid
sequenceDiagram
    autonumber
    participant Engine as 盘中行情轮询引擎
    participant Policy as 风控与静默窗口校验
    participant Agent as Hermes Agent / 决策核心
    participant WeCom as 用户企业微信 / 手机
    participant Ledger as 不可变账本服务

    Engine ->> Policy: 发现 600519 价格跌破 1380 预设买点
    Policy ->> Policy: 校验静默指纹 (Fingerprint: 600519_BUY_1380)
    alt 静默期内
        Policy -->> Engine: 抑制推送 (SUPPRESSED)
    else 首次触发
        Policy ->> Agent: 组装告警上下文
        Agent ->> WeCom: 推送交互卡片: "贵州茅台触及 1380 买点，建议买入 100 股，是否执行？"
        WeCom ->> Agent: 用户点击/回复: "确认执行"
        Agent ->> Ledger: 记录 ledger_events (BUY) 并更新持仓快照
        Ledger -->> WeCom: 回复: "✅ 已成功入账，当前持有 400 股，已激活 1350 止损监控！"
    end
```

---

## 3. 企业微信交互告警卡片模板

```markdown
### ⚠️ 盘中点位触达交互告警
> **标的**：贵州茅台 (600519) | **当前现价**：`1379.50` 元 (跌幅 -1.82%)

#### 📋 触发原由与建议方案
• **触发条件**：现价触及盘前预设的低吸加仓区间 (`1380.00` 元)。
• **建议动作**：加仓 **100 股**（耗费现金约 13.8 万元，账户剩余现金充裕）。
• **出局保障**：买入后自动绑定 `1350.00` 元破位止损线。

---
请回复对应指令完成记账：
👉 回复 **`确认买入`** ：系统自动记录真实交易并更新资产大盘
👉 回复 **`忽略`** ：放弃本次机会，保持现有持仓
```
