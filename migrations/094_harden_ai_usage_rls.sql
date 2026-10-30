-- Replace indirect auth.uid() membership checks with
-- safe JWT identity checks for AI usage tasks.

DROP POLICY IF EXISTS ai_usage_task_read
    ON research.ai_usage_tasks;

CREATE POLICY ai_usage_task_read
ON research.ai_usage_tasks
FOR SELECT TO wealthsail_api
USING (
    (
        tenant_id::text = research.safe_jwt_claim('tenant_id')
        AND (
            user_id = research.safe_jwt_uid()
            OR (
                research.safe_jwt_uid() IS NOT NULL
                AND EXISTS (
                    SELECT 1
                    FROM identity.tenant_members tm
                    WHERE tm.tenant_id = research.ai_usage_tasks.tenant_id
                      AND tm.user_id = research.safe_jwt_uid()
                      AND tm.status = 'ACTIVE'
                      AND tm.role = ANY (ARRAY['OWNER', 'ADMIN'])
                )
            )
        )
    )
    OR (
        tenant_id IS NULL
        AND EXISTS (
            SELECT 1
            FROM identity.platform_roles p
            WHERE p.user_id = research.safe_jwt_uid()
              AND p.role = 'PLATFORM_ADMIN'
              AND p.status = 'ACTIVE'
        )
    )
);

DROP POLICY IF EXISTS ai_usage_task_insert
    ON research.ai_usage_tasks;

CREATE POLICY ai_usage_task_insert
ON research.ai_usage_tasks
FOR INSERT TO wealthsail_api
WITH CHECK (
    tenant_id::text = research.safe_jwt_claim('tenant_id')
    AND user_id = research.safe_jwt_uid()
    AND research.safe_jwt_uid() IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM identity.tenant_members tm
        WHERE tm.tenant_id = research.ai_usage_tasks.tenant_id
          AND tm.user_id = research.safe_jwt_uid()
          AND tm.status = 'ACTIVE'
    )
);
