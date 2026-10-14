"""TuneSaver database adapter. SQLite remains available for local development."""
from contextlib import contextmanager
import os
import re
import sqlite3

import psycopg

INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)


class Row(dict):
    """Keep named and positional reads used by TuneSaver's existing SQL layer."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def enabled():
    return bool(os.environ.get('TUNESAVER_PG_HOST'))


def connect():
    return psycopg.connect(host=os.environ['TUNESAVER_PG_HOST'],
                          port=int(os.environ.get('TUNESAVER_PG_PORT', '5432')),
                          dbname=os.environ['TUNESAVER_PG_DATABASE'],
                          user=os.environ['TUNESAVER_PG_USER'],
                          password=os.environ['TUNESAVER_PG_PASSWORD'],
                          connect_timeout=10, row_factory=row_factory)


def translate(query, parameterized=False):
    # Translate placeholders outside SQL strings/quoted identifiers only.
    # The app's statements contain no dollar-quoted bodies or SQL comments.
    parts = re.split(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")", query)
    if parameterized:
        parts = [p.replace('%', '%%') for p in parts]
        parts[::2] = [p.replace('?', '%s') for p in parts[::2]]
    query = ''.join(parts)
    if re.match(r'^\s*insert\s+or\s+ignore\b', query, re.I):
        query = re.sub(r'insert\s+or\s+ignore', 'insert', query, count=1, flags=re.I).rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    return query


class Connection:
    dialect = 'postgres'

    def __init__(self, raw):
        self.raw = raw

    def execute(self, query, params=None):
        if query.strip().lower() == 'begin immediate':
            # Every transaction already holds the compatibility writer lock.
            return self.raw.execute('SELECT 1')
        return self.raw.execute(translate(query, params is not None), tuple(int(v) if isinstance(v, bool) else v for v in params) if params is not None else None)

    def executemany(self, query, params):
        cursor = self.raw.cursor()
        cursor.executemany(translate(query, True), params)
        return cursor


@contextmanager
def postgres_db():
    with connect() as conn:
        # Preserve SQLite's serialized write transactions during the migration.
        # Later versions can replace this with narrower row locks after load tests.
        conn.execute('SELECT pg_advisory_xact_lock(741927310)')
        yield Connection(conn)


def check_schema():
    with postgres_db() as db:
        row = db.execute('SELECT version FROM _tunesaver_schema').fetchone()
        if not row or row['version'] != 1:
            raise RuntimeError('TuneSaver PostgreSQL migrations must be applied before startup')
