-- CVMAX: users, usage log for rate limits, saved results.
-- Tables are locked down (RLS on, no policies). The app reaches data only through
-- SECURITY DEFINER functions that require the app token (stored here only as a hash).

create extension if not exists pgcrypto with schema extensions;

create schema if not exists cvmax_private;
revoke all on schema cvmax_private from public, anon, authenticated;

create table if not exists cvmax_private.app_config (
  id int primary key default 1,
  token_hash text not null
);
-- Token scopes: 'app' (the application, full access) and 'learning' (CI: only the learning and job functions).
alter table cvmax_private.app_config add column if not exists scope text not null default 'app';
alter table cvmax_private.app_config drop constraint if exists app_config_id_check;
create unique index if not exists app_config_scope_uniq on cvmax_private.app_config (scope);

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
  perform cvmax_private.check_token(p_token, 'app');
end $$;

-- A 'learning' scope token passes only where check_token(p_token, 'learning') is called;
-- an 'app' token passes everywhere.
create or replace function cvmax_private.check_token(p_token text, p_scope text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if p_token is null or not exists (
    select 1 from cvmax_private.app_config
    where token_hash = encode(extensions.digest(p_token, 'sha256'), 'hex')
      and (scope = p_scope or scope = 'app')
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
    select jsonb_agg(jsonb_build_object('id', r.id, 'kind', r.kind, 'title', r.title, 'created_at', r.created_at,
                                       'analysis_id', r.payload->>'analysis_id')
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
  -- Today's counters stay: otherwise "delete data" would reset the daily limits.
  delete from public.cvmax_usage where user_key = lower(p_email) and created_at < cvmax_private.day_start();
  delete from public.cvmax_edit_feedback where user_key = lower(p_email);
  delete from public.cvmax_events where user_key = lower(p_email);
  delete from public.cvmax_training_samples where user_key = lower(p_email);
  delete from public.cvmax_credit_use where user_key = lower(p_email);
  -- Purchases are accounting records: kept, but no longer linked to the person.
  update public.cvmax_purchases set user_key = 'deleted:' || md5(user_key) where user_key = lower(p_email);
  delete from public.cvmax_waitlist where email = lower(p_email);
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
revoke all on function cvmax_private.check_token(text, text) from public;
revoke all on function cvmax_private.day_start() from public;

-- Hardening: the token table is closed too, and objects created later in public are not open to the API by default.
alter table cvmax_private.app_config enable row level security;
revoke all on cvmax_private.app_config from public, anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated, public;

-- Anonymous feedback from the site: no email or user id, only the page, a thumbs rating and an optional message.
create table if not exists public.cvmax_feedback (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  page text not null,
  rating smallint check (rating between 0 and 1),
  message text check (char_length(message) <= 2000)
);
alter table public.cvmax_feedback enable row level security;
revoke all on public.cvmax_feedback from anon, authenticated;

create or replace function public.cvmax_save_feedback(p_token text, p_page text, p_rating int, p_message text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  insert into public.cvmax_feedback (page, rating, message)
  values (left(coalesce(p_page, ''), 40), p_rating, nullif(left(coalesce(p_message, ''), 2000), ''));
end $$;
revoke all on function public.cvmax_save_feedback(text, text, int, text) from public, authenticated;
grant execute on function public.cvmax_save_feedback(text, text, int, text) to anon;


-- Launch list from the landing page (getcvmax.com). Written by a Cloudflare Pages Function that holds the app token.
create table if not exists public.cvmax_waitlist (
  id bigint generated always as identity primary key,
  email text not null unique check (char_length(email) <= 254),
  plan text not null default 'full' check (plan in ('scan', 'full', 'hunt')),
  source text check (char_length(source) <= 60),
  country text check (char_length(country) <= 2),
  created_at timestamptz not null default now()
);
alter table public.cvmax_waitlist enable row level security;
revoke all on public.cvmax_waitlist from anon, authenticated;

create or replace function public.cvmax_join_waitlist(
  p_token text, p_email text, p_plan text, p_source text, p_country text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v_email text := lower(trim(p_email));
begin
  perform cvmax_private.check_token(p_token);
  if v_email is null or char_length(v_email) > 254 or v_email !~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]{2,}$' then
    raise exception 'bad email' using errcode = '22023';
  end if;
  insert into public.cvmax_waitlist (email, plan, source, country)
  values (v_email,
          case when p_plan in ('scan', 'full', 'hunt') then p_plan else 'full' end,
          nullif(left(coalesce(p_source, ''), 60), ''),
          nullif(left(upper(coalesce(p_country, '')), 2), ''))
  on conflict (email) do update set plan = excluded.plan;
  return jsonb_build_object('ok', true);
end $$;
revoke all on function public.cvmax_join_waitlist(text, text, text, text, text) from public, authenticated;
grant execute on function public.cvmax_join_waitlist(text, text, text, text, text) to anon;

-- Launch list by day and source (read in the SQL editor).
create or replace view cvmax_private.waitlist_by_source as
  select date_trunc('day', created_at)::date as day, coalesce(source, '(direct)') as source, plan, count(*) as signups
    from public.cvmax_waitlist group by 1, 2, 3 order by 1 desc, 4 desc;


-- ===== Learning loops and job links (see docs/plan-learning-jobs.md) =====

-- Events: only ids, ratings and decisions. Never CV text. Deleted together with the user's data.
create table if not exists public.cvmax_events (
  id bigint generated always as identity primary key,
  user_key text not null,
  kind text not null check (char_length(kind) <= 40),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz default now()
);
create index if not exists cvmax_events_kind_time on public.cvmax_events (kind, created_at);
create index if not exists cvmax_events_analysis on public.cvmax_events ((payload->>'analysis_id'));
alter table public.cvmax_events enable row level security;
revoke all on public.cvmax_events from anon, authenticated;

-- Daily snapshot of vacancies from public sources (no personal data).
create table if not exists public.cvmax_vacancies (
  id bigint generated always as identity primary key,
  url_hash text not null,
  url text not null,
  source text not null,
  title text not null,
  company text,
  location text,
  region text not null default '',
  role_family text not null default '',
  posted_at date,
  salary text,
  snippet text check (char_length(snippet) <= 600),
  remote boolean,
  query text,
  first_seen_at timestamptz default now(),
  last_seen_at timestamptz default now(),
  -- One posting can belong to several roles and regions: each pair is counted separately.
  unique (url_hash, role_family, region)
);
create index if not exists cvmax_vacancies_family_region_seen
  on public.cvmax_vacancies (role_family, region, last_seen_at);
alter table public.cvmax_vacancies enable row level security;
revoke all on public.cvmax_vacancies from anon, authenticated;

-- Skills most requested in vacancies, per program and region (rewritten by the market script).
create table if not exists public.cvmax_skill_demand (
  id bigint generated always as identity primary key,
  program text not null,
  region text not null,
  skill text not null,
  postings int not null,
  total_postings int not null,
  window_days int not null,
  computed_at timestamptz default now()
);
create index if not exists cvmax_skill_demand_program_region on public.cvmax_skill_demand (program, region);
alter table public.cvmax_skill_demand enable row level security;
revoke all on public.cvmax_skill_demand from anon, authenticated;

create or replace function public.cvmax_log_event(p_token text, p_user_key text, p_kind text, p_payload jsonb)
returns void language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  if p_kind is null or char_length(p_kind) = 0 or char_length(p_kind) > 40 then
    raise exception 'bad event kind' using errcode = '22023';
  end if;
  if p_user_key is null or char_length(p_user_key) = 0 then
    raise exception 'bad user key' using errcode = '22023';
  end if;
  if length(coalesce(p_payload, '{}'::jsonb)::text) > 4000 then
    raise exception 'payload too big' using errcode = '22023';
  end if;
  insert into public.cvmax_events (user_key, kind, payload)
  values (p_user_key, p_kind, coalesce(p_payload, '{}'::jsonb));
end $$;

-- Success/failure counts per prompt variant and program. Shared by the RPC and the private view.
-- Reward: rating 1/0 wins; without a rating, accepted edits >= 50% (with >= 2 edits) is success, below is failure.
-- An outcome 'yes' (interview) adds 2 successes, even if the analysis has no other signal.
create or replace function cvmax_private.variant_stats_rows(p_task text, p_days int)
returns table (variant text, program text, successes bigint, failures bigint)
language sql stable set search_path = '' as $$
  with done as (
    select distinct on (e.payload->>'analysis_id')
           e.payload->>'analysis_id' as analysis_id,
           e.payload->>'variant' as variant,
           coalesce(e.payload->>'program', '') as program
      from public.cvmax_events e
     where e.kind = 'analysis_done'
       and e.created_at > now() - make_interval(days => greatest(p_days, 1))
       and coalesce(e.payload->>'task', 'analysis') = p_task
       and e.payload->>'analysis_id' is not null
       and coalesce(e.payload->>'variant', '') <> ''
     order by e.payload->>'analysis_id', e.created_at desc, e.id desc
  ),
  rated as (
    select distinct on (e.payload->>'analysis_id')
           e.payload->>'analysis_id' as analysis_id,
           case when jsonb_typeof(e.payload->'rating') = 'number' and e.payload->>'rating' in ('0', '1') then (e.payload->>'rating')::int end as rating
      from public.cvmax_events e
     where e.kind = 'analysis_rated' and e.payload->>'analysis_id' is not null
     order by e.payload->>'analysis_id', e.created_at desc, e.id desc
  ),
  edits as (
    select distinct on (e.payload->>'analysis_id')
           e.payload->>'analysis_id' as analysis_id,
           case when e.payload->>'accepted' ~ '^[0-9]{1,6}$' then (e.payload->>'accepted')::int end as accepted,
           case when e.payload->>'total' ~ '^[0-9]{1,6}$' then (e.payload->>'total')::int end as total
      from public.cvmax_events e
     where e.kind = 'edits_decided' and e.payload->>'analysis_id' is not null
     order by e.payload->>'analysis_id', e.created_at desc, e.id desc
  ),
  outcomes as (
    select distinct on (e.payload->>'analysis_id')
           e.payload->>'analysis_id' as analysis_id, (e.payload->>'answer' = 'yes') as got_yes
      from public.cvmax_events e
     where e.kind = 'outcome' and e.payload->>'analysis_id' is not null
     order by e.payload->>'analysis_id', e.created_at desc, e.id desc
  ),
  scored as (
    select d.variant, d.program,
           (r.rating = 1 or (r.rating is null and x.total >= 2 and x.accepted * 2 >= x.total)) as is_success,
           (r.rating = 0 or (r.rating is null and x.total >= 2 and x.accepted * 2 < x.total)) as is_failure,
           coalesce(o.got_yes, false) as got_yes
      from done d
      left join rated r on r.analysis_id = d.analysis_id
      left join edits x on x.analysis_id = d.analysis_id
      left join outcomes o on o.analysis_id = d.analysis_id
  )
  select s.variant, s.program,
         sum(coalesce(s.is_success, false)::int + 2 * s.got_yes::int)::bigint as successes,
         sum(coalesce(s.is_failure, false)::int)::bigint as failures
    from scored s
   group by s.variant, s.program
  having sum(coalesce(s.is_success, false)::int + 2 * s.got_yes::int + coalesce(s.is_failure, false)::int) > 0
$$;

create or replace function public.cvmax_variant_stats(p_token text, p_task text, p_days int default 90)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token, 'learning');
  return coalesce((
    select jsonb_agg(jsonb_build_object('variant', v.variant, 'program', v.program,
                                        'successes', v.successes, 'failures', v.failures)
                     order by v.variant, v.program)
      from cvmax_private.variant_stats_rows(coalesce(p_task, 'analysis'), p_days) v
  ), '[]'::jsonb);
end $$;

-- Removes emails, URLs and phone numbers and truncates the text to 300 characters in the database, before export.
create or replace function cvmax_private.redact_text(p_text text)
returns text language sql immutable set search_path = '' as $$
  select left(
    regexp_replace(
      regexp_replace(
        regexp_replace(coalesce(p_text, ''), '[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}', '[email]', 'g'),
        '(https?://|www\.)[^[:space:]]+', '[url]', 'g'),
      '\+?[0-9][0-9 ()./-]{7,}[0-9]', '[phone]', 'g'),
    300)
$$;
revoke all on function cvmax_private.redact_text(text) from public, anon, authenticated;

-- Free section text -> a word from the vocabulary (otherwise 'other'): no arbitrary text goes into the export.
create or replace function cvmax_private.canonical_section(p text)
returns text language plpgsql immutable set search_path = '' as $$
declare
  w text;
  v_low text := lower(coalesce(p, ''));
begin
  foreach w in array array['summary', 'experience', 'education', 'projects', 'skills', 'leadership',
                           'activities', 'volunteering', 'awards', 'languages', 'certifications',
                           'interests', 'personal'] loop
    if position(w in v_low) > 0 then
      return w;
    end if;
  end loop;
  return 'other';
end $$;
revoke all on function cvmax_private.canonical_section(text) from public, anon, authenticated;

-- Anonymous export for the weekly reflection script: no user_key, no emails.
create or replace function public.cvmax_learning_export(p_token text, p_days int default 30)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_since timestamptz := now() - make_interval(days => greatest(p_days, 1));
begin
  perform cvmax_private.check_token(p_token, 'learning');
  return jsonb_build_object(
    'edit_feedback', coalesce((
      select jsonb_agg(jsonb_build_object(
               'program', t.program, 'section', cvmax_private.canonical_section(t.section),
               'priority', t.priority, 'analysis_id', md5(t.analysis_id),
               'before_text', cvmax_private.redact_text(t.before_text),
               'after_text', cvmax_private.redact_text(t.after_text),
               'accepted', t.accepted, 'created_at', t.created_at) order by t.created_at desc)
        from (select * from public.cvmax_edit_feedback where created_at >= v_since
               order by created_at desc limit 2000) t
    ), '[]'::jsonb),
    -- Counters only: how many different users and analyses stand behind a field's signals (without keys).
    'breadth', coalesce((
      select jsonb_object_agg(coalesce(program, 'other'),
               jsonb_build_object('users', users, 'analyses', analyses))
        from (select program, count(distinct user_key) as users, count(distinct analysis_id) as analyses
                from public.cvmax_edit_feedback where created_at >= v_since group by program) b
    ), '{}'::jsonb),
    'feedback', coalesce((
      select jsonb_agg(jsonb_build_object(
               'page', t.page, 'rating', t.rating, 'created_at', t.created_at)
               order by t.created_at desc)
        from (select * from public.cvmax_feedback where created_at >= v_since
               order by created_at desc limit 500) t
    ), '[]'::jsonb),
    'events', coalesce((
      select jsonb_agg(jsonb_build_object(
               'kind', t.kind, 'payload', t.payload, 'created_at', t.created_at) order by t.created_at desc)
        from (
          (select * from public.cvmax_events
            where created_at >= v_since
              and kind in ('analysis_done', 'analysis_rated', 'edits_decided', 'outcome', 'rescan')
            order by created_at desc limit 5000)
          union all
          (select * from public.cvmax_events
            where created_at >= v_since
              and kind in ('grill_turn', 'export', 'jobs_shown', 'job_applied')
            order by created_at desc limit 2000)
        ) t
    ), '[]'::jsonb)
  );
end $$;

create or replace function public.cvmax_upsert_vacancies(p_token text, p_items jsonb)
returns int language plpgsql security definer set search_path = '' as $$
declare
  item jsonb;
  v_url text;
  v_title text;
  v_posted date;
  v_remote boolean;
  v_region text;
  v_family text;
  v_count int := 0;
begin
  perform cvmax_private.check_token(p_token, 'learning');
  for item in select * from jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) loop
    v_url := left(btrim(coalesce(item->>'url', '')), 2000);
    v_title := left(btrim(coalesce(item->>'title', '')), 300);
    if v_url = '' or v_title = '' then
      continue;
    end if;
    v_posted := null;
    begin
      v_posted := nullif(left(coalesce(item->>'posted_at', ''), 10), '')::date;
    exception when others then
      v_posted := null;
    end;
    v_region := left(coalesce(item->>'region', ''), 40);
    v_family := left(coalesce(item->>'role_family', ''), 80);
    v_remote := case when item->>'remote' in ('true', 'false') then (item->>'remote')::boolean end;
    insert into public.cvmax_vacancies
      (url_hash, url, source, title, company, location, region, role_family, posted_at, salary, snippet,
       remote, query)
    values (md5(v_url), v_url, left(coalesce(nullif(item->>'source', ''), 'unknown'), 40), v_title,
            left(item->>'company', 200), left(item->>'location', 200), v_region,
            v_family, v_posted, left(item->>'salary', 120),
            left(item->>'snippet', 600), v_remote, left(item->>'query', 200))
    on conflict (url_hash, role_family, region) do update
      set last_seen_at = now(),
          title = excluded.title,
          snippet = excluded.snippet,
          salary = excluded.salary,
          query = excluded.query;
    v_count := v_count + 1;
  end loop;
  return v_count;
end $$;

create or replace function public.cvmax_recent_vacancies(
  p_token text, p_role_family text, p_region text, p_days int default 30, p_limit int default 300)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token, 'learning');
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'id', t.id, 'url', t.url, 'source', t.source, 'title', t.title, 'company', t.company,
             'location', t.location, 'posted_at', t.posted_at, 'salary', t.salary,
             'snippet', t.snippet, 'remote', t.remote) order by t.last_seen_at desc, t.id desc)
      from (select * from (
              select distinct on (url_hash) *
                from public.cvmax_vacancies
               where last_seen_at >= now() - make_interval(days => greatest(p_days, 1))
                 and (p_role_family is null or role_family = p_role_family)
                 and (p_region is null or region = p_region)
               order by url_hash, last_seen_at desc, id desc) d
             order by last_seen_at desc, id desc
             limit greatest(least(p_limit, 1000), 1)) t
  ), '[]'::jsonb);
end $$;

create or replace function public.cvmax_save_skill_demand(p_token text, p_items jsonb)
returns int language plpgsql security definer set search_path = '' as $$
declare v_count int;
begin
  perform cvmax_private.check_token(p_token, 'learning');
  delete from public.cvmax_skill_demand d
   using (select distinct i->>'program' as program, i->>'region' as region
            from jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) i
           where coalesce(i->>'program', '') <> '' and coalesce(i->>'region', '') <> ''
             and coalesce(i->>'skill', '') <> '') b
   where d.program = b.program and d.region = b.region;
  insert into public.cvmax_skill_demand (program, region, skill, postings, total_postings, window_days)
  select i->>'program', i->>'region', left(i->>'skill', 80),
         coalesce((i->>'postings')::int, 0), coalesce((i->>'total_postings')::int, 0),
         coalesce((i->>'window_days')::int, 30)
    from jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) i
   where coalesce(i->>'program', '') <> '' and coalesce(i->>'region', '') <> ''
     and coalesce(i->>'skill', '') <> '';
  get diagnostics v_count = row_count;
  return v_count;
end $$;

create or replace function public.cvmax_skill_demand(p_token text, p_program text, p_region text)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'skill', t.skill, 'postings', t.postings, 'total_postings', t.total_postings,
             'window_days', t.window_days, 'computed_at', t.computed_at)
             order by t.postings desc, t.skill)
      from (select * from public.cvmax_skill_demand
             where program = p_program and region = p_region
             order by postings desc, skill limit 20) t
  ), '[]'::jsonb);
end $$;

-- Variant performance for the last 90 days, task 'analysis' (read in the SQL editor).
create or replace view cvmax_private.variant_performance as
  select v.variant, v.program, v.successes, v.failures,
         v.successes + v.failures as trials,
         round(100.0 * v.successes / nullif(v.successes + v.failures, 0), 1) as success_pct
    from cvmax_private.variant_stats_rows('analysis', 90) v
   order by trials desc;

-- Events per day and kind (read in the SQL editor).
create or replace view cvmax_private.events_by_day as
  select date_trunc('day', created_at)::date as day, kind, count(*) as count
    from public.cvmax_events group by 1, 2 order by 1 desc, 3 desc;

do $$
declare f text;
begin
  foreach f in array array[
    'public.cvmax_log_event(text, text, text, jsonb)',
    'public.cvmax_variant_stats(text, text, int)',
    'public.cvmax_learning_export(text, int)',
    'public.cvmax_upsert_vacancies(text, jsonb)',
    'public.cvmax_recent_vacancies(text, text, text, int, int)',
    'public.cvmax_save_skill_demand(text, jsonb)',
    'public.cvmax_skill_demand(text, text, text)'
  ] loop
    execute format('revoke all on function %s from public, authenticated', f);
    execute format('grant execute on function %s to anon', f);
  end loop;
end $$;
revoke all on function cvmax_private.variant_stats_rows(text, int) from public;

-- ===== Opt-in samples and the weekly judge (see docs/plan-judge-loop.md) =====

-- Opt-in anonymised samples for the weekly quality review (docs/plan-judge-loop.md, Part A).
-- Stored only when the user ticks "Help improve GetCVmax". Names, emails, phones and links are removed in the app
-- before saving. Kept 12 months, deleted with "Delete my data". No direct access: only the functions below.
create table if not exists public.cvmax_training_samples (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  user_key text not null,
  consent_version text not null,
  meta jsonb not null,
  cv_text text not null check (char_length(cv_text) <= 40000),
  analysis jsonb not null,
  judged_at timestamptz,
  judge jsonb
);
create index if not exists cvmax_training_samples_user on public.cvmax_training_samples (user_key);
create index if not exists cvmax_training_samples_created on public.cvmax_training_samples (created_at);
alter table public.cvmax_training_samples enable row level security;
revoke all on public.cvmax_training_samples from public, anon, authenticated;

create or replace function public.cvmax_save_training_sample(
  p_token text, p_email text, p_consent_version text, p_meta jsonb, p_cv_text text, p_analysis jsonb)
returns uuid language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  perform cvmax_private.check_token(p_token, 'app');
  if p_email is null or char_length(p_email) = 0 or char_length(p_email) > 254 then
    raise exception 'bad email' using errcode = '22023';
  end if;
  if p_consent_version is null or char_length(p_consent_version) = 0 or char_length(p_consent_version) > 40 then
    raise exception 'bad consent version' using errcode = '22023';
  end if;
  if p_cv_text is null or char_length(btrim(p_cv_text)) = 0 or char_length(p_cv_text) > 40000 then
    raise exception 'bad cv text' using errcode = '22023';
  end if;
  if p_meta is null or jsonb_typeof(p_meta) <> 'object' or length(p_meta::text) > 4000 then
    raise exception 'bad meta' using errcode = '22023';
  end if;
  if p_analysis is null or length(p_analysis::text) > 300000 then
    raise exception 'analysis too big' using errcode = '22023';
  end if;
  -- Retention does not depend on any schedule: every save first deletes copies older than 12 months.
  delete from public.cvmax_training_samples where created_at < now() - interval '12 months';
  insert into public.cvmax_training_samples (user_key, consent_version, meta, cv_text, analysis)
  values (lower(p_email), p_consent_version, p_meta, p_cv_text, p_analysis)
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.cvmax_training_samples_for_judge(p_token text, p_days int default 7, p_limit int default 20)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token, 'learning');
  return coalesce((
    select jsonb_agg(jsonb_build_object('id', t.id, 'meta', t.meta, 'cv_text', t.cv_text, 'analysis', t.analysis)
                     order by t.created_at desc, t.id)
      from (select * from public.cvmax_training_samples
             where judged_at is null
               and created_at >= now() - make_interval(days => greatest(coalesce(p_days, 7), 1))
             order by created_at desc, id
             limit greatest(least(coalesce(p_limit, 20), 50), 1)) t
  ), '[]'::jsonb);
end $$;

create or replace function public.cvmax_save_judgement(p_token text, p_id uuid, p_judge jsonb)
returns void language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token, 'learning');
  if p_judge is null or jsonb_typeof(p_judge) <> 'object' or length(p_judge::text) > 100000 then
    raise exception 'bad judgement' using errcode = '22023';
  end if;
  update public.cvmax_training_samples set judge = p_judge, judged_at = now() where id = p_id;
end $$;

create or replace function public.cvmax_judged_samples(p_token text, p_days int default 90, p_limit int default 200)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token, 'learning');
  return coalesce((
    select jsonb_agg(jsonb_build_object('id', t.id, 'meta', t.meta, 'cv_text', t.cv_text,
                                        'analysis', t.analysis, 'judge', t.judge)
                     order by t.judged_at desc, t.id)
      from (select * from public.cvmax_training_samples
             where judged_at is not null
               and judged_at >= now() - make_interval(days => greatest(coalesce(p_days, 90), 1))
             order by judged_at desc, id
             limit greatest(least(coalesce(p_limit, 200), 200), 1)) t
  ), '[]'::jsonb);
end $$;

create or replace function public.cvmax_purge_training_samples(p_token text)
returns int language plpgsql security definer set search_path = '' as $$
declare v_count int;
begin
  perform cvmax_private.check_token(p_token, 'learning');
  delete from public.cvmax_training_samples where created_at < now() - interval '12 months';
  get diagnostics v_count = row_count;
  return v_count;
end $$;

do $$
declare f text;
begin
  foreach f in array array[
    'public.cvmax_save_training_sample(text, text, text, jsonb, text, jsonb)',
    'public.cvmax_training_samples_for_judge(text, int, int)',
    'public.cvmax_save_judgement(text, uuid, jsonb)',
    'public.cvmax_judged_samples(text, int, int)',
    'public.cvmax_purge_training_samples(text)'
  ] loop
    execute format('revoke all on function %s from public, authenticated', f);
    execute format('grant execute on function %s to anon', f);
  end loop;
end $$;

-- ===== Billing: purchases and credits (see docs/plan-paywall.md) =====
-- Token scope 'billing' (the Paddle webhook): may only grant and refund purchases. The 'app' token may do everything.
-- Purchases are accounting records: "Delete my data" keeps them but replaces user_key with a hash.

create table if not exists public.cvmax_purchases (
  id bigint generated always as identity primary key,
  user_key text not null check (char_length(user_key) between 1 and 254),
  plan text not null check (plan in ('full','hunt')),
  transaction_id text not null unique check (char_length(transaction_id) between 1 and 100),
  full_credits int not null check (full_credits >= 0),
  amount_cents int,
  currency text check (char_length(currency) <= 3),
  status text not null default 'paid' check (status in ('paid','refunded')),
  created_at timestamptz not null default now(),
  refunded_at timestamptz
);
create index if not exists cvmax_purchases_user on public.cvmax_purchases (user_key);

create table if not exists public.cvmax_credit_use (
  id bigint generated always as identity primary key,
  user_key text not null check (char_length(user_key) between 1 and 254),
  kind text not null default 'full' check (kind = 'full'),
  mode text not null default 'credit' check (mode in ('credit','rescan')),
  analysis_id text not null unique check (char_length(analysis_id) between 1 and 100),
  target_role text not null default '',
  rescans_left int not null default 0,
  parent_id bigint,
  refunded boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists cvmax_credit_use_user on public.cvmax_credit_use (user_key, created_at);

-- Per-purchase accounting: each credit use is linked to the purchase it consumed, and has a purpose.
alter table public.cvmax_credit_use add column if not exists purchase_id bigint;
alter table public.cvmax_credit_use add column if not exists purpose text not null default 'review';
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'cvmax_credit_use_purpose_check') then
    alter table public.cvmax_credit_use add constraint cvmax_credit_use_purpose_check
      check (purpose in ('review', 'builder'));
  end if;
end $$;

alter table public.cvmax_purchases enable row level security;
alter table public.cvmax_credit_use enable row level security;
revoke all on public.cvmax_purchases, public.cvmax_credit_use from public, anon, authenticated;

-- Unspent full credits: per purchase that is not refunded and younger than 12 months, credits minus the live
-- credit-mode uses linked to it. Never below zero per purchase; other purchases are untouched.
create or replace function cvmax_private.full_balance(p_user_key text) returns int
language sql stable security definer set search_path = '' as $$
  select coalesce(sum(greatest(p.full_credits - (
           select count(*) from public.cvmax_credit_use u
            where u.purchase_id = p.id and u.mode = 'credit' and not u.refunded), 0)), 0)::int
    from public.cvmax_purchases p
   where p.user_key = p_user_key and p.status = 'paid' and p.created_at > now() - interval '12 months'
$$;

-- The oldest purchase (not refunded, not expired) that still has an unspent credit, or null.
create or replace function cvmax_private.next_purchase(p_user_key text) returns bigint
language sql stable security definer set search_path = '' as $$
  select p.id from public.cvmax_purchases p
   where p.user_key = p_user_key and p.status = 'paid' and p.created_at > now() - interval '12 months'
     and p.full_credits - (select count(*) from public.cvmax_credit_use u
                            where u.purchase_id = p.id and u.mode = 'credit' and not u.refunded) > 0
   order by p.created_at, p.id limit 1
$$;

-- Same normalisation as cvmax.db.norm_role: lower-case, trim, collapse inner whitespace, 200 chars.
create or replace function cvmax_private.norm_role(p_role text) returns text
language sql immutable set search_path = '' as $$
  select left(btrim(regexp_replace(lower(coalesce(p_role, '')), '\s+', ' ', 'g')), 200)
$$;

create or replace function public.cvmax_grant_purchase(
  p_token text, p_user_key text, p_plan text, p_transaction_id text,
  p_amount_cents int default null, p_currency text default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_key text := lower(btrim(coalesce(p_user_key, '')));
  v_credits int;
  v_id bigint;
begin
  perform cvmax_private.check_token(p_token, 'billing');
  if char_length(v_key) = 0 or char_length(v_key) > 254 then
    raise exception 'bad user key' using errcode = '22023';
  end if;
  if p_transaction_id is null or char_length(p_transaction_id) = 0 or char_length(p_transaction_id) > 100 then
    raise exception 'bad transaction id' using errcode = '22023';
  end if;
  v_credits := case p_plan when 'full' then 1 when 'hunt' then 5 else null end;
  if v_credits is null then
    raise exception 'bad plan' using errcode = '22023';
  end if;
  insert into public.cvmax_purchases (user_key, plan, transaction_id, full_credits, amount_cents, currency)
  values (v_key, p_plan, p_transaction_id, v_credits, p_amount_cents, upper(left(p_currency, 3)))
  on conflict (transaction_id) do nothing
  returning id into v_id;
  if v_id is null then
    return jsonb_build_object('granted', false, 'reason', 'duplicate');
  end if;
  return jsonb_build_object('granted', true, 'plan', p_plan, 'full_credits', v_credits);
end $$;

create or replace function public.cvmax_refund_purchase(p_token text, p_transaction_id text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_row public.cvmax_purchases%rowtype;
  v_before int;
  v_after int;
begin
  perform cvmax_private.check_token(p_token, 'billing');
  select * into v_row from public.cvmax_purchases where transaction_id = p_transaction_id;
  if not found then
    return jsonb_build_object('refunded', false, 'reason', 'unknown');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('cvmax_billing:' || v_row.user_key, 0));
  v_before := cvmax_private.full_balance(v_row.user_key);
  update public.cvmax_purchases set status = 'refunded', refunded_at = now()
   where id = v_row.id and status = 'paid';
  if not found then
    return jsonb_build_object('refunded', false, 'reason', 'already_refunded');
  end if;
  v_after := cvmax_private.full_balance(v_row.user_key);
  -- Only this purchase's unspent credits disappear; uses linked to it stay consumed; other purchases are untouched.
  return jsonb_build_object('refunded', true, 'plan', v_row.plan, 'removed', v_before - v_after);
end $$;

create or replace function public.cvmax_credits(p_token text, p_user_key text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_key text := lower(btrim(coalesce(p_user_key, '')));
  v_last public.cvmax_credit_use%rowtype;
begin
  perform cvmax_private.check_token(p_token, 'app');
  if char_length(v_key) = 0 or char_length(v_key) > 254 then
    raise exception 'bad user key' using errcode = '22023';
  end if;
  select * into v_last from public.cvmax_credit_use
   where user_key = v_key and mode = 'credit' and purpose = 'review' and not refunded
   order by id desc limit 1;
  return jsonb_build_object(
    'full', cvmax_private.full_balance(v_key),
    'has_hunt', exists (select 1 from public.cvmax_purchases
                         where user_key = v_key and plan = 'hunt' and status = 'paid'),
    -- Roles (normalised) that still have a free re-scan: review uses with one left, purchase not refunded.
    'rescan_roles', coalesce((select jsonb_agg(distinct u.target_role)
                                from public.cvmax_credit_use u
                                join public.cvmax_purchases p on p.id = u.purchase_id
                               where u.user_key = v_key and u.mode = 'credit' and u.purpose = 'review'
                                 and not u.refunded and u.rescans_left > 0 and p.status = 'paid'),
                             '[]'::jsonb),
    'last_use', case when v_last.id is null then null else jsonb_build_object(
      'analysis_id', v_last.analysis_id, 'target_role', v_last.target_role,
      'rescans_left', v_last.rescans_left, 'created_at', v_last.created_at) end);
end $$;

-- p_purpose: 'review' (a CV review: may be re-scanned once) or 'builder' (a CV build: never re-scans).
drop function if exists public.cvmax_use_credit(text, text, text, text);
create or replace function public.cvmax_use_credit(
  p_token text, p_user_key text, p_analysis_id text, p_target_role text, p_purpose text default 'review')
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_key text := lower(btrim(coalesce(p_user_key, '')));
  v_role text := cvmax_private.norm_role(p_target_role);
  v_existing public.cvmax_credit_use%rowtype;
  v_parent bigint;
  v_purchase bigint;
begin
  perform cvmax_private.check_token(p_token, 'app');
  if char_length(v_key) = 0 or char_length(v_key) > 254 then
    raise exception 'bad user key' using errcode = '22023';
  end if;
  if p_analysis_id is null or char_length(p_analysis_id) = 0 or char_length(p_analysis_id) > 100 then
    raise exception 'bad analysis id' using errcode = '22023';
  end if;
  if p_purpose is null or p_purpose not in ('review', 'builder') then
    raise exception 'bad purpose' using errcode = '22023';
  end if;
  -- One spender per user at a time, so two tabs cannot spend the same credit twice.
  perform pg_advisory_xact_lock(hashtextextended('cvmax_billing:' || v_key, 0));

  select * into v_existing from public.cvmax_credit_use where analysis_id = p_analysis_id;
  if found then
    if v_existing.user_key <> v_key then
      return jsonb_build_object('allowed', false, 'reason', 'conflict');
    end if;
    if not v_existing.refunded then
      return jsonb_build_object('allowed', true, 'mode', v_existing.mode, 'duplicate', true);
    end if;
    delete from public.cvmax_credit_use where id = v_existing.id;  -- refunded earlier: this retry spends again
  end if;

  if p_purpose = 'review' then
    -- A free re-scan: an earlier review use (not a builder use) with the same role, one left, purchase not refunded.
    select u.id into v_parent
      from public.cvmax_credit_use u join public.cvmax_purchases p on p.id = u.purchase_id
     where u.user_key = v_key and u.mode = 'credit' and u.purpose = 'review' and not u.refunded
       and u.rescans_left > 0 and u.target_role = v_role and p.status = 'paid'
     order by u.id desc limit 1;
    if v_parent is not null then
      update public.cvmax_credit_use set rescans_left = rescans_left - 1 where id = v_parent;
      insert into public.cvmax_credit_use (user_key, mode, purpose, analysis_id, target_role, rescans_left, parent_id)
      values (v_key, 'rescan', 'review', p_analysis_id, v_role, 0, v_parent);
      return jsonb_build_object('allowed', true, 'mode', 'rescan', 'duplicate', false);
    end if;
  end if;

  v_purchase := cvmax_private.next_purchase(v_key);
  if v_purchase is null then
    return jsonb_build_object('allowed', false, 'reason', 'no_credit');
  end if;
  insert into public.cvmax_credit_use (user_key, mode, purpose, analysis_id, target_role, rescans_left, purchase_id)
  values (v_key, 'credit', p_purpose, p_analysis_id, v_role, case when p_purpose = 'review' then 1 else 0 end,
          v_purchase);
  return jsonb_build_object('allowed', true, 'mode', 'credit', 'duplicate', false);
end $$;

create or replace function public.cvmax_refund_credit(p_token text, p_user_key text, p_analysis_id text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_key text := lower(btrim(coalesce(p_user_key, '')));
  v_row public.cvmax_credit_use%rowtype;
begin
  perform cvmax_private.check_token(p_token, 'app');
  perform pg_advisory_xact_lock(hashtextextended('cvmax_billing:' || v_key, 0));
  update public.cvmax_credit_use set refunded = true
   where analysis_id = p_analysis_id and user_key = v_key and not refunded
   returning * into v_row;
  if not found then
    return jsonb_build_object('refunded', false);
  end if;
  if v_row.mode = 'rescan' and v_row.parent_id is not null then
    update public.cvmax_credit_use set rescans_left = rescans_left + 1 where id = v_row.parent_id;
  end if;
  return jsonb_build_object('refunded', true, 'mode', v_row.mode);
end $$;

-- Paid revenue per day and plan (read in the SQL editor).
create or replace view cvmax_private.revenue_by_day as
  select date_trunc('day', created_at)::date as day, plan, status, count(*) as purchases,
         sum(coalesce(amount_cents, 0)) / 100.0 as amount
    from public.cvmax_purchases group by 1, 2, 3 order by 1 desc, 2;

do $$
declare f text;
begin
  foreach f in array array[
    'public.cvmax_grant_purchase(text, text, text, text, int, text)',
    'public.cvmax_refund_purchase(text, text)',
    'public.cvmax_credits(text, text)',
    'public.cvmax_use_credit(text, text, text, text, text)',
    'public.cvmax_refund_credit(text, text, text)'
  ] loop
    execute format('revoke all on function %s from public, authenticated', f);
    execute format('grant execute on function %s to anon', f);
  end loop;
end $$;
revoke all on function cvmax_private.full_balance(text), cvmax_private.next_purchase(text), cvmax_private.norm_role(text)
  from public, anon, authenticated;
