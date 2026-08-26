-- =============================================================================
-- WealthSail Migration 019: 多用户报告投递的 API 只读授权
--
-- Browser 的 authenticated 角色仍只能读取本人的 binding。Domain API 在生成
-- tenant 报告时需要枚举该 tenant 全部 ACTIVE binding，才能为 2–3 个用户分别
-- 创建 Outbox delivery。该策略只授予 API 角色，且 API 仍只有脱敏状态列的
-- SELECT 权限；外部路由密文继续仅供 Worker 读取。
-- =============================================================================

GRANT SELECT (
    id, tenant_id, user_id, channel_type, external_username, status,
    bound_at, revoked_at, last_used_at, version, created_at
) ON identity.channel_bindings TO wealthsail_api;

DROP POLICY IF EXISTS domain_api_channel_binding_tenant_select
    ON identity.channel_bindings;
CREATE POLICY domain_api_channel_binding_tenant_select
    ON identity.channel_bindings
    FOR SELECT TO wealthsail_api
    USING (identity.is_tenant_member(tenant_id));

COMMENT ON POLICY domain_api_channel_binding_tenant_select
    ON identity.channel_bindings IS
    'Domain API 可按当前 TenantContext 枚举所有用户的脱敏 binding 元数据，用于逐用户 Outbox 展开；浏览器仍受 self-select 限制。';
