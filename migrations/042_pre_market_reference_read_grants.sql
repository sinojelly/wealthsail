BEGIN;

-- PRE_MARKET reads global reference facts through the API transaction role.
-- Browser roles remain unable to query these tables directly, and neither
-- runtime role receives any write capability.
REVOKE ALL ON market.corporate_actions FROM PUBLIC, authenticated, wealthsail_worker;
REVOKE ALL ON reference.instrument_cutoff_rules FROM PUBLIC, authenticated, wealthsail_worker;

GRANT SELECT ON market.corporate_actions TO wealthsail_api;
GRANT SELECT ON reference.instrument_cutoff_rules TO wealthsail_api;

COMMIT;
