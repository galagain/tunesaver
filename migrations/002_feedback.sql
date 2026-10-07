-- Run with the database owner (or SET ROLE from the maintenance account).
BEGIN;
SET LOCAL ROLE tunesaver_owner;
CREATE TABLE IF NOT EXISTS feedback_threads (
    id text primary key, user_id text not null references users(id) on delete cascade,
    kind text not null, title text not null, status text not null default 'new',
    context text not null, screenshot text not null default '',
    created_at text not null, updated_at text not null,
    revision integer not null default 1, user_read integer not null default 1,
    admin_read integer not null default 0
);
CREATE TABLE IF NOT EXISTS feedback_messages (
    id text primary key, thread_id text not null references feedback_threads(id) on delete cascade,
    author text not null, body text not null, created_at text not null,
    position integer not null, unique(thread_id, position)
);
CREATE INDEX IF NOT EXISTS feedback_owner_date ON feedback_threads(user_id, updated_at);
GRANT SELECT, INSERT, UPDATE, DELETE ON feedback_threads, feedback_messages TO tunesaver_app;
COMMIT;
