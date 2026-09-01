# 研报订阅配置与受控单次调试触发设计

## 1. 目标与边界

为每个 WealthSail 用户提供四个研报推送开关：`PRE_MARKET`、`INTRADAY`、
`FUND_1430`、`POST_MARKET`。四项默认全部开启，用户可在“账号安全”页面独立修改。
开关只影响企业微信 Outbox 投递，不影响报告生成、版本链、模型记录、证据链或研报审计页面。

增加 `POST /api/v1/debug/reports/trigger/{session}`，供当前租户 `OWNER/ADMIN`
单次生成指定时段报告并为一个目标用户排队推送。接口不创建、不修改、不租赁
`workflow.jobs`，也不伪造定时任务运行记录。

本功能不恢复 `AUTH_DEV_MODE`。该变量继续在所有环境被启动校验拒绝，避免重新引入任意密码
登录漏洞。调试接口始终要求有效登录会话以及当前租户 `OWNER/ADMIN` 角色。

## 2. 数据模型与迁移

新增 migration `043_user_report_subscriptions.sql`，在已有的
`identity.user_preferences` 增加四个非空布尔列：

- `pre_market_report_enabled BOOLEAN NOT NULL DEFAULT TRUE`
- `intraday_report_enabled BOOLEAN NOT NULL DEFAULT TRUE`
- `fund_1430_report_enabled BOOLEAN NOT NULL DEFAULT TRUE`
- `post_market_report_enabled BOOLEAN NOT NULL DEFAULT TRUE`

已有用户由列默认值安全回填，新注册用户沿用默认值，不需要修改所有创建
`identity.user_preferences` 的 SQL。表已有强制 RLS，且只有 `wealthsail_api` 拥有
`SELECT/INSERT/UPDATE`，`PUBLIC` 与 `authenticated` 无权直接读写；migration 043 不扩大该权限。

迁移 runner 的 readback 增加以下检查：四列存在、均为 `NOT NULL`、默认值为 `TRUE`，并继续验证
浏览器与 PUBLIC 不具备表权限。迁移是 forward-only，不修改既有 migration checksum。

## 3. 用户订阅 API

`GET /api/v1/auth/me` 的响应增加：

```json
{
  "reportSubscriptions": {
    "PRE_MARKET": true,
    "INTRADAY": true,
    "FUND_1430": true,
    "POST_MARKET": true
  }
}
```

新增 `PATCH /api/v1/auth/report-subscriptions`。请求体允许只提交需要改变的一个或多个时段，
但至少包含一项；服务端只接受上述四个固定字段，并返回保存后的完整四项状态。所有活动租户角色
`OWNER/ADMIN/EDITOR/VIEWER` 都可以修改本人的全局用户偏好，不能通过请求体指定其他用户。

更新在 `wealthsail_api` 事务内执行，并写入 `REPORT_SUBSCRIPTIONS_UPDATED` 审计事件。审计只记录
变化后的四项布尔值，不包含渠道路由值、令牌或其他敏感信息。

订阅是用户级全局偏好：同一用户加入多个租户时，四个开关在其所有租户的企业微信推送中一致。
这一语义与现有 `identity.user_preferences` 的默认租户和资产范围设置保持一致。

## 4. 报告生成与投递过滤

报告生成路径保持不变。`reporting.reports`、`reporting.report_items`、证据和审计仍按租户、
交易日与时段正常写入。

仅在 `insert_report_outbox_deliveries` 查询活动企业微信绑定时，左连接
`identity.user_preferences` 并按当前 `session` 选择对应布尔列：

- 偏好行为 `TRUE`：为该绑定创建 Outbox；
- 偏好行为 `FALSE`：跳过该绑定；
- 历史异常数据没有 preference 行：使用 `COALESCE(..., TRUE)`，保持默认开启和向后兼容。

过滤发生在每个 `tenant_id + user_id + binding` 上，因此同租户不同用户可获得不同投递结果。
Outbox 幂等键、Markdown payload、报告详情链接和 Worker 消费逻辑不变。报告审计中的
`deliveryCount` 记录实际入队数量，全部用户关闭该时段时显示 `NO_ACTIVE_BINDING` 的现有值；
本次不改变既有审计枚举，避免前端兼容性变化。

## 5. 受控调试接口

接口：

```text
POST /api/v1/debug/reports/trigger/{session}?user_id=<optional UUID>
```

规则：

1. `session` 仅允许四个标准时段，否则返回 422 标准错误信封；
2. 调用者必须是当前租户 `OWNER/ADMIN`，`EDITOR/VIEWER` 返回 403；
3. `user_id` 缺省为调用者本人；指定时必须是当前租户的活动成员，否则返回 404；
4. 调用跳过报告服务的 60 秒重复生成窗口，强制创建一个新报告版本；
5. Outbox 只针对目标用户的活动企微绑定创建，并继续遵守该用户对应时段的订阅开关；
6. 不访问或写入 `workflow.jobs`、`workflow.job_runs`；
7. 写入 `DEBUG_REPORT_TRIGGERED` 审计，记录 session、目标用户、报告和 Outbox 结果。

响应：

```json
{
  "reportId": "uuid",
  "outboxId": "uuid-or-null"
}
```

目标用户没有活动绑定或已关闭该时段时，报告仍成功生成，`outboxId` 返回 `null`。这样既遵守
“开关只控制推送”，又让开发者能区分“生成失败”和“没有产生投递”。接口只负责持久化 PENDING
Outbox；实际发送仍由现有 Worker 消费，避免 HTTP 请求直接持有企业微信凭据或绕过重试机制。

实现上，报告服务增加内部参数 `force` 与 `delivery_user_id`。普通 API 和 Worker 使用默认值，
行为不变；调试服务使用 `force=True` 和目标用户。Outbox repository 增加目标用户过滤，并提供按
`report_id + user_id` 回读 Outbox ID 的租户内查询。调试 API 通过该查询返回实际入队结果。

## 6. 前端交互

在 `frontend/src/features/settings/components/ReportSubscriptionCard.tsx` 新增独立卡片，展示四个
明确命名的开关及“只影响企业微信推送，不影响报告生成与研报审计”的说明。

页面从 `/auth/me.reportSubscriptions` 初始化状态。每次切换只提交对应字段；提交期间禁用该开关，
成功后以服务端返回的完整状态刷新 React Query 缓存，失败时恢复原状态并显示错误。四种租户角色都
展示且可操作该卡片。调试接口不在普通用户设置页提供按钮，避免把开发者能力混入产品界面。

## 7. 文件范围

新增：

- `migrations/043_user_report_subscriptions.sql`
- `backend/debug/__init__.py`
- `backend/debug/api.py`
- `backend/debug/service.py`
- `frontend/src/features/settings/components/ReportSubscriptionCard.tsx`
- 后端、迁移与前端对应的新测试文件

修改：

- `backend/identity/api.py`
- `backend/identity/domain_service.py`
- `backend/reports/repository.py`
- `backend/reports/service.py`
- `backend/main.py`
- `scripts/migrate.py`
- `frontend/src/api/types.ts`
- `frontend/src/api/identity.ts`
- `frontend/src/api/hooks.ts`
- `frontend/src/features/settings/SettingsPage.tsx`
- `frontend/src/features/settings/settings.test.tsx`
- `docs/10.WealthSail-MVP前后端实现与系统部署规范.md`
- `docs/11.数据库升级方法.md`
- `docs/20.WealthSail累计收益与标准报告任务设计及运维.md`

`backend/config.py` 不修改，因为最终方案不增加调试开关，且必须继续拒绝 `AUTH_DEV_MODE=true`。

## 8. 测试与验收

后端覆盖：

- migration 043 列、默认值、最小权限和 runner readback；
- 四时段偏好分别关闭时只过滤对应 Outbox，缺 preference 行默认开启；
- 指定目标用户时只给该用户入队；
- 偏好更新只允许本人且所有租户角色可用；
- 调试接口四时段校验、OWNER/ADMIN 允许、EDITOR/VIEWER 拒绝、跨租户用户拒绝；
- 调试触发强制新版本、无 job/job_run 写入、返回 reportId/outboxId；
- 订阅关闭或未绑定时 outboxId 为 null，报告仍存在。

前端覆盖：

- 四个开关按服务端状态渲染且默认数据为全开；
- VIEWER 也能看到并修改本人开关；
- 单个开关只提交对应字段；
- 保存失败恢复状态并展示错误；
- 设置页不暴露调试触发按钮。

提交前运行后端全量 Pytest、Ruff、前端 Vitest、ESLint、TypeScript/Vite build 和
`git diff --check`。本功能需要执行 migration 043，但不需要修改现有计划任务或重建历史报告。
