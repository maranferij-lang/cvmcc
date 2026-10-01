# База даних CVmax

Схема вже застосована в проєкті Supabase `cvmax`. Ці файли потрібні, щоб відтворити її в новому проєкті.

1. Виконай `schema.sql` у SQL Editor.
2. Згенеруй випадковий токен, напр. `python -c "import secrets; print('cvmx_' + secrets.token_urlsafe(32))"`.
3. Збережи в базі тільки його хеш:
   ```sql
   insert into cvmax_private.app_config (id, token_hash)
   values (1, encode(extensions.digest('ТВІЙ_ТОКЕН', 'sha256'), 'hex'))
   on conflict (id) do update set token_hash = excluded.token_hash;
   ```
4. Сам токен встав у Streamlit Secrets як `CVMAX_DB_TOKEN`.

Щоб змінити токен, повтори кроки 2-4. Старий одразу перестане працювати.
