-- =============================================================================
-- WealthSail Migration 028: portfolio import item UPDATE policy
--
-- Tiantian confirmation locks every staged POSITION_SNAPSHOT with
-- SELECT ... FOR UPDATE before it writes ledger effects. PostgreSQL applies
-- UPDATE RLS to those row locks. Migration 021 granted the two decision
-- columns but did not create an UPDATE policy, so the API saw an empty result
-- even though the draft contained valid items.
--
-- Keep the capability narrow:
--   * only OWNER / ADMIN / EDITOR may lock or update rows;
--   * only rows whose parent import is still DRAFT are visible to UPDATE;
--   * the existing column-level grant remains unchanged, so the API can update
--     only user_holding_started_on and updated_at;
--   * the existing trigger remains the final state-transition guard.
-- =============================================================================

DROP POLICY IF EXISTS domain_api_update ON ledger.portfolio_import_items;
CREATE POLICY domain_api_update
ON ledger.portfolio_import_items
FOR UPDATE TO wealthsail_api
USING (
    identity.has_tenant_role(
        tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']
    )
    AND EXISTS (
        SELECT 1
          FROM ledger.portfolio_import_batches batch
         WHERE batch.tenant_id = portfolio_import_items.tenant_id
           AND batch.id = portfolio_import_items.batch_id
           AND batch.status = 'DRAFT'
    )
)
WITH CHECK (
    identity.has_tenant_role(
        tenant_id, ARRAY['OWNER', 'ADMIN', 'EDITOR']
    )
    AND EXISTS (
        SELECT 1
          FROM ledger.portfolio_import_batches batch
         WHERE batch.tenant_id = portfolio_import_items.tenant_id
           AND batch.id = portfolio_import_items.batch_id
           AND batch.status = 'DRAFT'
    )
);
