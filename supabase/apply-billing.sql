-- Run this whole file once: Supabase dashboard -> your project -> SQL Editor -> New query -> paste -> Run.
-- It does three things:
--   1. adds the purchases and credit tables and the five billing functions (grant, refund, credits, use, refund credit);
--   2. replaces "Delete my data": it still deletes everything it deleted before (usage, edit feedback, events, opt-in
--      samples, waitlist, user), now also deletes credit uses, and keeps purchases as accounting records with the
--      owner replaced by a hash;
--   3. creates the token with scope 'billing' for the payment webhook (the last statement; its result is the token).
-- Everything except step 3 is safe to run twice. Run it after the earlier SQL files (the token check already exists).
-- Per-purchase accounting: every credit use is linked to the purchase it consumed (oldest first), credits expire 12
-- months after purchase, and cvmax_use_credit gains p_purpose ('review' | 'builder'); the old 4-argument version is
-- dropped. Credit uses written by an earlier version of this file have no purchase link and are not counted; the
-- billing is not live yet, so there are none.
-- Nothing here touches your saved CVs. The same code is in supabase/schema.sql, which stays the source of truth.

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

-- "Delete my data" with billing rows.
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

-- Last step: create the billing token (the Paddle webhook on Cloudflare Pages uses it as CVMAX_BILLING_TOKEN).
-- The result shows one value starting with cvmx_bill_. Copy it now: it is stored only as a hash. Running this again
-- creates a new token and the old one stops working, so run this whole file once and keep the value.
with t as (select 'cvmx_bill_' || encode(extensions.gen_random_bytes(24), 'hex') as token),
     u as (insert into cvmax_private.app_config (id, scope, token_hash)
           select 3, 'billing', encode(extensions.digest(token, 'sha256'), 'hex') from t
           on conflict (scope) do update set token_hash = excluded.token_hash
           returning 1)
select token from t;
