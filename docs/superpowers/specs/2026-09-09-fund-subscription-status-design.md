# 天天基金申赎状态同步设计

## 目标

在交易日盘前报告生成前，同步所有当前持有场外基金的可验证申购、赎回状态。盘前个性化报告和企微推送据此展示“暂停申购”“暂停赎回”或既有的截止提醒；无可验证状态时保持现有降级文案，不推断开放状态。

## 范围

本设计仅覆盖用户需求的第二优先级：天天基金申赎状态变化。

- 不抓取基金分红；这是后续独立功能。
- 不改变持仓、报告、推送 API 的地址或响应格式。
- 不创建或修改 `workflow.jobs`；这是全局市场事实同步，不是租户报告任务。
- 不直接授权 Worker 写 `reference.fund_profiles`。

## 数据源与状态映射

使用天天基金公开申赎状态列表（AkShare `fund_purchase_em`）作为当前状态的结构化来源。实测该列表包含基金代码、基金简称、申购状态、赎回状态和下一开放日，且覆盖当前示例持仓 `110022`、`012733`、`001632`。

`fund_purchase_em` 是状态快照而非公告原文。因此首版报告只陈述当前可验证状态，不编造暂停原因或公告标题。后续“基金分红”阶段可以单独增加公告标题和来源链接。

状态标准化必须保守：

| 原始文本 | 写入状态 |
| --- | --- |
| 包含“暂停”或“限制” | `SUSPENDED` |
| 包含“开放”或“正常” | `OPEN` |
| 空值、未知文本、抓取失败 | 不写入、不覆盖既有有效记录 |

每项同步结果带 `TIANTIAN_FUND_STATUS` 数据源标识。原始状态文本和下一开放日作为审计元数据保存，避免将“限额申购”误解释为完全开放。

## 数据库边界

新增迁移 `061_tiantian_fund_subscription_status.sql`：

1. 注册 `reference.data_sources` 行 `TIANTIAN_FUND_STATUS`。
2. 为 `reference.fund_profiles` 增加 `source_ref`、`source_metadata`、`retrieved_at`，以保存来源方法、原始文本和采集时间。
3. 建立 `reference.upsert_fund_profile_status(...)`，使用固定 `search_path` 的 `SECURITY DEFINER` 函数作为唯一写入路径。
4. 函数仅接受 `OPEN` 或 `SUSPENDED`，验证来源为活动状态数据源；当状态没有变化时只更新采集信息；状态变化时关闭旧行（`effective_to = p_effective_from - 1`）并写入新版本。
5. 撤销 `reference.fund_profiles` 的 Worker 直接写权限，仅授予函数执行权限。

这样，历史状态以生效区间保留；报告读取既有的 `get_pre_market_fund_profiles` 查询即可按报告日获得正确状态。

## 同步流程

新增 `backend/integrations/fund_subscription_status.py`，只负责调用 AkShare、读取状态列表、按六位基金代码过滤和保守标准化，不访问数据库。

新增 `backend/jobs/fund_subscription_status_sync.py`：

1. 从所有租户最新的 `ledger.position_snapshots` 读取正数量、`OFF_EXCHANGE_FUND` 的活跃持仓，并按 `reference.instruments` 去重。
2. 一次调用天天基金状态列表，仅保留这些持仓的代码。
3. 对每个可识别的状态，通过 `reference.upsert_fund_profile_status` 持久化；一个基金失败不会中断同批其他基金。
4. 记录 `scanned`、`available`、`changed`、`unchanged`、`unavailable`、`failed`，但日志不包含用户、租户或持仓数量明细。

在 `backend/jobs/scheduler.py` 增加 `FUND_SUBSCRIPTION_STATUS_REFRESH`，交易日每天 06:05 运行。在 `backend/worker.py` 作为全局刷新分支执行：它在 08:30/08:35 盘前报告之前完成，且不触发报告任务。

## 报告与推送

不改变现有报告结构：`backend/reports/pre_market.py` 已经优先将 `fund_profiles` 中的 `SUSPENDED` 渲染为“今日暂停申购/赎回（以渠道状态为准）”。本功能只提供该输入。

若数据源没有某只持仓基金、返回未知状态或同步失败，保留现有“今日无申赎截止提醒”及场外基金 15:00 渠道提示；绝不把缺失状态写成 `OPEN`。

## 测试与验证

测试覆盖：

1. 状态列表解析：仅处理请求的六位基金代码；“暂停/限制”映射为 `SUSPENDED`，“开放/正常”映射为 `OPEN`，未知状态被丢弃。
2. 同步任务：跨租户相同基金只处理一次；成功写入受控函数；单个基金异常不影响后续基金。
3. 数据库迁移契约：函数固定 `search_path`，Worker 没有表写权限，只拥有函数执行权限。
4. 盘前报告回归：同步产生的 `SUSPENDED` 档案继续生成暂停申赎提醒，缺失状态仍使用现有降级文案。

部署验证：执行迁移后重启 Worker，手动运行一次全局同步或等待下一个交易日 06:05；检查 Worker 的汇总日志、`reference.fund_profiles` 最新生效记录，并用管理员调试接口生成盘前个性化报告验证提醒。
