BEGIN;

CREATE TABLE research.study_tracks (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,
 origin_key TEXT NOT NULL,title TEXT NOT NULL,scope JSONB NOT NULL DEFAULT '{}',
 enabled BOOLEAN NOT NULL DEFAULT TRUE,generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>0),
 created_at TIMESTAMPTZ NOT NULL,updated_at TIMESTAMPTZ NOT NULL,
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity.tenant_members(tenant_id,user_id),
 UNIQUE(tenant_id,user_id,id),UNIQUE(tenant_id,user_id,origin_key),
 CHECK(jsonb_typeof(scope)='object')
);
CREATE TABLE research.study_versions (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,track_id UUID NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),generation INTEGER NOT NULL CHECK(generation>0),
 snapshot JSONB NOT NULL CHECK(jsonb_typeof(snapshot)='object'),reason TEXT NOT NULL,
 command_key TEXT NOT NULL,command_hash TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL,
 FOREIGN KEY(tenant_id,user_id,track_id) REFERENCES research.study_tracks(tenant_id,user_id,id),
 UNIQUE(tenant_id,user_id,track_id,id),UNIQUE(tenant_id,user_id,track_id,version),
 UNIQUE(tenant_id,user_id,track_id,command_key)
);
CREATE TABLE research.study_checks (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,track_id UUID NOT NULL,
 version_id UUID NOT NULL,generation INTEGER NOT NULL CHECK(generation>0),evaluated_at TIMESTAMPTZ NOT NULL,
 input_hash TEXT NOT NULL,method_version TEXT NOT NULL,result JSONB NOT NULL CHECK(jsonb_typeof(result)='object'),
 FOREIGN KEY(tenant_id,user_id,track_id) REFERENCES research.study_tracks(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id,track_id,version_id) REFERENCES research.study_versions(tenant_id,user_id,track_id,id),
 UNIQUE(tenant_id,user_id,track_id,id),UNIQUE(tenant_id,user_id,track_id,version_id,generation,input_hash,method_version)
);
CREATE INDEX study_checks_latest ON research.study_checks(track_id,generation,evaluated_at DESC,id DESC);
CREATE TABLE research.study_notes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,track_id UUID NOT NULL,
 version_id UUID NOT NULL,generation INTEGER NOT NULL CHECK(generation>0),
 kind TEXT NOT NULL CHECK(kind IN ('LIFECYCLE','DECISION','REVIEW','ACK')),
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
 command_key TEXT NOT NULL,command_hash TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL,
 FOREIGN KEY(tenant_id,user_id,track_id) REFERENCES research.study_tracks(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id,track_id,version_id) REFERENCES research.study_versions(tenant_id,user_id,track_id,id),
 UNIQUE(tenant_id,user_id,track_id,command_key)
);
CREATE TABLE research.study_trade_links (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,user_id UUID NOT NULL,track_id UUID NOT NULL,
 version_id UUID NOT NULL,generation INTEGER NOT NULL CHECK(generation>0),fill_id UUID NOT NULL,
 linked_at TIMESTAMPTZ NOT NULL,reason TEXT NOT NULL,
 execution_time_precision TEXT NOT NULL DEFAULT 'DAY' CHECK(execution_time_precision='DAY'),
 timing_relation TEXT NOT NULL CHECK(timing_relation IN ('BEFORE_TRADE','POST_HOC','UNKNOWN')),
 fill JSONB NOT NULL CHECK(jsonb_typeof(fill)='object'),
 FOREIGN KEY(tenant_id,user_id,track_id) REFERENCES research.study_tracks(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id,track_id,version_id) REFERENCES research.study_versions(tenant_id,user_id,track_id,id),
 FOREIGN KEY(tenant_id,fill_id) REFERENCES ledger.trade_fills(tenant_id,id),
 UNIQUE(tenant_id,user_id,track_id,version_id,fill_id)
);

CREATE FUNCTION research.study_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'study history is append-only' USING ERRCODE='55000'; END $$;
CREATE FUNCTION research.study_check_generation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM research.study_tracks t
   JOIN identity.tenant_members m ON m.tenant_id=t.tenant_id AND m.user_id=t.user_id AND m.status='ACTIVE'
   WHERE t.tenant_id=NEW.tenant_id AND t.user_id=NEW.user_id AND t.id=NEW.track_id
   AND t.enabled AND t.generation=NEW.generation
   AND NEW.version_id=(SELECT v.id FROM research.study_versions v WHERE v.tenant_id=t.tenant_id
       AND v.user_id=t.user_id AND v.track_id=t.id ORDER BY v.version DESC LIMIT 1))
 THEN RAISE EXCEPTION 'study generation is no longer active' USING ERRCODE='40001'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER study_check_active BEFORE INSERT ON research.study_checks
 FOR EACH ROW EXECUTE FUNCTION research.study_check_generation();

CREATE FUNCTION research.study_trade_ownership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM ledger.trade_fills f
   JOIN identity.account_members owner ON owner.tenant_id=f.tenant_id AND owner.account_id=f.account_id
      AND owner.user_id=NEW.user_id AND owner.relationship IN ('BENEFICIAL_OWNER','JOINT_OWNER')
      AND owner.effective_from<=(NEW.linked_at AT TIME ZONE 'Asia/Shanghai')::date
      AND (owner.effective_to IS NULL OR owner.effective_to>=(NEW.linked_at AT TIME ZONE 'Asia/Shanghai')::date)
   JOIN research.study_versions v ON v.tenant_id=NEW.tenant_id AND v.user_id=NEW.user_id
      AND v.track_id=NEW.track_id AND v.id=NEW.version_id
   WHERE f.tenant_id=NEW.tenant_id AND f.id=NEW.fill_id AND f.status='CONFIRMED'
      AND f.ledger_event_id IS NOT NULL AND f.confirmed_at<=NEW.linked_at
      AND f.trade_date<=(NEW.linked_at AT TIME ZONE 'Asia/Shanghai')::date
      AND (NULLIF(v.snapshot->'scope'->>'instrumentId','') IS NULL
        OR f.instrument_id::text=v.snapshot->'scope'->>'instrumentId')
      AND (NULLIF(v.snapshot->'scope'->>'fundCode','') IS NULL
        OR NULLIF(v.snapshot->'scope'->>'instrumentId','') IS NOT NULL)
      AND (NULLIF(v.snapshot->'scope'->>'indexId','') IS NULL OR EXISTS(
        SELECT 1 FROM reference.fund_index_links l JOIN reference.research_indices idx ON idx.id=l.index_id
        WHERE l.instrument_id=f.instrument_id AND l.index_id=v.snapshot->'scope'->>'indexId'
          AND l.verified_at<=NEW.linked_at AND idx.verified_at<=NEW.linked_at
          AND l.effective_from<=(NEW.linked_at AT TIME ZONE 'Asia/Shanghai')::date
          AND (l.effective_to IS NULL OR l.effective_to>=(NEW.linked_at AT TIME ZONE 'Asia/Shanghai')::date)))
      AND (NULLIF(v.snapshot->'scope'->>'directionId','') IS NULL OR EXISTS(
        SELECT 1 FROM reference.fund_index_links l JOIN reference.research_indices idx ON idx.id=l.index_id
        JOIN reference.direction_index_links d ON d.index_id=l.index_id
        WHERE l.instrument_id=f.instrument_id AND d.direction_id=v.snapshot->'scope'->>'directionId'
          AND l.verified_at<=NEW.linked_at AND idx.verified_at<=NEW.linked_at
          AND l.effective_from<=(NEW.linked_at AT TIME ZONE 'Asia/Shanghai')::date
          AND (l.effective_to IS NULL OR l.effective_to>=(NEW.linked_at AT TIME ZONE 'Asia/Shanghai')::date)))
      AND NEW.fill->>'fillId'=f.id::text AND NEW.fill->>'instrumentId'=f.instrument_id::text
      AND NEW.fill->>'accountId'=f.account_id::text AND NEW.fill->>'status'='CONFIRMED'
      AND NEW.fill->>'side'=f.side AND NEW.fill->>'tradeDate'=f.trade_date::text
      AND (NEW.fill->>'quantity')::numeric=f.quantity
      AND (NEW.fill->>'unitPrice')::numeric=f.unit_price AND (NEW.fill->>'fee')::numeric=f.fee)
 THEN RAISE EXCEPTION 'confirmed MINE trade required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER study_trade_mine BEFORE INSERT ON research.study_trade_links
 FOR EACH ROW EXECUTE FUNCTION research.study_trade_ownership();

DO $$ DECLARE tab TEXT; BEGIN
 FOREACH tab IN ARRAY ARRAY['study_tracks','study_versions','study_checks','study_notes','study_trade_links'] LOOP
   EXECUTE format('ALTER TABLE research.%I ENABLE ROW LEVEL SECURITY',tab);
   EXECUTE format('ALTER TABLE research.%I FORCE ROW LEVEL SECURITY',tab);
   EXECUTE format('CREATE POLICY study_actor_read ON research.%I FOR SELECT TO wealthsail_api USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id))',tab);
   EXECUTE format('CREATE POLICY study_actor_insert ON research.%I FOR INSERT TO wealthsail_api WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id))',tab);
   IF tab<>'study_tracks' THEN
     EXECUTE format('CREATE TRIGGER study_immutable BEFORE UPDATE OR DELETE ON research.%I FOR EACH ROW EXECUTE FUNCTION research.study_history_immutable()',tab);
   END IF;
 END LOOP;
END $$;
CREATE POLICY study_actor_toggle ON research.study_tracks FOR UPDATE TO wealthsail_api
 USING(user_id=auth.uid() AND identity.is_tenant_member(tenant_id))
 WITH CHECK(user_id=auth.uid() AND identity.is_tenant_member(tenant_id));
REVOKE ALL ON research.study_tracks,research.study_versions,research.study_checks,research.study_notes,research.study_trade_links FROM PUBLIC,authenticated;
GRANT SELECT ON research.study_tracks,research.study_versions,research.study_checks,research.study_notes,research.study_trade_links TO wealthsail_api,wealthsail_worker;
GRANT INSERT ON research.study_tracks,research.study_versions,research.study_checks,research.study_notes,research.study_trade_links TO wealthsail_api;
GRANT INSERT ON research.study_checks TO wealthsail_worker;
GRANT UPDATE(enabled,generation,updated_at) ON research.study_tracks TO wealthsail_api;
COMMIT;
