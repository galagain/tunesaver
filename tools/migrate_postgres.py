#!/usr/bin/env python3
"""Import a stopped TuneSaver SQLite snapshot into an empty PostgreSQL database.

The whole schema/data import is transactional. Source SQLite remains read-only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import psycopg
from psycopg import sql
import database

SCHEMA = Path(__file__).resolve().parents[1] / 'migrations/001_postgres.sql'
if not SCHEMA.exists():
    SCHEMA = Path('/app/migrations/001_postgres.sql')


def digest(rows):
    hashes = []
    for row in rows:
        payload = json.dumps(list(row), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        hashes.append(hashlib.sha256(payload.encode()).digest())
    return len(hashes), hashlib.sha256(b''.join(sorted(hashes))).hexdigest()


def grant(conn, runtime):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', runtime):
        raise ValueError('Invalid runtime role')
    role = sql.Identifier(runtime)
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(role))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}').format(role))
    conn.execute(sql.SQL('REVOKE INSERT,UPDATE,DELETE ON _tunesaver_schema FROM {}').format(role))
    conn.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(role))


def migrate(source, runtime, report):
    original = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    original.execute('BEGIN')
    if original.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise RuntimeError('SQLite integrity check failed')
    if original.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError('SQLite contains broken foreign keys')
    names = [row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    pending = set(names)
    order = []
    while pending:
        ready = sorted(name for name in pending if all(row[2] not in pending for row in original.execute('PRAGMA foreign_key_list("'+name+'")')))
        if not ready:
            raise RuntimeError('Cyclic foreign keys require a dedicated migration')
        order.extend(ready)
        pending.difference_update(ready)
    summary = {}
    with database.connect() as conn:
        if conn.execute("SELECT 1 FROM pg_tables WHERE schemaname='public'").fetchone():
            raise RuntimeError('Target database is not empty; import refused')
        conn.execute(SCHEMA.read_text())
        for name in order:
            columns = [row[1] for row in original.execute('PRAGMA table_info("'+name+'")')]
            if name in ('playlist_items', 'tag_items'):
                columns.insert(0, 'rowid')
            table = sql.Identifier(name)
            cols = sql.SQL(',').join(map(sql.Identifier, columns))
            select_sql = 'SELECT '+','.join('"'+col+'"' for col in columns)+' FROM "'+name+'"'
            with conn.cursor().copy(sql.SQL('COPY {} ({}) FROM STDIN').format(table, cols)) as copy:
                for row in original.execute(select_sql):
                    copy.write_row(row)
            expected = digest(original.execute(select_sql))
            with conn.cursor(row_factory=psycopg.rows.tuple_row) as cursor:
                cursor.execute(sql.SQL('SELECT {} FROM {}').format(cols, table))
                actual = digest(cursor)
            if expected != actual:
                raise RuntimeError('Content verification failed for '+name)
            summary[name] = {'rows': actual[0], 'sha256': actual[1]}
            if name in ('playlist_items', 'tag_items'):
                conn.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s,'rowid'), COALESCE(max(rowid),1), max(rowid) IS NOT NULL) FROM {}").format(table), (name,))
            print(name+': '+str(actual[0])+' rows verified', flush=True)
        grant(conn, runtime)
    original.close()
    if report:
        descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(summary, output, indent=2)
    print('Import committed; every source row verified by SHA-256')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--runtime-role', required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    migrate(args.source, args.runtime_role, args.report)
