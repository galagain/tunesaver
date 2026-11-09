BEGIN;
CREATE TABLE IF NOT EXISTS device_logins (token_hash text PRIMARY KEY, code text UNIQUE NOT NULL, expires double precision NOT NULL, user_id text, providers text, attempts integer NOT NULL DEFAULT 0);
GRANT SELECT,INSERT,UPDATE,DELETE ON device_logins TO tunesaver_app;
COMMIT;
