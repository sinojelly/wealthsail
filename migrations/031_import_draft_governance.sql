-- =============================================================================
-- 031 Import draft governance
--
-- Preserve why a Tiantian draft became terminal and link a replacement DRAFT
-- to the cancelled preview it supersedes.  These fields are audit metadata;
-- they do not add another economic batch state.
-- =============================================================================

ALTER TABLE ledger.portfolio_import_batches
    ADD COLUMN terminal_reason TEXT,
    ADD COLUMN supersedes_batch_id UUID;

-- Historical cancellation commands predate explicit reasons.  Treat them as
-- user cancellations so the new invariant can be enabled without rewriting
-- any economic state or terminal command identity.
--
-- The runtime guard intentionally rejects UPDATEs to terminal batches.  The
-- migration runner executes this file and its ledger INSERT in one PostgreSQL
-- transaction, so bypass only that named user trigger for this one-time
-- metadata backfill.  ENABLE is part of the same transaction: any failure
-- before commit rolls the trigger state and the backfill back together.
ALTER TABLE ledger.portfolio_import_batches
    DISABLE TRIGGER protect_portfolio_import_batches_trigger;

UPDATE ledger.portfolio_import_batches
   SET terminal_reason = 'USER_CANCELLED'
 WHERE source_type = 'TIANTIAN_FUND_ZIP'
   AND status = 'CANCELLED'
   AND terminal_reason IS NULL;

ALTER TABLE ledger.portfolio_import_batches
    ENABLE TRIGGER protect_portfolio_import_batches_trigger;

ALTER TABLE ledger.portfolio_import_batches
    ADD CONSTRAINT portfolio_import_batches_terminal_reason_value_check CHECK (
        terminal_reason IS NULL OR terminal_reason IN (
            'USER_CANCELLED', 'SUPERSEDED_BY_REPARSE', 'EXPIRED'
        )
    ),
    ADD CONSTRAINT portfolio_import_batches_terminal_reason_check CHECK (
        source_type <> 'TIANTIAN_FUND_ZIP'
        OR (status = 'CANCELLED' AND terminal_reason IS NOT NULL)
        OR (status <> 'CANCELLED' AND terminal_reason IS NULL)
    ),
    ADD CONSTRAINT portfolio_import_batches_not_self_superseding_check
        CHECK (supersedes_batch_id IS NULL OR supersedes_batch_id <> id),
    ADD CONSTRAINT portfolio_import_batches_tiantian_supersession_check CHECK (
        supersedes_batch_id IS NULL OR source_type = 'TIANTIAN_FUND_ZIP'
    ),
    ADD CONSTRAINT fk_portfolio_import_batches_supersedes
        FOREIGN KEY (tenant_id, supersedes_batch_id)
        REFERENCES ledger.portfolio_import_batches (tenant_id, id)
        ON DELETE RESTRICT;

CREATE INDEX idx_import_batches_supersedes
    ON ledger.portfolio_import_batches (tenant_id, supersedes_batch_id)
    WHERE supersedes_batch_id IS NOT NULL;

-- Table-level INSERT already covers supersedes_batch_id on the replacement
-- row.  UPDATE is needed for the atomic cancellation of the old DRAFT; keep it
-- column-scoped instead of widening the role to arbitrary batch updates.
GRANT UPDATE (terminal_reason, supersedes_batch_id)
    ON ledger.portfolio_import_batches TO wealthsail_api;
