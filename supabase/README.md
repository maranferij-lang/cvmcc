# GetCVmax database

The schema is already applied in the Supabase project `cvmax`. These files are needed to recreate it in a new project.

1. Run `schema.sql` in the SQL Editor.
2. Generate a random token, e.g. `python -c "import secrets; print('cvmx_' + secrets.token_urlsafe(32))"`.
3. Store only its hash in the database:
   ```sql
   insert into cvmax_private.app_config (id, token_hash)
   values (1, encode(extensions.digest('YOUR_TOKEN', 'sha256'), 'hex'))
   on conflict (id) do update set token_hash = excluded.token_hash;
   ```
4. Put the token itself into Streamlit Secrets as `CVMAX_DB_TOKEN`.

5. A separate token for GitHub Actions (scope `learning`: only the learning and jobs functions, with no access to
   saved CVs). Generate another token and store its hash:
   ```sql
   insert into cvmax_private.app_config (id, scope, token_hash)
   values (2, 'learning', encode(extensions.digest('TOKEN_FOR_CI', 'sha256'), 'hex'))
   on conflict (scope) do update set token_hash = excluded.token_hash;
   ```
   Put it into GitHub Secrets as `CVMAX_LEARN_TOKEN`. Do not put `CVMAX_DB_TOKEN` into GitHub.
   Workflows run the scripts in a job with read-only permission; the PR is opened by a separate job (see docs/learning/README.md).

To change a token, repeat steps 2-4. The old one stops working immediately.

## Updating an existing project: learning and jobs

If the project was created earlier, apply the new part of the schema. These commands are idempotent
(`create ... if not exists` and `create or replace`), so it is safe to repeat them.

1. Open the SQL Editor and run the whole `schema.sql` again: it contains no `drop` and does not touch the token.
   Do not run only the block starting from `-- ===== Learning loops and job links`: the learning functions call
   `cvmax_private.check_token(text, text)`, and step 5 needs the column `app_config.scope`. Both are
   defined at the beginning of the file, outside this block. If you still run it in parts, first run
   from the beginning of `schema.sql`: `alter table cvmax_private.app_config add column if not exists scope ...`,
   `drop constraint if exists app_config_id_check`, `create unique index ... app_config_scope_uniq`,
   both `create or replace function cvmax_private.check_token` (with one and with two arguments)
   and `revoke all on function cvmax_private.check_token(text, text) from public`, and only then the learning block.
2. Update `cvmax_delete_my_data`. Its definition in `schema.sql` now also deletes events
   (`delete from public.cvmax_events`). The old definition in the project does not do this, so run the
   updated one if you do not run the whole file.
3. Check that these tables appeared in the `public` schema, with RLS enabled and no policies:
   - `cvmax_events`: events (`kind`, `payload`, `user_key`), without CV text;
   - `cvmax_vacancies`: a snapshot of job postings (unique by `url_hash`);
   - `cvmax_skill_demand`: skills from job postings by direction and region.
4. Check the functions. All of them accept `p_token` and work only with a correct token, so
   `CVMAX_DB_TOKEN` in Secrets must match the hash in `cvmax_private.app_config`:
   - `cvmax_log_event`: record an event;
   - `cvmax_variant_stats`: statistics of prompt variants;
   - `cvmax_learning_export`: export of events for `scripts/learn.py`;
   - `cvmax_upsert_vacancies`: write a snapshot, used by `scripts/snapshot_jobs.py`;
   - `cvmax_recent_vacancies`: fresh job postings by direction and region;
   - `cvmax_save_skill_demand` and `cvmax_skill_demand`: write and read market skills
     (`scripts/learn_market.py`).
5. Analytics in the SQL Editor. Two private views that are not exposed to the app:
   - `cvmax_private.variant_performance`: successes and failures of variants over 90 days;
   - `cvmax_private.events_by_day`: number of events by day and kind.

   Example: `select * from cvmax_private.variant_performance;`
