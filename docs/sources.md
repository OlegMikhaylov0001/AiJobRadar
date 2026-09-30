# Источники вакансий: условия использования и решения

Проверено 2026-09-30 по живым страницам (docs, ToS, ответы API). Условия меняются —
перед изменением набора источников перепроверить ссылки ниже.

Наш сценарий: раз в сутки забрать вакансии через официальный API/RSS, хранить в
приватной БД, показать одному человеку в личном Telegram, некоммерчески.

## Общие правила, вытекающие из условий

1. **Атрибуция в каждом сообщении.** Каждая вакансия в дайджесте содержит ссылку на
   страницу вакансии у источника и подпись «via <Источник>». Этого требуют Himalayas,
   Jobicy, RemoteOK, WWR; для HN — ссылка на комментарий.
2. **Не больше одного запроса на ленту в сутки** (минимум среди лимитов: Himalayas
   кэширует данные на 24 ч, Jobicy — не чаще раза в час, Remotive — до 4 раз в день).
3. **Никаких реальных записей в публичном репозитории.** Тексты вакансий — контент
   работодателей/источников; общие условия сайтов запрещают перепубликацию. Тестовые
   фикстуры — **синтетические**, со схемой, повторяющей реальные ответы. Реальные образцы
   лежат только в `private/raw/` (в `.gitignore`) для локальной разработки.
4. **Не передавать данные третьим сервисам-агрегаторам** (прямой запрет у Himalayas и
   Remotive). LLM-оценка через Claude — обработка для владельца, не публикация.
5. **Текст вакансии — недоверенный ввод** (см. RemoteOK ниже).

## Решения

| Источник | Путь | Решение | Почему |
|---|---|---|---|
| Himalayas | JSON API | **v1** | API явно разрешён, в т. ч. коммерчески, с атрибуцией; структурные `locationRestrictions` и `timezoneRestrictions` |
| Jobicy | JSON API | **v1** | самые явные условия под наш сценарий; атрибуция + канонический URL |
| We Work Remotely | RSS по категориям | **v1** | «anyone can use the feed» с атрибуцией; риск Cloudflare из IP CI |
| RemoteOK | JSON API | **v1** | атрибуция dofollow-ссылкой; много не-dev вакансий, лента запаздывает |
| HN «Who is hiring» | Algolia + HN Firebase API | **v1** | официальные API; ссылка на комментарий; не скрейпить news.ycombinator.com |
| Remocate | RSS (объявлен на сайте) | **v1, эксперимент** | удалёнка в нероссийских компаниях; элементы без описания — оценка по скудным данным |
| Хабр Карьера | RSS (объявлен на сайте) | **v1 (этап 7), решение владельца 2026-09-30** | §5.5 допускает личное некоммерческое использование; но ~2/3 вакансий — российские компании, в RSS нет полного описания |
| Djinni | RSS (объявлен на сайте) | **v1 (этап 7), решение владельца 2026-09-30** | полный текст, много удалёнки; платформа англо/украиноязычная |
| Greenhouse / Lever / Ashby | публичные job-board API | **v2** | официальные публичные эндпоинты; нужен ручной список компаний |
| Remotive | JSON API | **исключён** | бесплатный API отдаёт 16 вакансий на все категории, фильтр категорий игнорируется, задержка 24 ч; §8 ToS двусмысленен про «базу вакансий» |
| getmatch | — | **исключён** | нет официального API/RSS; внутренний `/api/` закрыт robots.txt |
| hh.ru, SuperJob | API с ключом | **исключены** | публичный поиск закрыт (403 без токена); российские работодатели |
| Geekjob | RSS не объявлен на сайте | **исключён** | в основном Москва / российские компании |
| Telegram-каналы | MTProto user-сессия | **исключены** | условия Telegram запрещают использовать контент для AI/ML-моделей; автоматизация user-аккаунта |
| LinkedIn, Indeed, Wellfound, Glassdoor | — | **исключены** | нет законного API для вакансий, скрейпинг запрещён |

## По источникам

### Himalayas
- Docs: https://himalayas.app/api · https://github.com/Himalayas-App/remote-jobs-api · Terms: https://himalayas.app/terms
- Условия: ссылка на страницу Himalayas + упоминание источника; не отправлять вакансии в
  Jooble / Google Jobs / LinkedIn; «Sync once daily, store the results, and serve from your
  own cache» (README). Лимит не опубликован, при превышении — 429.
- Эндпоинты: `GET /jobs/api?limit=20&cursor=…` (обход, курсор `nextCursor`) и
  `GET /jobs/api/search?q=…&sort=recent&page=N`. Параметра категории нет —
  фильтр по `parentCategories` содержит `Developer`.
- Гео: `locationRestrictions: string[]` (`[]` = без ограничений), `timezoneRestrictions:
  number[]` (все смещения = без ограничений).
- Особенности: одна роль публикуется по разу на страну → склеивать дедупом; `applicationLink`
  ведёт на Himalayas, не в ATS; `salaryPeriod` задаёт период суммы; описание в HTML.

### Jobicy
- Docs: https://jobicy.com/jobs-rss-feed · Terms: https://jobicy.com/terms-and-conditions
- Условия: можно использовать в своих продуктах без отдельного разрешения; сохранять
  Jobicy как источник и канонический URL; автоматические проверки не чаще раза в час.
- Эндпоинт: `GET https://jobicy.com/api/v2/remote-jobs?count=50&industry=engineering`
  (без пагинации, последние N ≤ 200). RSS беднее JSON (нет зарплаты и уровня) — берём JSON.
- Гео: только свободный текст `jobGeo` (`Anywhere`, `Europe`, `USA`, `APAC`, …).
- Особенности: ключи зарплаты **отсутствуют**, а не `null`; `industry` фильтрует нестрого.

### We Work Remotely
- Docs: https://weworkremotely.com/remote-job-rss-feed · Terms: https://weworkremotely.com/terms-and-conditions
- Условия: «Anyone can use the feed, all we ask is that you attribute the links back to
  We Work Remotely.» Лимит не указан (`<ttl>60</ttl>`).
- Ленты: `/categories/remote-full-stack-programming-jobs.rss`,
  `/categories/remote-back-end-programming-jobs.rss`, `/categories/remote-programming-jobs.rss`
  (пересекаются — дедуп по `guid`).
- Гео: `region` (`Anywhere in the World`, `North America Only`), `country` (флаг + название,
  часто пусто) и строки в описании (`Location: Remote - US`) — нужны все три.
- Особенности: компания и роль в `title` через `": "`; перепост получает новый `guid` с
  суффиксом `-3` (ловится нечётким дедупом); `pubDate` у перепостов старый — не использовать
  как водяной знак. HTML-страницы за Cloudflare; RSS отвечал, но из IP CI может блокироваться
  → при блокировке источник получит статус `failed`, это проверяется на этапе 4.

### RemoteOK
- Условия: в самом ответе API (`[0].legal`) и https://remoteok.com/legal — ссылка на
  страницу Remote OK (без `nofollow`) и упоминание источника, без их логотипа.
- Эндпоинт: `GET https://remoteok.com/api` — массив, `[0]` — служебный элемент.
- **Безопасность:** каждое описание заканчивается строкой вида «Please mention the word … and
  tag R… when applying», где метка — base64 IP-адреса запрашивающей машины. Эта строка
  (а) раскрывает IP в сохранённых данных, (б) является инструкцией для читателя, то есть
  поверхностью prompt-injection. Адаптер вырезает её до сохранения и до LLM.
- Особенности: `apply_url` = страница RemoteOK; `location` пусто у ~37%; `salary_min/max`
  = 0 означает «не указано», валюта не указана; лента отстаёт на несколько дней.

### Hacker News «Who is hiring?»
- Docs: https://github.com/HackerNews/API · https://hn.algolia.com/api · Terms: https://www.ycombinator.com/legal/
- Условия: только официальные API (Algolia ограничивает 10 000 запросов/час с IP);
  сайт не скрейпить; не перепубликовывать; ссылка на комментарий.
- Тред месяца: `search_by_date?tags=story,author_whoishiring` → самый новый с заголовком
  `Ask HN: Who is hiring?` (в ту же секунду публикуется «Who wants to be hired?» — отличать).
- Инкремент: `search_by_date?tags=comment,story_<id>&numericFilters=created_at_i%3E<watermark>`,
  оставлять только `parent_id == story_id`; водяной знак — в `kv`.
- Данные: свободный текст (`Company | Role | Location | REMOTE | …`, порядок полей
  плавает); извлечение — LLM, дешёвые правила — только по явным маркерам. В текстах бывают
  контактные email — персональные данные, не коммитить.

### Remocate
- Feed: https://www.remocate.app/feed.xml · Terms: https://www.remocate.app/terms-of-use
- Условия: запрета на автоматический доступ нет; robots.txt разрешает `/`.
- Данные: `title` = «Роль at Компания», `description` = «Страна · Remote · ≈ $…», без текста
  вакансии. Оценка LLM по таким данным слабая — в `main_risk` это должно отражаться.

### Greenhouse / Lever / Ashby (v2)
- Greenhouse: https://docs.greenhouse.io/job-board.html — `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`;
  нет поля «remote», только строка `location.name`; `content` — экранированный HTML.
- Lever: https://github.com/lever/postings-api — README прямо допускает сбор опубликованных
  вакансий третьими сторонами; `workplaceType` (`remote`/`hybrid`/`onsite`); EU-инстанс
  `api.eu.lever.co`.
- Ashby: https://developers.ashbyhq.com/docs/public-job-posting-api — `workplaceType`
  надёжнее `isRemote`; лучшие структурные данные о компенсации (`includeCompensation=true`).

### Хабр Карьера (v1, этап 7)
- API https://career.habr.com/info/api — только для интеграции работодателя (OAuth), не поиск.
- RSS `https://career.habr.com/vacancies/rss?remote=true&q=…` объявлен на странице вакансий.
  Соглашение §5.5 допускает личное некоммерческое использование при сохранении указаний
  авторства. В выборке 50 вакансий: 32 с пометкой «(Россия)», зарплаты в ₽ — большую часть
  может отсечь `R-EMPLOYER-COUNTRY` по профилю. Полного описания в RSS нет.

### Djinni (v1, этап 7)
- RSS https://djinni.co/jobs/rss/ (объявлен на странице `/jobs`), полный HTML-текст,
  фильтр `primary_keyword`. Условия https://djinni.co/terms-of-use не касаются
  автоматического доступа. Подходит ли площадка конкретному кандидату — решение владельца, не правил.
