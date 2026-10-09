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
