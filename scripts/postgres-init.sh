#!/usr/bin/env bash
set -Eeuo pipefail
# psql quotes the role password as a SQL literal. Never echo credentials.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$TUNESAVER_PG_PASSWORD" <<'SQL'
CREATE ROLE tunesaver_app LOGIN PASSWORD :'app_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
for migration in /migrations/*.sql; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --file "$migration"
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
GRANT USAGE ON SCHEMA public TO tunesaver_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tunesaver_app;
REVOKE INSERT, UPDATE, DELETE ON _tunesaver_schema FROM tunesaver_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tunesaver_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tunesaver_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tunesaver_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tunesaver_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO tunesaver_app;
SQL
