BEGIN;
CREATE TABLE market.macro_release_evidence (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), code TEXT NOT NULL REFERENCES reference.macro_indicator_catalog(field_code),
 reference_period DATE NOT NULL CHECK(EXTRACT(DAY FROM reference_period)=1), value NUMERIC(28,10) NOT NULL,
 unit TEXT NOT NULL, published_at TIMESTAMPTZ, source_uri TEXT NOT NULL, publisher TEXT NOT NULL,
 source_key TEXT NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL, content_hash TEXT NOT NULL,
 canonical_hash TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
 CHECK(value::text NOT IN ('NaN','Infinity','-Infinity')),
 UNIQUE(code,reference_period,source_uri,revision)
);
CREATE TABLE market.macro_release_schedules (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), code TEXT NOT NULL REFERENCES reference.macro_indicator_catalog(field_code),
 reference_period DATE NOT NULL CHECK(EXTRACT(DAY FROM reference_period)=1), scheduled_at TIMESTAMPTZ NOT NULL,
 source_uri TEXT NOT NULL, publisher TEXT NOT NULL, source_key TEXT NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL,
 content_hash TEXT NOT NULL, canonical_hash TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
 UNIQUE(code,reference_period,source_uri,revision)
);
CREATE TABLE market.macro_events (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), event_key TEXT NOT NULL, title TEXT NOT NULL,
 category TEXT NOT NULL, source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_FACT'),
 source_uri TEXT NOT NULL, publisher TEXT NOT NULL, source_key TEXT NOT NULL, published_at TIMESTAMPTZ,
 retrieved_at TIMESTAMPTZ NOT NULL, summary TEXT NOT NULL CHECK(length(summary)<=300),
 evidence JSONB NOT NULL, content_hash TEXT NOT NULL, canonical_hash TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0), UNIQUE(event_key,revision)
);
CREATE TABLE market.macro_event_direction_links (
 event_id UUID NOT NULL REFERENCES market.macro_events(id),
 direction_id TEXT NOT NULL REFERENCES reference.research_directions(id), PRIMARY KEY(event_id,direction_id)
);
CREATE TABLE market.macro_evidence_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), run_id UUID NOT NULL, job_key TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('CLAIMED','FINISHED','CANCELLED')),
 started_at TIMESTAMPTZ NOT NULL, recorded_at TIMESTAMPTZ NOT NULL, lease_until TIMESTAMPTZ,
 trigger TEXT NOT NULL, release_keys JSONB NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(release_keys)='array'), result JSONB NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(result)='object'),
 UNIQUE(run_id,phase)
);
CREATE INDEX macro_release_evidence_latest ON market.macro_release_evidence(code,reference_period,source_uri,revision DESC);
CREATE INDEX macro_release_schedules_due ON market.macro_release_schedules(scheduled_at,retrieved_at DESC);
CREATE INDEX macro_events_latest ON market.macro_events(event_key,revision DESC);
CREATE INDEX macro_evidence_attempts_latest ON market.macro_evidence_attempts(job_key,recorded_at DESC);
REVOKE ALL ON market.macro_release_evidence,market.macro_release_schedules,market.macro_events,
 market.macro_event_direction_links,market.macro_evidence_attempts FROM PUBLIC,authenticated;
GRANT SELECT ON market.macro_release_evidence,market.macro_release_schedules,market.macro_events,
 market.macro_event_direction_links,market.macro_evidence_attempts TO wealthsail_api,wealthsail_worker;
GRANT INSERT ON market.macro_release_evidence,market.macro_release_schedules,market.macro_events,
 market.macro_event_direction_links,market.macro_evidence_attempts TO wealthsail_worker;
COMMIT;
