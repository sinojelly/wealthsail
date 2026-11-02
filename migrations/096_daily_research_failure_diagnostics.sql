-- Bounded, non-sensitive terminal diagnostics are inserted with the result.
-- Historical rows remain NULL; dispatch uniqueness and runtime grants do not change.
ALTER TABLE research.daily_research_results
 ADD COLUMN failure_stage TEXT,
 ADD COLUMN failure_reason_code TEXT,
 ADD COLUMN failure_rule_id TEXT,
 ADD COLUMN failure_item_index INTEGER,
 ADD CONSTRAINT daily_research_failure_stage_check CHECK (
   failure_stage IS NULL OR failure_stage IN (
     'MODEL_EXECUTION','SOURCE_VERIFICATION','REPORT_VALIDATION','PERSISTING')),
 ADD CONSTRAINT daily_research_failure_reason_code_check CHECK (
   failure_reason_code IS NULL OR failure_reason_code ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 ADD CONSTRAINT daily_research_failure_rule_id_check CHECK (
   failure_rule_id IS NULL OR failure_rule_id ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 ADD CONSTRAINT daily_research_failure_item_index_check CHECK (
   failure_item_index IS NULL OR failure_item_index BETWEEN 0 AND 9999),
 ADD CONSTRAINT daily_research_ready_diagnostics_check CHECK (
   status<>'READY' OR (
     failure_stage IS NULL AND failure_reason_code IS NULL
     AND failure_rule_id IS NULL AND failure_item_index IS NULL));

COMMENT ON COLUMN research.daily_research_results.failure_stage IS
 'Bounded failure stage; never an exception message, prompt, source quote or credential.';
COMMENT ON COLUMN research.daily_research_results.failure_reason_code IS
 'Application-allowlisted diagnostic code; nullable for historical results.';
COMMENT ON COLUMN research.daily_research_results.failure_rule_id IS
 'Application-allowlisted validation rule identifier; no raw validation detail.';
COMMENT ON COLUMN research.daily_research_results.failure_item_index IS
 'Zero-based offending item index, when available; no payload contents.';
