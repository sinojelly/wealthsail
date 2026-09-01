# WealthSail 关注列表与双阶段盘前简报设计

> 日期：2026-09-01  
> 状态：已确认  
> 实现基线：`ce262dc`

## 1. 目标与范围

本次按依赖顺序交付两个可独立验证的功能：

1. 用户可以维护自己的关注标的，资产看板展示“我的关注”，报告上下文能够读取关注列表；
2. `PRE_MARKET` 在交易日拆成 08:30 宏观简报和 08:35 个性化简报，两篇报告分别落库、分别投递、分别出现在研报审计中。

不新增公开报告订阅类型，不改变现有四时段 API 枚举，不增加 AI 调用，也不让浏览器直接写数据库。

## 2. 关注列表

### 2.1 数据模型

migration 044 新建 `identity.watchlist`：

- `user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE`；
- `symbol TEXT NOT NULL`；
- `asset_name TEXT NOT NULL`；
- `asset_type TEXT NOT NULL`，仅允许 `STOCK / ETF / FUND`；
- `added_at TIMESTAMPTZ NOT NULL DEFAULT now()`；
- 主键 `(user_id, symbol)`，同时满足唯一约束要求。

关注列表属于用户而不是租户，因此同一用户切换租户时看到相同列表。symbol 在服务端去除空白并转为大写；资产名称和类型必须由 `reference.instruments` 与有效 TICKER alias 回读，不能信任浏览器传入的显示名称。

表启用 RLS。`authenticated` 的读取与写入只允许 `user_id = auth.uid()`；领域 API 使用受限 `wealthsail_api` 角色，并通过事务中的 `app.user_id` 强制本人边界。`PUBLIC` 不具备任何权限。迁移 readback 必须检查表结构、主键、RLS、API 角色权限以及浏览器/PUBLIC 越权边界。

### 2.2 API

新增独立 `backend/watchlist` 模块，并挂载在 `/api/v1/watchlist`：

- `GET /api/v1/watchlist`：按 `added_at DESC, symbol` 返回本人关注列表；
- `POST /api/v1/watchlist`：请求 `{ "symbol": "600036" }`，服务端解析主数据后幂等插入；重复关注返回现有记录，不创建重复行；
- `DELETE /api/v1/watchlist/{symbol}`：删除本人记录；不存在时返回规范化 404。

所有活动租户角色均可管理自己的关注列表。添加和删除分别写入 `WATCHLIST_ITEM_ADDED`、`WATCHLIST_ITEM_REMOVED` 审计，审计详情只保留规范化 symbol、资产类型，不保存原始搜索输入。

### 2.3 前端

资产看板新增“我的关注”面板：

- 展示名称、代码、类型和关注时间；
- 使用现有 `/portfolio/search` 搜索权威标的；
- 搜索结果提供“关注”，已关注项提供“取消关注”；
- 成功后刷新 watchlist 查询；失败时保留原列表并展示标准错误；
- 无关注时展示引导文案，不与“无持仓”状态混淆。

前端只发送 symbol，不能覆盖服务端解析出的 `asset_name` 或 `asset_type`。

### 2.4 报告上下文

关注列表完成时先在报告上下文中增加 `watchlist` 字段，但不改变现有报告文案和调度。读取按报告受众用户 ID 进行，为第二阶段提供稳定接口。

## 3. 双阶段盘前简报

### 3.1 报告身份

migration 045 扩展 `reporting.reports`：

- `report_variant TEXT NOT NULL DEFAULT 'STANDARD'`；
- `audience_user_id UUID NULL REFERENCES identity.profiles(id)`，并通过
  `(tenant_id, audience_user_id)` 复合外键约束为当前租户成员。

允许的变体包括 `STANDARD`、`PRE_MARKET_MACRO`、`PRE_MARKET_PERSONALIZED`。公开 `report_session` 仍为 `PRE_MARKET`，因此现有订阅开关、过滤参数和 API 枚举保持兼容。

报告版本唯一性从 `(tenant_id, trade_date, report_session, version)` 调整为受众感知的唯一索引：

`(tenant_id, trade_date, report_session, report_variant, COALESCE(audience_user_id, zero_uuid), version)`。

宏观报告的 `audience_user_id` 为 NULL，租户所有活动成员可读；个性化报告必须指定受众，只允许本人通过报告列表、详情、条目和版本历史读取。迁移同步收紧 reporting 相关 RLS，防止通过 Supabase Data API 跨成员读取个性化内容。

研报 API 新增向后兼容字段 `variant` 和 `audienceUserId`。时间线标题分别为“盘前简报·宏观”和“盘前简报·个性化”，两篇报告独立列出，不互相 supersede。

### 3.2 08:30 宏观阶段

新增按 `trade_date` 唯一的 append-only 共享缓存 `reporting.shared_pre_market_briefs`。首次执行者在 advisory transaction lock 内基于权威结构化数据生成并写入：

- 隔夜市场速览：美股主要指数、A50；
- 今日重点关注：经济数据、全球事件。

同一天后续租户只读取缓存，不重复调用模型或外部生成流程。当前没有可靠字段时必须写“暂无”，不得推断或伪造。每个启用 PRE_MARKET job 的租户仍创建一条 `PRE_MARKET_MACRO` 报告，以便租户审计和链接跳转；Outbox 向该租户所有活动、已绑定且开启 PRE_MARKET 订阅的用户展开。

### 3.3 08:35 个性化阶段

Worker 枚举租户活动成员，并使用 `AssetScope(kind="MINE", owner_user_id=user_id)` 读取本人持仓，同时读取本人 watchlist：

- 两者均为空：跳过个性化报告和 Outbox；
- 任一非空：创建一条受众为该用户的 `PRE_MARKET_PERSONALIZED` 报告；
- Outbox 只定向该用户，并继续遵守 PRE_MARKET 订阅开关和活动企微绑定。

内容全部由已有结构化数据确定性生成：

1. 持仓今日影响；
2. 关注标的动态；
3. 今日操作提醒。

关注标的动态使用主数据、最新市场观测和当日公司行动；没有新鲜数据时明确显示“暂无”。该阶段不新增 AI 调用。

### 3.4 调度与幂等

`workflow.jobs` 继续只保留一个 `report_session='PRE_MARKET'` 任务，不破坏 migration 040 的每租户每时段唯一约束和“四任务完整性”监控。

Scheduler 使用两个内部 dispatch key：

- `PRE_MARKET_MACRO`：08:30；
- `PRE_MARKET_PERSONALIZED`：08:35。

Runner 将二者映射到同一个 PRE_MARKET job，但 job key 包含内部变体和受众：宏观阶段每租户每天一次；个性化阶段每租户、每用户、每天一次。租约、失败分类、恢复和重试沿用现有 `workflow.job_runs` 状态机。旧的 `PRE_MARKET` 手动调试入口默认只触发宏观阶段并保持单个 `reportId/outboxId` 响应；可选 `phase=PERSONALIZED` 用于定向验证个性化阶段，其他时段契约不变。

## 4. 错误处理与可观测性

- 单个用户个性化报告失败不能阻塞同租户其他用户；
- 共享缓存首次生成失败时不写半成品，后续 catch-up tick 可重试；
- 无持仓无关注是正常 SKIPPED 结果，不记作失败；
- 无绑定或关闭订阅时报告仍生成，Outbox 数量为 0；
- 审计详情记录 variant、audienceUserId、deliveryCount、cacheHit 和 sharedBriefId；
- Worker 日志使用 tenant、variant、受众的非敏感 UUID，不输出关注原始搜索词或数据库凭据。

## 5. 测试与验收

### 关注列表

- migration 结构、RLS、角色权限和 readback；
- API 认证、本人隔离、主数据校验、重复关注、删除 404 和审计；
- OWNER/ADMIN/EDITOR/VIEWER 均可维护本人列表；
- 前端加载、搜索、关注、取消、空状态和失败回滚；
- 报告上下文能按指定用户读取 watchlist。

### 双阶段盘前简报

- 08:30/08:35 时区和交易日调度；
- 共享缓存并发下每天只生成一次；
- 两个变体拥有独立版本链且都出现在当前用户审计页；
- 个性化 RLS 与 API 不泄露其他成员报告；
- 有持仓、仅关注、两者都有、两者都无四种分支；
- PRE_MARKET 订阅关闭时两阶段都不推送但仍保留应生成的报告；
- Worker 租约恢复、Outbox 定向、零投递和失败隔离；
- 完整后端测试、前端测试、lint 和生产构建通过。

## 6. 发布顺序

1. 完成并发布关注列表代码；执行 migration 044，重启 API，重建前端；
2. 验证关注增删和报告上下文后，再发布双阶段盘前代码；
3. 执行 migration 045，重启 API 与 Worker，重建前端；
4. 不回填第五条 workflow job；确认每个活动租户仍恰好四条标准报告任务；
5. 在交易日观察 08:30 与 08:35 自然调度、两篇审计报告及对应 Outbox。
