-- ==============================================================================
-- WealthSail Migration 014: Outbox retry column grant for the Worker role
--
-- Forward-only remediation. Migration 013 granted the ``wealthsail_worker``
-- role column-level UPDATE on workflow.outbox but omitted ``available_at``,
-- which the exponential-backoff retry path writes
-- (``_cas_retry_or_dead`` in backend/jobs/outbox.py). On Supabase the worker
-- runs as ``wealthsail_worker``, so every failed delivery fell into
-- ``InsufficientPrivilegeError: permission denied for table outbox`` while
-- trying to schedule its own retry, and the row could never leave the
-- PENDING/SENDING loop.
-- ==============================================================================

GRANT UPDATE (
    status, attempt_count, last_error_class, locked_at, locked_by,
    external_message_id, sent_at, claim_token, lease_until, available_at
) ON workflow.outbox TO wealthsail_worker;
