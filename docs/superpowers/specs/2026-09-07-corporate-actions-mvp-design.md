# 公司行动最小可行版本设计

## 目标

让人工维护的除权除息、分红派息、配股和其他重大公司行动成为可审计的持久化事实，并进入当天至未来三天的盘前个性化简报。报告生成不新增 AI 调用，不改变 08:30 宏观简报与 08:35 个性化简报的调度边界。

## 现状与决策

系统已经在 `market.corporate_actions` 保存公司行动，并通过 `wealthsail_api` 只读访问。因此不在 `reference` schema 建立同名表，而是向现有表增加通用事件日期、摘要、来源事件标识和来源链接。

现有 AkShare 适配器把单标的 `stock_dividend_cninfo` 当作全市场接口调用，结果不持久化且无法形成稳定、可审计的数据源。盘前报告改为只消费已持久化的公司行动；适配器保留给后续采集工具使用，但不参与生产报告事实合并。

## 数据契约

`market.corporate_actions` 保留现有字段和历史数据，并增加：

- `action_date DATE NOT NULL`：报告筛选和排序使用的事件发生日期；历史数据由 `ex_date` 回填。
- `summary TEXT NOT NULL`：不超过 500 字的用户可读说明。
- `source_event_id TEXT NOT NULL`：来源内稳定事件标识；历史行用 `legacy:<id>` 回填。
- `source_ref TEXT`：可选公告或来源链接。

`ex_date` 改为可空；`action_type` 保留原值并增加 `EX_RIGHTS`、`RIGHTS_ISSUE`、`OTHER`。新增唯一约束 `(source_event_id, revision)` 和索引 `(action_date, instrument_id)`。同一事件的更正通过增加 revision 追加，不覆盖历史版本。

迁移注册 `MANUAL_CORPORATE_ACTIONS` 数据源。运行时角色仍只有 `wealthsail_api` 可以读取；API、Worker、浏览器角色均不能写入。

## 导入契约

`scripts/import_corporate_actions.py` 读取 UTF-8/UTF-8 BOM CSV。必填列为 `symbol,market,action_type,action_date,summary`；可选列为 `asset_name,ex_date,record_date,pay_date,source_event_id,revision,source_ref`。

脚本使用 `MIGRATION_DATABASE_URL`，默认只预检，只有 `--apply` 才写入。所有行先完成字段、日期、行动类型和标的解析校验，再在单个事务中写入。标的必须能通过 `symbol + market` 唯一解析，脚本不得自动建立主数据。未提供 `source_event_id` 时，根据标准化业务字段计算稳定 SHA-256 标识。重复的 `(source_event_id, revision)` 跳过；更正必须显式增加 revision。

## 报告契约

报告上下文读取持仓与关注列表的 instrument_id 并查询 `[report_date, report_date + 3 days]`。查询必须先按 `source_event_id` 选择最高 revision，再按日期窗口过滤，避免已更正到窗口外的旧版本泄漏。

08:35 个性化报告在现有“持仓今日影响”和“关注标的动态”中显示公司行动；无持仓无关注用户仍只收到 08:30 宏观简报。完整审计报告显示所有匹配事件；企微摘要按日期、代码、类型排序后最多显示前三条，超过三条追加“等共 N 条”。无记录时显示“今日及未来3天暂无已录入公司行动”，不宣称市场上绝对不存在行动。

只有当天的除权类事件可以描述为价格可能技术性调整；未来事件只显示日期和事实，不预测价格方向。所有内容由结构化数据确定性生成。

## 验收边界

- 当天和未来三天事件进入报告，第五个自然日不进入。
- 持仓和关注列表都能匹配，其他标的不泄漏。
- 四类目标行动有明确中文标签和日期。
- CSV 重复执行不重复写库，更高 revision 替代旧版本展示。
- 企微最多展示三条并保持 200 字以内，审计报告保留全部明细。
- 原有四时段报告、订阅、Outbox 与权限边界不回归。
