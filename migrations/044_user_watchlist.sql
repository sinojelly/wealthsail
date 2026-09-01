BEGIN;

CREATE TABLE identity.watchlist (
    user_id UUID NOT NULL REFERENCES identity.profiles(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL CHECK (symbol = upper(btrim(symbol)) AND length(symbol) BETWEEN 1 AND 32),
    asset_name TEXT NOT NULL CHECK (length(btrim(asset_name)) BETWEEN 1 AND 200),
    asset_type TEXT NOT NULL CHECK (asset_type IN ('STOCK', 'ETF', 'FUND')),
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);

ALTER TABLE identity.watchlist ENABLE ROW LEVEL SECURITY;

CREATE POLICY watchlist_self_select ON identity.watchlist
    FOR SELECT TO authenticated USING (user_id = auth.uid());

CREATE POLICY watchlist_self_insert ON identity.watchlist
    FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());

CREATE POLICY watchlist_self_delete ON identity.watchlist
    FOR DELETE TO authenticated USING (user_id = auth.uid());

CREATE POLICY watchlist_domain_api_select ON identity.watchlist
    FOR SELECT TO wealthsail_api
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

CREATE POLICY watchlist_domain_api_insert ON identity.watchlist
    FOR INSERT TO wealthsail_api
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

CREATE POLICY watchlist_domain_api_delete ON identity.watchlist
    FOR DELETE TO wealthsail_api
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

REVOKE ALL ON identity.watchlist FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, DELETE ON identity.watchlist TO wealthsail_api;

COMMIT;
