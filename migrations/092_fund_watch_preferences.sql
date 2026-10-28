BEGIN;

CREATE TABLE research.fund_watch_preferences (
 tenant_id UUID NOT NULL,user_id UUID NOT NULL,
 fund_market TEXT NOT NULL CHECK(fund_market ~ '^[A-Z][A-Z0-9_]{0,31}$'),
 fund_code TEXT CHECK(fund_code ~ '^[0-9]{6}$'),
 instrument_id UUID REFERENCES reference.instruments(id),fund_key TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,updated_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(tenant_id,user_id,fund_market,fund_key),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 CHECK((fund_code IS NOT NULL) <> (instrument_id IS NOT NULL)),
 CHECK(fund_key=COALESCE(fund_code,'instrument:'||instrument_id::text))
);

ALTER TABLE research.fund_watch_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.fund_watch_preferences FORCE ROW LEVEL SECURITY;
CREATE POLICY fund_watch_actor_read ON research.fund_watch_preferences FOR SELECT TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY fund_watch_actor_insert ON research.fund_watch_preferences FOR INSERT TO wealthsail_api
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
CREATE POLICY fund_watch_actor_update ON research.fund_watch_preferences FOR UPDATE TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id))
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
REVOKE ALL ON research.fund_watch_preferences FROM PUBLIC,authenticated;
GRANT SELECT,INSERT ON research.fund_watch_preferences TO wealthsail_api;
GRANT UPDATE(enabled,updated_at) ON research.fund_watch_preferences TO wealthsail_api;

COMMIT;
