# База даних GetCVmax

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

5. Окремий токен для GitHub Actions (область `learning`: лише функції навчання і вакансій, без доступу до
   збережених CV). Згенеруй інший токен і збережи його хеш:
   ```sql
   insert into cvmax_private.app_config (id, scope, token_hash)
   values (2, 'learning', encode(extensions.digest('ТОКЕН_ДЛЯ_CI', 'sha256'), 'hex'))
   on conflict (scope) do update set token_hash = excluded.token_hash;
   ```
   Його встав у GitHub Secrets як `CVMAX_LEARN_TOKEN`. `CVMAX_DB_TOKEN` у GitHub не клади.
   Workflow виконують скрипти в job з правом лише читання; PR відкриває окремий job (див. docs/learning/README.md).

Щоб змінити токен, повтори кроки 2-4. Старий одразу перестане працювати.

## Оновлення існуючого проєкту: навчання і вакансії

Якщо проєкт уже створено раніше, застосуй нову частину схеми. Ці команди ідемпотентні
(`create ... if not exists` і `create or replace`), тож їх безпечно повторити.

1. Відкрий SQL Editor і виконай весь `schema.sql` повторно: він не містить `drop` і не чіпає токен.
   Не виконуй лише блок від `-- ===== Learning loops and job links`: функції навчання викликають
   `cvmax_private.check_token(text, text)`, а крок 5 потребує колонки `app_config.scope`. Обидва
   визначені на початку файлу, поза цим блоком. Якщо все ж запускаєш частинами, спершу виконай
   з початку `schema.sql`: `alter table cvmax_private.app_config add column if not exists scope ...`,
   `drop constraint if exists app_config_id_check`, `create unique index ... app_config_scope_uniq`,
   обидві `create or replace function cvmax_private.check_token` (з одним і з двома аргументами)
   та `revoke all on function cvmax_private.check_token(text, text) from public`, і лише потім блок навчання.
2. Онови `cvmax_delete_my_data`. Її визначення в `schema.sql` тепер також видаляє події
   (`delete from public.cvmax_events`). Старе визначення в проєкті цього не робить, тому виконай
   оновлене, якщо не запускаєш файл повністю.
3. Перевір, що з'явилися таблиці в схемі `public`, з увімкненим RLS і без політик:
   - `cvmax_events`: події (`kind`, `payload`, `user_key`), без тексту CV;
   - `cvmax_vacancies`: знімок вакансій (унікальність за `url_hash`);
   - `cvmax_skill_demand`: навички з вакансій за напрямом і регіоном.
4. Перевір функції. Усі приймають `p_token` і працюють лише з коректним токеном, тому
   `CVMAX_DB_TOKEN` у Secrets мусить збігатися з хешем у `cvmax_private.app_config`:
   - `cvmax_log_event`: записати подію;
   - `cvmax_variant_stats`: статистика варіантів промпту;
   - `cvmax_learning_export`: експорт подій для `scripts/learn.py`;
   - `cvmax_upsert_vacancies`: запис знімка, використовує `scripts/snapshot_jobs.py`;
   - `cvmax_recent_vacancies`: свіжі вакансії за напрямом і регіоном;
   - `cvmax_save_skill_demand` і `cvmax_skill_demand`: запис і читання навичок ринку
     (`scripts/learn_market.py`).
5. Аналітика в SQL Editor. Два приватні вигляди, які не відкриті для застосунку:
   - `cvmax_private.variant_performance`: успіхи й невдачі варіантів за 90 днів;
   - `cvmax_private.events_by_day`: кількість подій за днями і видами.

   Приклад: `select * from cvmax_private.variant_performance;`
