-- =============================================================================
-- WealthSail Migration 035: personal-tenant job privilege hardening
-- =============================================================================
-- Migration 009 granted workflow.jobs INSERT to wealthsail_api so the early
-- Domain API could manage schedules. Migration 033 moved new personal-tenant
-- schedule creation into the SECURITY DEFINER accept_invitation function, but
-- deliberately did not rewrite that already-applied migration. Remove the now
-- unnecessary effective capability and its dormant RLS policy so an accidental
-- future table grant cannot reopen direct schedule creation.
--
-- The protected function continues to insert as its owner. Existing jobs and
-- tenants are unchanged; authenticated/browser and Worker permissions are not
-- widened.

REVOKE INSERT ON workflow.jobs FROM PUBLIC, authenticated;
REVOKE INSERT ON workflow.jobs FROM wealthsail_api;
DROP POLICY IF EXISTS domain_api_insert ON workflow.jobs;
