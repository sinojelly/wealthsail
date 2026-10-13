BEGIN;
-- Source governance can update research metadata, never instrument identity.
GRANT UPDATE (metadata) ON reference.instruments TO wealthsail_worker;
COMMIT;
