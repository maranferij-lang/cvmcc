# План: система, що вчиться сама, і прямі посилання на вакансії

Автор плану: Fable 5.1. Реалізація: агенти на слабших моделях за задачами нижче, перевірка: сильні моделі.

## 1. Що будуємо

**Мета 1. Самонавчання.** Апка вже збирає сигнали (які правки прийняли, лайки на розбір, відгуки), але нічого з ними не робить. Тепер сигнали замикаються в чотири петлі, кожна зі своїм горизонтом:

| Петля | Що вчить | Горизонт | Механізм |
|---|---|---|---|
| A. Бандит над варіантами промпту | який стиль розбору людям корисніший | хвилини | Thompson sampling, нагорода = лайк або прийняті правки |
| B. Тижнева рефлексія | що модель робить не так у кожній сфері | тижні | LLM читає прийняті й відхилені правки → `rubrics/learned/<сфера>.md`; злиття тільки якщо evals не впали |
| C. Ринок | які навички зараз просять у вакансіях | дні | щоденний знімок вакансій → витяг навичок → `rubrics/market/<сфера>.md` і вкладка «Skills to build» |
| D. Результат | чи CV дало співбесіду | місяці | питання в «My account» через 7+ днів після розбору → довгострокова нагорода для A і B |

Гнучкість досягається тим, що все, що вчиться, лежить у даних, а не в коді: варіанти промпту в JSON, уроки й ринок у Markdown. Код лише обирає, зважує і відкочує. Запобіжники: evals-ворота перед злиттям, автовідключення варіанта-невдахи, автовідкат уроків, якщо частка лайків впала.

Чого не робимо зараз: fine-tuning (даних на це немає), векторну базу прикладів (етап 2, коли буде 500+ прийнятих правок).

**Мета 2. Прямі посилання на вакансії.** На сторінках «Where to apply» і «CV review» під кожним напрямом з'являються живі вакансії з посиланнями: DOU, Work.ua, Robota.ua, Djinni для України; Jooble, Adzuna, Arbeitnow, Remotive, Jobicy для UK/US/EU/remote; Greenhouse і Lever для конкретної компанії. Модель ранжує їх за CV і каже, чого бракує. LinkedIn, Indeed і Glassdoor дають тільки готові посилання на пошук: парсити їх заборонено правилами, а бан акаунта нам ні до чого.

## 2. Рішення, прийняті в плані

1. LinkedIn: лише deep link з уже підставленими ключовими словами, регіоном і фільтром «internship / entry level». Те саме для Indeed і Glassdoor.
2. Джерела без ключів працюють одразу (DOU RSS, Arbeitnow, Remotive, Jobicy, Greenhouse, Lever). Jooble і Adzuna вмикаються, коли ти додаси безплатні ключі в Secrets (`JOOBLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`). Без ключів ці джерела тихо пропускаються.
3. Усі адреси вакансій проходять allowlist доменів провайдера. Усе, що приходить ззовні, показується через `md_escape`; клікабельне лише перевірене посилання.
4. Події зберігаються без тексту CV: тільки ідентифікатори, оцінки, рішення й версія знань. Видалення даних стирає і події.
5. Автоматичні зміни знань (уроки, варіанти, ринок) ідуть через PR від GitHub Actions з результатами evals. Злиття автоматичне, якщо в репозиторії змінна `LEARN_AUTOMERGE=1`, інакше ти натискаєш merge.

## 3. Архітектура

```
cvmax/jobs/                 пошук вакансій
  models.py                 Vacancy, JobQuery
  http.py                   fetch з таймаутом, лімітом розміру, кешем і паралельністю
  providers/*.py            dou, workua, robotaua, djinni, jooble, adzuna, arbeitnow, remotive, jobicy,
                            greenhouse, lever, linkedin (deep link), indeed (deep link), glassdoor (deep link)
  search.py                 маршрутизація за регіоном, дедуплікація, фільтр рівня, попереднє ранжування
  rank.py                   LLM-ранжування: fit 0–100, чому, чого бракує
cvmax/learning/
  events.py                 словник подій і їхніх payload
  bandit.py                 Thompson sampling над варіантами промпту, автовідключення
  version.py                хеш знань (рубрики + уроки + ринок + варіанти)
cvmax/variants/analysis.json   варіанти промпту (дані)
cvmax/rubrics/learned/*.md     уроки з відгуків (пише scripts/learn.py)
cvmax/rubrics/market/*.md      ринок навичок (пише scripts/learn_market.py)
scripts/learn.py            тижнева рефлексія + автовідкат
scripts/snapshot_jobs.py    щоденний знімок вакансій у Supabase
scripts/learn_market.py     витяг навичок із знімка → skill demand
.github/workflows/learn.yml, market.yml
supabase/schema.sql         cvmax_events, cvmax_vacancies, cvmax_skill_demand + RPC
```

**Потік подій.** `analysis_done` (analysis_id, variant, program, region, score, knowledge_version, previous_score) → `analysis_rated` (rating) → `edits_decided` (accepted, total) → `grill_turn` (answered | skipped) → `export` → `jobs_shown` (source, n) → `job_applied` (source, fit) → `outcome` (interview: yes | no | not_yet). Нагорода для бандита: лайк = 1, дизлайк = 0; без лайка: прийнято ≥ 50% правок при ≥ 2 правках = 1, інакше 0. `outcome=yes` додається як +2 до successes варіанта.

**Потік вакансій.** напрям (role, search_keywords) + регіон + рівень → провайдери регіону паралельно (≤ 8 с) → дедуплікація → фільтр senior-ролей → попереднє ранжування за збігом слів і свіжістю (топ-40) → LLM (light-модель) дає fit і «чого бракує» → список із посиланнями, джерелом, датою, кнопкою «Applied» → подія.

## 4. Агенти, моделі, файли

Правила для всіх: тільки свої файли; не чіпати git; коментарі в коді українською, тексти інтерфейсу англійською; жодних ключів у коді; мережа з контейнера заблокована, тому парсери пишуться на фікстурах, а жива перевірка іде на GitHub Actions і на сервері.

| # | Агент | Модель | Файли | Результат |
|---|---|---|---|---|
| R1 | Дослідник джерел | sonnet | звіт у JSON (без файлів у репо) | точні URL фідів і API, формат відповіді, які потрібні ключі, deep links для кожного сайту |
| L1 | База даних | sonnet | `supabase/schema.sql`, `cvmax/db.py`, `tests/test_learning_db.py` | таблиці подій, вакансій, попиту на навички; RPC `cvmax_log_event`, `cvmax_variant_stats`, `cvmax_learning_export`, `cvmax_upsert_vacancies`, `cvmax_recent_vacancies`, `cvmax_save_skill_demand`, `cvmax_skill_demand`; методи в SupabaseDB і MemoryDB |
| L2a | Автор варіантів | opus | `cvmax/variants/analysis.json` | 3 варіанти стилю розбору, що справді відрізняються |
| L2b | Бандит | sonnet | `cvmax/learning/{__init__,bandit,version,events}.py`, `tests/test_bandit.py` | вибір варіанта, автовідключення, хеш знань |
| E1 | Evals | sonnet | `evals/cases/*`, `evals/run_evals.py`, `evals/baseline.json` | 3 нові вигадані кейси, `--json`, порівняння з базовою лінією |
| J1 | Провайдери | sonnet | `cvmax/jobs/{models,http}.py`, `cvmax/jobs/providers/*`, `tests/fixtures/jobs/*`, `tests/test_jobs_providers.py`, `requirements.txt` (defusedxml) | кожен провайдер: `build_urls`, `parse`, `search_url`, allowlist доменів |
| J2 | Пошук і ранжування | sonnet | `cvmax/jobs/{__init__,search,rank,demo}.py`, `cvmax/schemas.py` (+JobFit), `cvmax/demo.py` (+RankedJobs), `cvmax/config.py` (+LIMITS jobs), `tests/test_jobs_search.py` | `search_jobs`, `rank_jobs`, демо-дані для тестів |
| L3 | Рефлексія | sonnet | `cvmax/prompts.py`, `scripts/learn.py`, `.github/workflows/learn.yml`, `docs/learning/README.md`, `tests/test_learn.py` | уроки в промпті, тижневий скрипт з evals-воротами й автовідкатом |
| J4 | Ринок | sonnet | `scripts/snapshot_jobs.py`, `scripts/learn_market.py`, `.github/workflows/market.yml`, `tests/test_market.py` | знімок вакансій, витяг навичок, `rubrics/market/*.md` |
| U1 | Інтерфейс | sonnet | `views/career.py`, `views/analyze.py`, `views/account.py`, `ui/account.py`, `ui/jobs.py`, `tests/test_pages.py` | вакансії під напрямами і в розборі, події, бандит у розборі, питання про результат, ринок у «Skills to build» |
| D1 | Документація | haiku | `README.md`, `supabase/README.md`, `docs/learning/README.md` | що з'явилось, які секрети додати, як застосувати SQL |
| V1 | Тести | sonnet | нічого | повний `pytest -q`, звіт |
| V2 | Безпека | opus | нічого | SSRF, XSS через Markdown, витік даних у події, ключі |
| V3 | Коректність | opus | нічого | логіка бандита, SQL, парсери, UI-потоки |
| F | Виправлення | sonnet | за знахідками | кожна підтверджена знахідка виправлена, тести зелені |

Порядок: R1, L1, L2a, L2b, E1 паралельно → J1 після R1; L3 після L1+L2b+E1 → J2 після J1; J4 після J1+L1 → U1 після J2+L1+L2b → D1 → V1+V2+V3 → F → V1 повторно (до 3 кіл).

## 5. Перевірка

- `pytest -q` зелений, разом зі старими 68 тестами.
- Парсери на фікстурах: DOU RSS, Work.ua, Robota.ua, Djinni, Jooble, Adzuna, Arbeitnow, Remotive, Jobicy, Greenhouse, Lever. Битий XML і порожня відповідь не ламають сторінку.
- AppTest: «Where to apply» у демо показує вакансії з посиланнями; «CV review» логує події; «My account» показує питання про результат.
- Бандит: на 1 000 симульованих розборів з нагородами 0.7 / 0.5 / 0.3 обирає кращий варіант у понад 70% випадків, а варіант 0.3 відключає.
- Жодна адреса поза allowlist не доходить до екрана. Жоден payload події не містить тексту CV.

## 6. Що робиш ти

1. Виконати новий SQL у Supabase (блок «learning and jobs» у `supabase/schema.sql`) або дозволити мені застосувати міграцію.
2. Додати в GitHub Secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_LEARN_TOKEN` (токен зі scope `learning`, крок 5 у `supabase/README.md`; ніколи не токен застосунку `CVMAX_DB_TOKEN`), `GEMINI_API_KEY`. Змінна репозиторію `LEARN_AUTOMERGE=1`, якщо хочеш повну автономію.
3. Один раз записати базову лінію evals (без неї `learn.yml` і `market.yml` завжди падають з кодом 3): з реальним `GEMINI_API_KEY` у середовищі виконати `python evals/run_evals.py --runs 2 --update-baseline` і закомітити `evals/baseline.json`.
4. За бажанням: безплатні ключі Jooble (jooble.org/api/about) і Adzuna (developer.adzuna.com) у Secrets Streamlit і GitHub.

## 7. Етап 2 (після перших 100 розборів із відгуками)

- Приклади прийнятих правок у pgvector з ембедингами Gemini, добір за схожістю рядка.
- Лист через 14 днів: «чи було інтерв'ю» поштою, не тільки в кабінеті.
- Окремі варіанти промпту для Q&A і «Where to apply».
- Перерахунок ваг критеріїв рубрики за результатами (outcome).
