BEGIN;
ALTER TABLE reference.research_indices ADD COLUMN publish_hour INTEGER CHECK(publish_hour BETWEEN 0 AND 23);
COMMENT ON COLUMN reference.research_indices.publish_hour IS 'Verified Asia/Shanghai availability hour; NULL disables complete scoring.';
COMMIT;
