BEGIN;

ALTER TABLE identity.user_preferences
    ADD COLUMN pre_market_report_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN intraday_report_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN fund_1430_report_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN post_market_report_enabled BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN identity.user_preferences.pre_market_report_enabled IS
    'Whether PRE_MARKET report deliveries are queued for this user.';
COMMENT ON COLUMN identity.user_preferences.intraday_report_enabled IS
    'Whether INTRADAY report deliveries are queued for this user.';
COMMENT ON COLUMN identity.user_preferences.fund_1430_report_enabled IS
    'Whether FUND_1430 report deliveries are queued for this user.';
COMMENT ON COLUMN identity.user_preferences.post_market_report_enabled IS
    'Whether POST_MARKET report deliveries are queued for this user.';

COMMIT;
