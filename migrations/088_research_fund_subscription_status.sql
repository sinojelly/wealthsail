BEGIN;

-- Public research needs subscription constraints without exposing the source
-- profile table or granting the worker a new write path.
CREATE OR REPLACE VIEW reference.fund_subscription_status AS
SELECT instrument_id, purchase_status,
       source_metadata->>'dailyPurchaseLimit' AS daily_purchase_limit,
       effective_from, effective_to, retrieved_at
  FROM reference.fund_profiles;

REVOKE ALL ON reference.fund_subscription_status FROM PUBLIC, authenticated;
GRANT SELECT ON reference.fund_subscription_status
    TO wealthsail_api, wealthsail_worker;

COMMENT ON VIEW reference.fund_subscription_status IS
    'Read-only public-research projection of verified fund subscription status and declared daily CNY limit.';

COMMIT;
