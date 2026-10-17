-- Run this whole file once: Supabase dashboard -> your project -> SQL Editor -> New query -> paste -> Run.
-- It is safe to run twice. It does two things:
--   1. adds the table and functions for the opt-in "Help improve GetCVmax" samples and the weekly judge
--      (save a sample, list unjudged ones, save a judgement, list judged ones, purge samples older than 12 months);
--   2. makes "Delete my data" also delete the user's saved samples.
-- Run it after the earlier SQL files (the token check and the other tables already exist).
-- The same code is in supabase/schema.sql, which stays the source of truth.

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

create or replace function public.cvmax_delete_my_data(p_token text, p_email text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  -- Today's counters stay: otherwise "delete data" would reset the daily limits.
  delete from public.cvmax_usage where user_key = lower(p_email) and created_at < cvmax_private.day_start();
  delete from public.cvmax_edit_feedback where user_key = lower(p_email);
  delete from public.cvmax_events where user_key = lower(p_email);
  delete from public.cvmax_training_samples where user_key = lower(p_email);
  delete from public.cvmax_waitlist where email = lower(p_email);
  delete from public.cvmax_users where email = lower(p_email);
end $$;
