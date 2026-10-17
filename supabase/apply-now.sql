-- Run this whole file once: Supabase dashboard -> your project -> SQL Editor -> New query -> paste -> Run.
-- It is safe to run twice. It does two things:
--   1. adds the function the weekly market job uses to save "which skills postings ask for";
--   2. makes "Delete my data" also delete a user's learning events (privacy promise on the site).
-- Everything else from supabase/schema.sql is already in your database.

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
revoke all on function public.cvmax_save_skill_demand(text, jsonb) from public, authenticated;
grant execute on function public.cvmax_save_skill_demand(text, jsonb) to anon;

create or replace function public.cvmax_delete_my_data(p_token text, p_email text)
returns void language plpgsql security definer set search_path = '' as $$
begin
  perform cvmax_private.check_token(p_token);
  -- Today's counters stay: otherwise "delete data" would reset the daily limits.
  delete from public.cvmax_usage where user_key = lower(p_email) and created_at < cvmax_private.day_start();
  delete from public.cvmax_edit_feedback where user_key = lower(p_email);
  delete from public.cvmax_events where user_key = lower(p_email);
  -- Same deletes as the final version in schema.sql. The tables of later files are checked first, so this file is
  -- safe to run in any order: running it again after apply-judge.sql or apply-billing.sql must not drop their deletes.
  if to_regclass('public.cvmax_training_samples') is not null then
    execute 'delete from public.cvmax_training_samples where user_key = $1' using lower(p_email);
  end if;
  if to_regclass('public.cvmax_credit_use') is not null then
    execute 'delete from public.cvmax_credit_use where user_key = $1' using lower(p_email);
  end if;
  if to_regclass('public.cvmax_purchases') is not null then
    -- Purchases are accounting records: kept, but no longer linked to the person.
    execute 'update public.cvmax_purchases set user_key = ''deleted:'' || md5(user_key) where user_key = $1'
      using lower(p_email);
  end if;
  delete from public.cvmax_waitlist where email = lower(p_email);
  delete from public.cvmax_users where email = lower(p_email);
end $$;
