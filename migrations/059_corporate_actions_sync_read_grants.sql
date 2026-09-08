BEGIN;

-- 059: the global 06:00 sync needs only the current holding projection and
-- instrument master/alias lookup.  It remains unable to write the fact table
-- directly; 058's SECURITY DEFINER function is its sole write path.

GRANT USAGE ON SCHEMA ledger, reference TO wealthsail_worker;
GRANT SELECT ON ledger.position_snapshots,
                reference.instruments,
                reference.instrument_aliases
    TO wealthsail_worker;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON market.corporate_actions
    FROM wealthsail_worker;

COMMIT;
