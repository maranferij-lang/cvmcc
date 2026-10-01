-- CVMAX: users, usage log for rate limits, saved results.
-- Tables are locked down (RLS on, no policies). The app reaches data only through
-- SECURITY DEFINER functions that require the app token (stored here only as a hash).

create extension if not exists pgcrypto with schema extensions;

create schema if not exists cvmax_private;
revoke all on schema cvmax_private from public, anon, authenticated;

create table if not exists cvmax_private.app_config (
  id int primary key default 1 check (id = 1),
  token_hash text not null
);

create table if not exists public.cvmax_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  name text,
  program text,
  status text,
  background text,
  goal text,
  onboarded_at timestamptz,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

create table if not exists public.cvmax_usage (
  id bigint generated always as identity primary key,
  user_key text not null,
  kind text not null,
  created_at timestamptz not null default now()
);
create index if not exists cvmax_usage_user_kind_time on public.cvmax_usage (user_key, kind, created_at);
create index if not exists cvmax_usage_kind_time on public.cvmax_usage (kind, created_at);

create table if not exists public.cvmax_results (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.cvmax_users(id) on delete cascade,
  kind text not null,
  title text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists cvmax_results_user_time on public.cvmax_results (user_id, created_at desc);

alter table public.cvmax_users enable row level security;
alter table public.cvmax_usage enable row level security;
alter table public.cvmax_results enable row level security;
revoke all on public.cvmax_users, public.cvmax_usage, public.cvmax_results from anon, authenticated;

create or replace function cvmax_private.check_token(p_token text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if p_token is null or not exists (
    select 1 from cvmax_private.app_config
    where token_hash = encode(extensions.digest(p_token, 'sha256'), 'hex')
  ) then
    raise exception 'unauthorized' using errcode = '42501';
  end if;
end $$;

-- Start of the current "day" in Pacific time, when Gemini daily quotas reset.
create or replace function cvmax_private.day_start() returns timestamptz
language sql stable set search_path = '' as $$
  select (date_trunc('day', now() at time zone 'America/Los_Angeles')) at time zone 'America/Los_Angeles'
$$;

create or replace function public.cvmax_touch_user(p_token text, p_email text, p_name text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare u public.cvmax_users;
begin
  perform cvmax_private.check_token(p_token);
  insert into public.cvmax_users (email, name) values (lower(p_email), p_name)
  on conflict (email) do update set last_seen_at = now(), name = coalesce(excluded.name, public.cvmax_users.name)
  returning * into u;
  return to_jsonb(u);
end $$;

create or replace function public.cvmax_save_profile(
  p_token text, p_email text, p_program text, p_status text, p_background text, p_goal text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  update public.cvmax_users
     set program = p_program, status = p_status, background = p_background, goal = p_goal,
         onboarded_at = coalesce(onboarded_at, now())
   where email = lower(p_email);
end $$;

create or replace function public.cvmax_consume(
  p_token text, p_user_key text, p_kind text, p_user_limit int, p_global_limit int)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_start timestamptz := cvmax_private.day_start();
  v_user int;
  v_global int;
begin
  perform cvmax_private.check_token(p_token);
  perform pg_advisory_xact_lock(hashtext('cvmax_consume:' || p_kind));
  select count(*) into v_user from public.cvmax_usage
   where user_key = p_user_key and kind = p_kind and created_at >= v_start;
  select count(*) into v_global from public.cvmax_usage
   where kind = p_kind and created_at >= v_start;
  if p_user_limit is not null and v_user >= p_user_limit then
    return jsonb_build_object('allowed', false, 'reason', 'user', 'used', v_user, 'limit', p_user_limit);
  end if;
  if p_global_limit is not null and v_global >= p_global_limit then
    return jsonb_build_object('allowed', false, 'reason', 'global', 'used', v_global, 'limit', p_global_limit);
  end if;
  insert into public.cvmax_usage (user_key, kind) values (p_user_key, p_kind);
  return jsonb_build_object('allowed', true, 'reason', null, 'used', v_user + 1, 'limit', p_user_limit);
end $$;

create or replace function public.cvmax_save_result(
  p_token text, p_email text, p_kind text, p_title text, p_payload jsonb)
returns uuid language plpgsql security definer set search_path = '' as $$
declare v_user uuid; v_id uuid;
begin
  perform cvmax_private.check_token(p_token);
  select id into v_user from public.cvmax_users where email = lower(p_email);
  if v_user is null then raise exception 'unknown user'; end if;
  insert into public.cvmax_results (user_id, kind, title, payload)
  values (v_user, p_kind, left(p_title, 200), p_payload) returning id into v_id;
  return v_id;
end $$;

create or replace function public.cvmax_list_results(p_token text, p_email text, p_limit int default 50)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  return coalesce((
    select jsonb_agg(jsonb_build_object('id', r.id, 'kind', r.kind, 'title', r.title, 'created_at', r.created_at)
                     order by r.created_at desc)
      from (select r.* from public.cvmax_results r join public.cvmax_users u on u.id = r.user_id
             where u.email = lower(p_email) order by r.created_at desc limit least(p_limit, 200)) r
  ), '[]'::jsonb);
end $$;

create or replace function public.cvmax_get_result(p_token text, p_email text, p_id uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  return (select jsonb_build_object('id', r.id, 'kind', r.kind, 'title', r.title,
                                    'created_at', r.created_at, 'payload', r.payload)
            from public.cvmax_results r join public.cvmax_users u on u.id = r.user_id
           where u.email = lower(p_email) and r.id = p_id);
end $$;

-- Which suggested edits users accept. Used to see which kinds of edits are useful (no CV files, only edit texts).
create table if not exists public.cvmax_edit_feedback (
  id bigint generated always as identity primary key,
  user_key text not null,
  analysis_id text not null,
  source text not null,          -- 'analysis' or 'grill'
  target_role text,
  program text,
  section text,
  priority text,
  before_text text,
  after_text text,
  accepted boolean not null,
  created_at timestamptz not null default now(),
  unique (analysis_id, source, before_text, after_text)
);
create index if not exists cvmax_edit_feedback_time on public.cvmax_edit_feedback (created_at);
alter table public.cvmax_edit_feedback enable row level security;
revoke all on public.cvmax_edit_feedback from anon, authenticated;

create or replace function public.cvmax_log_edit_feedback(
  p_token text, p_user_key text, p_analysis_id text, p_target_role text, p_program text, p_items jsonb)
returns int language plpgsql security definer set search_path = '' as $$
declare v_count int := 0; item jsonb;
begin
  perform cvmax_private.check_token(p_token);
  for item in select * from jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) loop
    insert into public.cvmax_edit_feedback
      (user_key, analysis_id, source, target_role, program, section, priority, before_text, after_text, accepted)
    values (p_user_key, p_analysis_id, coalesce(item->>'source', 'analysis'), p_target_role, p_program,
            item->>'section', item->>'priority', coalesce(item->>'before', ''), coalesce(item->>'after', ''),
            coalesce((item->>'accepted')::boolean, false))
    on conflict (analysis_id, source, before_text, after_text)
    do update set accepted = excluded.accepted, created_at = now();
    v_count := v_count + 1;
  end loop;
  return v_count;
end $$;

-- Acceptance rate by section and priority over the last 30 days (read in the SQL editor).
create or replace view cvmax_private.edit_acceptance as
  select section, priority, count(*) as edits,
         round(100.0 * avg(case when accepted then 1 else 0 end), 1) as accepted_pct
    from public.cvmax_edit_feedback
   where created_at > now() - interval '30 days'
   group by section, priority
   order by edits desc;

create or replace function public.cvmax_delete_my_data(p_token text, p_email text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  -- Сьогоднішні лічильники лишаються: інакше «видалити дані» обнуляло б денні ліміти.
  delete from public.cvmax_usage where user_key = lower(p_email) and created_at < cvmax_private.day_start();
  delete from public.cvmax_edit_feedback where user_key = lower(p_email);
  delete from public.cvmax_users where email = lower(p_email);
end $$;

-- Only the app (anon key + app token) may call the functions.
do $$
declare f text;
begin
  foreach f in array array[
    'public.cvmax_touch_user(text, text, text)',
    'public.cvmax_save_profile(text, text, text, text, text, text)',
    'public.cvmax_consume(text, text, text, int, int)',
    'public.cvmax_save_result(text, text, text, text, jsonb)',
    'public.cvmax_list_results(text, text, int)',
    'public.cvmax_get_result(text, text, uuid)',
    'public.cvmax_delete_my_data(text, text)',
    'public.cvmax_log_edit_feedback(text, text, text, text, text, jsonb)'
  ] loop
    execute format('revoke all on function %s from public, authenticated', f);
    execute format('grant execute on function %s to anon', f);
  end loop;
end $$;
revoke all on function cvmax_private.check_token(text) from public;
revoke all on function cvmax_private.day_start() from public;

-- Hardening: the token table is closed too, and objects created later in public are not open to the API by default.
alter table cvmax_private.app_config enable row level security;
revoke all on cvmax_private.app_config from public, anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated, public;
