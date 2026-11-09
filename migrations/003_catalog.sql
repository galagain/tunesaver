BEGIN;
CREATE TABLE IF NOT EXISTS global_catalog (id text PRIMARY KEY, title text NOT NULL, artist text NOT NULL, url text NOT NULL, provider text NOT NULL, added_at text NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_devices (token_hash text PRIMARY KEY, user_id text NOT NULL, label text NOT NULL, revoked integer NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS catalog_scanned (job_id text PRIMARY KEY, stamp text NOT NULL);
GRANT SELECT, INSERT, UPDATE, DELETE ON global_catalog, catalog_devices, catalog_scanned TO tunesaver_app;
COMMIT;
