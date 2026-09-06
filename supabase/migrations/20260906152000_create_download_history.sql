create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null default '',
  approved boolean not null default false,
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;
revoke all on table public.profiles from anon, authenticated;
grant select on table public.profiles to authenticated;

create policy "Users read their own profile"
  on public.profiles for select
  to authenticated
  using ((select auth.uid()) = user_id);

create or replace function public.create_tunesaver_profile()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (user_id, email)
  values (new.id, coalesce(new.email, ''))
  on conflict (user_id) do nothing;
  return new;
end;
$$;

revoke execute on function public.create_tunesaver_profile() from public, anon, authenticated;

create trigger create_tunesaver_profile_after_signup
  after insert on auth.users
  for each row execute function public.create_tunesaver_profile();

insert into public.profiles (user_id, email)
select id, coalesce(email, '') from auth.users
on conflict (user_id) do nothing;

create table if not exists public.download_history (
  job_id text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  provider text not null check (provider in ('tiddl', 'beatport', 'qobuz')),
  source_url text not null default '',
  title text not null default '',
  artist text not null default '',
  resource_kind text not null default 'track',
  state text not null check (state in ('pending', 'running', 'done', 'failed')),
  track_count integer not null default 0 check (track_count >= 0),
  estimated_savings_cents integer not null default 0 check (estimated_savings_cents >= 0),
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  delivered_at timestamptz,
  updated_at timestamptz not null default now()
);

create index if not exists download_history_user_created_idx
  on public.download_history (user_id, created_at desc);

alter table public.download_history enable row level security;

revoke all on table public.download_history from anon, authenticated;
grant select, insert, update, delete on table public.download_history to authenticated;

create policy "Users read their own download history"
  on public.download_history for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users create their own download history"
  on public.download_history for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

create policy "Users update their own download history"
  on public.download_history for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "Users delete their own download history"
  on public.download_history for delete
  to authenticated
  using ((select auth.uid()) = user_id);
