"""Run TuneSaver HTTP regression tests against a disposable PostgreSQL database."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests'), str(ROOT)]
import psycopg
from psycopg import sql
import test_webapp
import database

name = os.environ['TUNESAVER_PG_DATABASE']
if not name.endswith('_test'):
    raise RuntimeError('Refusing to reset a database not ending in _test')


def owner():
    return psycopg.connect(host=os.environ['TUNESAVER_PG_HOST'], dbname=name,
                          user=name+'_owner', password=os.environ['TUNESAVER_TEST_OWNER_PASSWORD'])


with owner() as conn:
    conn.execute('DROP SCHEMA public CASCADE')
    conn.execute('CREATE SCHEMA public')
    conn.execute((ROOT/'migrations/001_postgres.sql').read_text())
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(name+'_app')))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(name+'_app')))
    conn.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(name+'_app')))

original = test_webapp.WebAppHTTPTests.asyncSetUp
async def setup(self):
    with owner() as conn:
        tables = [row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> '_tunesaver_schema'")]
        conn.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(sql.SQL(',').join(map(sql.Identifier,tables))))
    await original(self)
test_webapp.WebAppHTTPTests.asyncSetUp = setup
suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_webapp.WebAppHTTPTests)
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
