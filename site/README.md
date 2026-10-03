# getcvmax.com

Статичний лендінг, юридичні сторінки і форма списку очікування. Хоститься на Cloudflare Pages, збірки немає.

## Деплой
1. Cloudflare → Workers & Pages → Create → Pages → Connect to Git → репозиторій `cvmcc`, гілка `main`.
2. Build command: порожньо. Build output directory: `site`. Root directory: порожньо.
3. Settings → Variables and Secrets (тип Secret): `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN` (ті самі, що в апці).
4. Custom domains → `getcvmax.com` і `www.getcvmax.com`.
5. Metrics → Web Analytics → Enable (рахує перегляди без cookies).

## Перед публікацією
- Замінити `FOP [Full legal name]` у `terms.html`, `privacy.html`, `refund.html` на ФОП з повним ім'ям.
- Коли запрацюють оплати: у `site.js` поставити `APP_URL = "https://app.getcvmax.com"`, і всі кнопки цін ведуть в апку.

## Список очікування
Форма → `functions/api/waitlist.js` → RPC `cvmax_join_waitlist` (токен лише на сервері).
Скільки записалось і звідки: SQL Editor → `select * from cvmax_private.waitlist_by_source;`
Посилання з міткою каналу: `https://getcvmax.com/?ref=youtube`, `?ref=dou`, `?ref=tg-happymonday` тощо.
