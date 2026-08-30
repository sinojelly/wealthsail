-- =============================================================================
-- WealthSail Migration 034: Owner-scoped tenant name management
-- =============================================================================
-- 租户名称属于租户治理配置，不是平台管理员的跨租户权限。API 仅允许当前
-- 活动租户的 OWNER 更新 name/updated_at 两列；HTTP 服务层仍会先做角色校验，
-- 本策略作为数据库侧的纵深防御。

DROP POLICY IF EXISTS domain_api_tenant_owner_update ON identity.tenants;
CREATE POLICY domain_api_tenant_owner_update
ON identity.tenants
FOR UPDATE TO wealthsail_api
USING (identity.has_tenant_role(id, ARRAY['OWNER']))
WITH CHECK (identity.has_tenant_role(id, ARRAY['OWNER']));

GRANT UPDATE (name, updated_at) ON identity.tenants TO wealthsail_api;
