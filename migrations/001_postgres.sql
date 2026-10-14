-- TuneSaver schema. Epoch timestamps use double precision to preserve SQLite precision.
CREATE TABLE dig_cache (id text primary key, payload text not null, expires double precision not null);

CREATE TABLE users (
              id text primary key,
              email text not null unique,
              password_salt text not null,
              password_hash text not null,
              email_verified integer not null default 0,
              approved integer not null default 0,
              blocked integer not null default 0,
              created_at text not null,
              last_sign_in_at text
            );

CREATE TABLE dig_feedback (
                    user_id text not null references users(id) on delete cascade, item_id text not null,
                    state text not null, artist text not null, primary key(user_id, item_id)
                );

CREATE TABLE dig_sessions (
                    id text primary key, user_id text not null references users(id) on delete cascade,
                    name text not null, config text not null, seeds text not null,
                    state text not null default 'pending', results text not null default '[]',
                    generated integer not null default 0, target integer not null default 20,
                    progress text not null default '', warnings text not null default '[]', created_at text not null
                , revision integer not null default 0);

CREATE TABLE download_history (
              job_id text primary key,
              user_id text not null references users(id) on delete cascade,
              provider text not null,
              source_url text not null default '',
              title text not null default '',
              artist text not null default '',
              resource_kind text not null default 'track',
              state text not null,
              track_count integer not null default 0,
              estimated_savings_cents integer not null default 0,
              created_at text not null,
              completed_at text,
              delivered_at text
            );

CREATE TABLE email_codes (
              code_hash text primary key,
              user_id text not null references users(id) on delete cascade,
              purpose text not null check (purpose in ('verify_email', 'password_reset')),
              expires_at double precision not null,
              used_at text,
              created_at text not null
            );

CREATE TABLE library_orders (
              user_id text not null references users(id) on delete cascade,
              scope text not null,
              track_ids text not null,
              primary key (user_id, scope)
            );

CREATE TABLE library_removed (
              user_id text not null references users(id) on delete cascade,
              job_id text not null,
              primary key (user_id, job_id)
            );

CREATE TABLE playlist_folders (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              name text not null,
              collapsed integer not null default 0
            );

CREATE TABLE playlists (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              name text not null,
              created_at text not null
            );

CREATE TABLE sessions (
              token_hash text primary key,
              user_id text not null references users(id) on delete cascade,
              kind text not null check (kind in ('access', 'refresh')),
              expires_at double precision not null,
              created_at text not null
            );

CREATE TABLE stream_tickets (
              token_hash text primary key,
              user_id text not null references users(id) on delete cascade,
              job_id text not null,
              expires_at double precision not null
            );

CREATE TABLE tags (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              name text not null,
              color text not null,
              created_at text not null
            );

CREATE TABLE dig_live (
                    user_id text primary key references users(id) on delete cascade,
                    session_id text not null references dig_sessions(id) on delete cascade
                );

CREATE TABLE mixer_analysis (
            job_id text primary key references download_history(job_id) on delete cascade,
            status text not null default 'pending', signature text not null default '',
            result text, error text not null default '', updated double precision not null default 0
          , summary text);

CREATE TABLE mixer_settings (
            job_id text primary key references download_history(job_id) on delete cascade,
            user_id text not null references users(id) on delete cascade,
            settings text not null default '{}'
          );

CREATE TABLE playlist_folder_items (
              playlist_id text primary key references playlists(id) on delete cascade,
              folder_id text not null references playlist_folders(id) on delete cascade
            );

CREATE TABLE playlist_imports (
              id text primary key,
              user_id text not null references users(id) on delete cascade,
              playlist_id text not null references playlists(id) on delete cascade,
              source_url text not null,
              state text not null default 'pending',
              tracks text not null default '[]',
              processed integer not null default 0,
              queued integer not null default 0,
              failures text not null default '[]',
              error text not null default '',
              created_at text not null
            );

CREATE TABLE playlist_items (
 rowid bigint GENERATED BY DEFAULT AS IDENTITY UNIQUE,
              playlist_id text not null references playlists(id) on delete cascade,
              job_id text not null,
              created_at text not null,
              primary key (playlist_id, job_id)
            );

CREATE TABLE tag_items (
 rowid bigint GENERATED BY DEFAULT AS IDENTITY UNIQUE,
              tag_id text not null references tags(id) on delete cascade,
              job_id text not null,
              created_at text not null,
              primary key (tag_id, job_id)
            );

CREATE INDEX sessions_user_kind_idx
              on sessions (user_id, kind);

CREATE INDEX email_codes_user_purpose_idx
              on email_codes (user_id, purpose, expires_at desc);

CREATE INDEX download_history_user_created_idx
              on download_history (user_id, created_at desc);

CREATE INDEX download_history_state_created_idx
              on download_history (state, created_at desc);

CREATE INDEX playlists_user_created_idx
              on playlists (user_id, created_at desc);

CREATE INDEX tags_user_created_idx
              on tags (user_id, created_at desc);

CREATE INDEX dig_sessions_user on dig_sessions(user_id, created_at);

CREATE INDEX stream_tickets_expiry_idx on stream_tickets (expires_at);

CREATE INDEX stream_tickets_job_idx on stream_tickets (job_id, user_id);

CREATE TABLE _tunesaver_schema(version integer PRIMARY KEY);
INSERT INTO _tunesaver_schema VALUES (1);

CREATE TABLE feedback_threads (
    id text primary key, user_id text not null references users(id) on delete cascade,
    kind text not null, title text not null, status text not null default 'new',
    context text not null, screenshot text not null default '',
    created_at text not null, updated_at text not null,
    revision integer not null default 1, user_read integer not null default 1,
    admin_read integer not null default 0
);
CREATE TABLE feedback_messages (
    id text primary key, thread_id text not null references feedback_threads(id) on delete cascade,
    author text not null, body text not null, created_at text not null,
    position integer not null, unique(thread_id, position)
);
CREATE INDEX feedback_owner_date ON feedback_threads(user_id, updated_at);
