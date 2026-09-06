BEGIN;

-- 056: API 角色回读投递凭证所需的 outbox 列级 SELECT。
--
-- 根因：f9dbf82 简化手动重生成后，OWNER/ADMIN 的
-- POST /v1/debug/reports/trigger/{session} 会在写库事务内调用
-- repository.get_report_outbox_id（SELECT ... FROM workflow.outbox，按
-- aggregate_id + topic + 绑定用户回溯投递行）。迁移 009 只给了 wealthsail_api
-- 表级 INSERT 和列级 UPDATE，从未授予任何 SELECT；worker（delivery_user_id
-- 为空的定时链路）不调用该函数，因此定时推送一直正常，仅手动重生成 500
-- （asyncpg.InsufficientPrivilegeError: permission denied for table outbox）。
--
-- 列集合与 authenticated 的只读视图对齐（不含 payload 内容列），外加
-- JOIN 需要的 channel_binding_id。仍不授予 UPDATE/DELETE 之外的任何能力，
-- payload/locked_by/last_error_class 等运维明细继续只对 worker 可见。

GRANT SELECT (
    id,
    tenant_id,
    topic,
    aggregate_type,
    aggregate_id,
    schema_version,
    available_at,
    status,
    attempt_count,
    idempotency_key,
    sent_at,
    created_at,
    channel_binding_id
) ON workflow.outbox TO wealthsail_api;

COMMIT;
