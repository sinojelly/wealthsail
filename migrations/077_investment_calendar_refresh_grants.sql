BEGIN;
-- A source refresh may insert calendar facts and revise open/closed sessions.
-- It cannot rewrite market identity; the API remains read-only.
GRANT INSERT (market,calendar_date,is_trading_day,timezone), UPDATE (is_trading_day)
ON reference.trading_calendars TO wealthsail_worker;
COMMIT;
