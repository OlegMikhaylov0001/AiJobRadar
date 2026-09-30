# AiJobRadar — спецификация (design)

Дата: 2026-09-29 · Статус: черновик на ревью

## 1. Цель

Раз в сутки собирать удалённые вакансии из нескольких источников, отсеивать заведомо
неподходящие дешёвыми детерминированными правилами, ранжировать остаток LLM-ом против
профиля кандидата и присылать в Telegram **до 5** вакансий выше порога — с обоснованием по
каждой: почему подходит, главный риск, чего не хватает.

Портфолио-проект: публичный репозиторий, измеримое качество ранжирования, честные статусы
вместо тихих провалов.

### Критерии успеха

1. Дайджест приходит каждый день в заданное время (±задержка cron GitHub Actions), в том
   числе в дни, когда подходящих нет или часть источников упала — со статусом по каждому.
2. Одна и та же вакансия не приходит дважды (между источниками и между запусками).
3. По каждой отсеянной вакансии известно, каким правилом или каким решением LLM.
4. Изменение промпта/модели/профиля можно оценить командой `eval` на размеченном наборе с
   доверительным интервалом и вердиктом «лучше / хуже / в пределах шума».
5. Тесты на парсинг источников, правила, дедупликацию, валидацию ответа LLM и форматирование
   Telegram проходят в CI без сети.

### Не-цели (v1)

- Хостинг интерфейса, авторизация, многопользовательность. Интерфейс запускается локально.
- Скрейпинг HTML. Только официальные API / RSS / публичные ATS-API.
- Автоотклик на вакансии, генерация сопроводительных писем.
- Работа над доступностью (a11y) в интерфейсе — явное решение владельца.
- Чтение Telegram-каналов через user-аккаунт (MTProto) — исключено: условия Telegram
  запрещают использовать контент для AI-моделей (этап 0, `docs/sources.md`).
- Второй LLM-бэкенд (API-ключ) — только граница `score()`, см. §8.

## 2. Профиль кандидата

Профиль — это два документа, которые хранятся **в БД как версии** (`profile_versions`),
а не в репозитории:

- `resume` — факты (опыт, стек, проекты).
- `targets` — предпочтения и ограничения: целевые роли, домены, формат, гео, ставка, язык.

LLM оценивает раздельно «насколько кандидат подходит вакансии» (по фактам) и «насколько
вакансия подходит кандидату» (по целям). Смешивание делает оценку неинтерпретируемой.

**Личные параметры кандидата в репозитории не хранятся.** Страна проживания и форма работы,
минимальная ставка, рабочий часовой пояс, уровень английского, исключённые страны
работодателя, целевые роли — всё это лежит в `private/profile.yaml` (в `.gitignore`) и в БД;
в репозитории только `config/profile.example.yaml` с вымышленными значениями. Правила (§6.3)
и оси оценки (§7) читают значения оттуда. Общие принципы:

- только удалёнка;
- вакансии «только для резидентов/граждан страны X», где X — не страна кандидата, — отсев;
- неоднозначные требования (часовой пояс, язык) — риск в оценке, не отсев.

Первичный импорт: `aijobradar profile import private/resume.md private/targets.md`
(`private/` в `.gitignore`). Дальше — правка через интерфейс, каждая правка = новая версия.

## 3. Архитектура

```
GitHub Actions (cron 06:00 UTC)                      Локально (по требованию)
┌──────────────────────────────────────────┐        ┌─────────────────────────┐
│ fetch → normalize → dedup → rules →      │        │ Next.js UI  ⇄  FastAPI  │
│ score (claude -p) → select → deliver →   │        │  (127.0.0.1)            │
│ report                                   │        └───────────┬─────────────┘
└───────────────┬──────────────────────────┘                    │
                │            GitHub Actions (cron каждые 3 ч)   │
                │            ┌──────────────────────────┐       │
                │            │ labels: getUpdates →     │       │
                │            │ labels table             │       │
                │            └────────────┬─────────────┘       │
                ▼                         ▼                     ▼
            ┌─────────────────────────────────────────────────────┐
            │           Postgres (managed, Neon)                  │
            └─────────────────────────────────────────────────────┘
```

- **Postgres — единственное состояние.** Три писателя (пайплайн, сборщик разметки,
  интерфейс) исключают вариант «SQLite-файл в git».
  *Умолчание:* Neon (managed, TLS, рассчитан на доступ из интернета). Альтернатива —
  Postgres на своём сервере, открытый наружу (TLS + отдельная роль) — меняется только
  `DATABASE_URL`.
- **Пайплайн не зависит от локальной машины и сервера**: если интерфейс не запущен,
  дайджест всё равно приходит.
- **Публичность:** в логах Actions и `$GITHUB_STEP_SUMMARY` — только статусы и счётчики,
  без названий компаний и текстов вакансий (логи публичного репо видны всем; у части
  источников ToS запрещает перепубликацию).

### Структура репозитория

```
AiJobRadar/
  pyproject.toml, uv.lock
  src/aijobradar/
    sources/        # один модуль на источник + base.py (контракт адаптера)
    normalize.py
    dedup.py
    rules/          # engine.py + правила; параметры в config/rules.yaml
    scoring/        # prompt.py, schema.py, claude_cli.py, combine.py
    select.py
    telegram/       # client.py, render.py, labels_poller.py
    report.py
    eval/           # dataset.py, metrics.py, runner.py, report.py
    api/            # FastAPI для локального интерфейса
    db/             # SQLAlchemy-модели, alembic/
    cli.py          # typer: run, labels-poll, eval, profile, smoke
  prompts/scoring_v1.md
  config/sources.yaml, config/rules.yaml, config/scoring.yaml
  tests/            # + tests/fixtures/<source>/...
  web/              # Next.js (см. §10)
  docs/specs/, docs/sources.md
  .github/workflows/
```

Python 3.12, uv, httpx, pydantic v2, SQLAlchemy 2 + psycopg 3, alembic, feedparser,
rapidfuzz, typer, FastAPI; ruff, mypy, pytest, respx.

От hn-ai-monitor берётся форма (fetcher → filter → notifier, `.env.example`, цикл
`getUpdates`), не код: там нет Actions/LLM/тестов, ошибки Telegram проглатываются, а
`parse_mode: Markdown` без экранирования ломается на `_`/`*`/`[`.

## 4. Источники

Правило: официальный API / RSS / публичный ATS-API. Скрейпинг HTML — нет.
**Этап 0** — сверка ToS каждого источника с цитатами и ссылками в `docs/sources.md`;
источник, чьи условия не подтвердились, в v1 не включается.

Этап 0 выполнен 2026-09-30, подробности и ссылки — `docs/sources.md`.

| Источник | Способ | Версия | Примечание |
|---|---|---|---|
| Himalayas | публичный JSON API | v1 | структурные ограничения по локации/таймзоне |
| Jobicy | публичный JSON API | v1 | гео — свободный текст |
| We Work Remotely | RSS по категориям | v1 | риск Cloudflare из IP CI |
| RemoteOK | публичный JSON API | v1 | вырезать строку с IP-меткой (§4, контракт адаптера) |
| HN «Who is hiring» | Algolia + HN Firebase API | v1 | инкремент по `created_at_i` |
| Remocate | RSS | v1, эксперимент | без текста вакансии |
| Хабр Карьера, Djinni | RSS | v1 (этап 7) | Хабр: много локальных работодателей, нет полного текста |
| Greenhouse / Lever / Ashby | публичные job-board API по списку компаний | v2 | стартовый список предлагаю я |
| Remotive | публичный API | исключён | 16 вакансий на все категории, фильтр не работает |
| Telegram-каналы | MTProto user-сессия | исключены | условия Telegram запрещают использование контента для AI |
| getmatch, hh.ru, SuperJob, Geekjob, LinkedIn, Indeed, Wellfound, Glassdoor | — | исключены | нет официального пути / российские работодатели |

Требования из условий источников (обязательны для всех адаптеров и доставки):
- в каждом сообщении дайджеста — ссылка на страницу вакансии у источника и «via <Источник>»;
- не больше одного запроса на ленту в сутки;
- в публичном репозитории только синтетические фикстуры; реальные образцы — `private/raw/`;
- адаптер очищает описание от служебных вставок источника (RemoteOK: строка «mention the
  word … tag R<base64 IP>» — утечка IP и prompt-injection) до сохранения и до LLM.

### Контракт адаптера

```python
class SourceResult(BaseModel):
    source: str
    status: Literal["ok", "empty", "degraded", "failed"]
    items: list[RawJob]
    error: str | None
    http_status: int | None
    duration_ms: int
    invalid_items: int          # записи, не прошедшие валидацию
```

- `failed` — сеть/HTTP/исключение парсера на уровне ответа; `items == []`.
- `empty` — ответ корректен, записей 0. Три `empty` подряд → предупреждение в отчёте.
- `degraded` — `invalid_items / total > 20%` (признак смены формата источника).
- Адаптер никогда не бросает исключение наружу — только `SourceResult`.
- HN: тред месяца находится через Algolia (`author:whoishiring`), каждый запуск берёт
  только новые top-level комментарии; компания — эвристика по первой строке, остальное — LLM.

## 5. Модель данных (Postgres)

| Таблица | Ключевые поля |
|---|---|
| `runs` | id, kind (`daily`/`labels`/`eval`), started_at, finished_at, status (`ok`/`partial`/`failed`), counts jsonb, est_cost_usd |
| `source_runs` | run_id, source, status, item_count, invalid_items, http_status, error, duration_ms |
| `jobs` | id (uuid), content_hash, company_raw, company_norm, title_raw, title_norm, location_raw, remote_scope, salary_min/max/currency/period, posted_at, description_text, apply_url_canonical, fingerprint, first_seen_run, last_seen_at, state (`new`/`rejected`/`pending_score`/`scored`) |
| `job_sources` | job_id, source, source_job_id, source_url — одна вакансия, несколько источников |
| `rule_decisions` | job_id, run_id, rules_version, verdict (`pass`/`reject`), rule_ids text[], details jsonb |
| `profile_versions` | id, created_at, resume_md, targets_md, note |
| `llm_scores` | job_id, content_hash, prompt_version, model, profile_version_id, status, axes jsonb, final_score, why_fit, main_risk, gaps, input_tokens, output_tokens, est_cost_usd, raw_output, error |
| `deliveries` | job_id, run_id, slot (`top`/`explore`), position, tg_message_id |
| `labels` | id, job_id, label (`fit`/`no_fit`), reason (`geo`/`stack`/`seniority`/`money`/`not_interesting`/`other`/null), source (`telegram`/`ui`/`seed`), created_at |
| `review_queue` | job_id, kind (`explore`/`rule_rejected_sample`), added_run_id |
| `eval_runs` | id, candidate (prompt/model/profile), baseline, split, metrics jsonb, verdict, created_at |
| `kv` | key, value — например, `telegram_update_offset` |

`llm_scores` уникален по `(content_hash, prompt_version, model, profile_version_id)` — это
и кэш для eval, и гарантия «не платить дважды за одно и то же».

## 6. Конвейер

### 6.1 Normalize

Приведение к `NormalizedJob`: очистка HTML в текст, нормализация компании (регистр,
пунктуация, суффиксы `inc/ltd/llc/gmbh/ооо`), заголовка (регистр, «(remote)», пунктуация —
грейд сохраняется), зарплаты (валюта, период), `posted_at` в UTC, канонизация URL отклика
(удаление `utm_*`/`ref`, извлечение ID из Greenhouse/Lever/Ashby).

### 6.2 Dedup (между источниками и запусками)

1. `(source, source_job_id)` уже есть → обновить `last_seen_at`, дальше не идёт.
2. Совпал `apply_url_canonical` → та же вакансия, добавить строку в `job_sources`.
3. Тот же `company_norm` и `rapidfuzz.token_sort_ratio(title_norm) ≥ 92` среди вакансий,
   виденных за последние 60 дней → та же вакансия (это же ловит перепосты).
   Не `token_set_ratio`: он даёт 100 для «backend engineer» / «senior backend engineer» и
   склеил бы разные грейды. «full stack» / «full-stack» → «fullstack» до сравнения.
4. Изменение текста вакансии повторную отправку не вызывает (YAGNI).

Порог и окно — в конфиге; тесты фиксируют поведение на синтетических дублях.

### 6.3 Rules (дешёвые, детерминированные)

Каждое правило: `id`, описание, версия набора (`rules_version`), вердикт с причиной. Правила
только **отсекают явное**; всё неоднозначное уходит в LLM.

| ID | Отсекает | Не отсекает |
|---|---|---|
| `R-NOT-REMOTE` | явный onsite / hybrid | «remote-first, optional office» |
| `R-GEO-COUNTRY-ONLY` | «US only», «must be located in US/Canada», структурное ограничение локации только странами/регионами, куда не входит страна кандидата (US, Canada, LATAM, APAC…) | «Europe», «EMEA», «Worldwide», пусто |
| `R-GEO-RESIDENCY` | «EU residents only», «right to work in UK/EU required», «US citizens / security clearance» | «EU timezone preferred» |
| `R-EMPLOYER-COUNTRY` | явные признаки работодателя из страны, исключённой в профиле (наборы маркеров по странам в конфиге: оформление по местному трудовому кодексу, зарплата в местной валюте, форма юрлица) | язык описания сам по себе |
| `R-RATE-FLOOR` | указанный **потолок** ставки ниже минимальной ставки из профиля с запасом 10% (пересчёт по фиксированным курсам из конфига) | отсутствие ставки |
| `R-NON-ENG-ROLE` | не инженерные роли по заголовку (sales, marketing, recruiter, designer, account executive…) | инженерные роли любого грейда |
| `R-STALE` | `posted_at` старше 30 дней | нет даты |

Грейд и формат занятости правилами не проверяются.

Аудит правил: каждый день 5 случайных отсеянных вакансий кладутся в `review_queue`
(`rule_rejected_sample`) — их можно разметить в интерфейсе, это даёт оценку ложных отсевов.

### 6.4 Score — см. §7

### 6.5 Select

- `top`: до 5 вакансий с `final_score ≥ threshold` (порог в `config/scoring.yaml`),
  по убыванию. Меньше 5 выше порога → отправляется меньше, 0 → статус «подходящих нет».
- `explore`: 2 случайные вакансии из оценённых ниже порога — в Telegram с пометкой 🎲.
  Нужны для оценки того, что ранжирование пропускает (иначе меряется только precision@5).

### 6.6 Deliver (Telegram)

- `parse_mode: HTML`, всё пользовательское и LLM-содержимое через `html.escape`.
- Сообщения: заголовок дня → по одному на вакансию (компания, роль, ставка, ссылка на
  страницу у источника + «via <Источник>» для каждого источника — требование их условий,
  `why_fit` / `main_risk` / `gaps`, кнопки 👍 👎) → подвал со статусами.
- `callback_data`: `l:<job_id_short>:<1|0>` (≤ 64 байт).
- Ограничение 4096 символов на сообщение — обрезка текстов LLM с «…», тест на это.
- Ошибка отправки не проглатывается: ретрай ×3 с backoff, затем прогон `failed`.

### 6.7 Report и исходы

Подвал дайджеста и `$GITHUB_STEP_SUMMARY` (только счётчики):

```
Источники: himalayas ok 42 · remotive ok 17 · wwr ⚠️ degraded 8 (31% невалидных) · remoteok ❌ failed: HTTP 503
Отбор: 89 новых → 61 отсеяно правилами (R-GEO-COUNTRY-ONLY 34, R-NOT-REMOTE 12, …) → 28 в LLM
LLM: 26 ok · 1 invalid_output · 1 skipped_budget · ≈$0.41 (оценка по API-ценам)
Выдача: 3 выше порога + 2 🎲
```

| Ситуация | Статус прогона | Exit code | Telegram |
|---|---|---|---|
| Всё ок | `ok` | 0 | дайджест |
| Часть источников `failed`/`degraded`, часть LLM `invalid_output` | `partial` | 0 | дайджест + ⚠️ строки |
| Подходящих 0 | `ok` | 0 | «Сегодня подходящих нет» + статусы |
| Все источники `failed` | `failed` | 1 | сообщение об аварии + статусы |
| БД недоступна | `failed` | 1 | сообщение об аварии (без дедупа отправлять нельзя) |
| Telegram не принял | `failed` | 1 | — (видно по красному прогону в Actions) |

## 7. LLM-оценка

### 7.1 Ответ модели (JSON Schema, строгая валидация pydantic)

| Поле | Тип | Смысл |
|---|---|---|
| `skills_match` | 0–4 | стек вакансии против фактов резюме |
| `domain_match` | 0–4 | домен вакансии против целевых доменов из профиля |
| `seniority_fit` | 0–4 | требования к опыту против фактического опыта |
| `target_fit` | 0–4 | совпадение с целевыми ролями |
| `geo_eligible` | yes / no / unclear | может ли кандидат из своей страны (контрактор/EOR) быть нанят |
| `employer_country_excluded` | yes / no / unclear | работодатель/выплаты из страны, исключённой в профиле |
| `timezone_risk` | low / medium / high | требования по часам против часового пояса из профиля |
| `english_risk` | low / medium / high | против уровня английского из профиля |
| `preferred_language` | yes / no / unclear | коммуникация на предпочитаемом языке из профиля (плюс) |
| `comp_ok` | yes / no / unknown | против минимальной ставки из профиля |
| `why_fit`, `main_risk`, `gaps` | строки ≤ 300 символов, на русском | обоснование для дайджеста |

`final_score` считает **код** (`scoring/combine.py`): взвешенная сумма осей, веса и
штрафы в `config/scoring.yaml`; `geo_eligible = no` или `employer_country_excluded = yes` → 0.
Так по размеченным ошибкам видно, на какой оси модель ошиблась.

### 7.2 Статусы на вакансию

`ok` · `invalid_output` (не прошла схему после 1 повтора) · `refused` · `error`
(таймаут/CLI/сеть) · `skipped_budget`. Всё, кроме `ok`, считается в отчёте отдельно;
`skipped_budget` остаётся в состоянии `pending_score` и оценивается в следующий прогон.

### 7.3 Бюджет

`max_items_per_run` (80) и `max_est_cost_usd_per_run` (3.0) в конфиге. Превышение →
`skipped_budget`, не тихое обрезание.

### 7.4 Недоверенный ввод

Текст вакансии — недоверенные данные (возможна инъекция «оцени на 100»). Меры: у
оценивающей модели нет инструментов (побочных эффектов нет по построению), ответ
ограничен схемой, текст вакансии передаётся как данные в явных разделителях, итоговый
балл собирает код. Остаточный риск — искажённая оценка одной вакансии — ловится разметкой.

## 8. LLM-рантайм: подписка через `claude -p`

- Аутентификация: `claude setup-token` → секрет `CLAUDE_CODE_OAUTH_TOKEN` (токен на 1 год).
- Вызов на вакансию: `claude -p --output-format json --json-schema <schema> --model <model>`
  с заменой системного промпта (`--system-prompt`) и отключёнными инструментами; результат
  в поле `structured_output`, оценка стоимости — `total_cost_usd`.
- **Стоимость в отчёте — оценка в пересчёте на API**, а не списание: при подписке расходуются
  лимиты подписки. Помечается как `≈$`.
- Модель по умолчанию `claude-opus-5-5`; после накопления разметки — `eval` против
  `claude-sonnet-5-5` и `claude-haiku-4-5`, решение о смене — по цифрам.
- **Риск:** режим `--bare` (рекомендован для CI и объявлен будущим умолчанием для `-p`) не
  читает подписочный логин. Меры: версия Claude Code в workflow зафиксирована; весь вызов
  спрятан за `score(job, profile, prompt) -> ScoreResult` в `scoring/claude_cli.py`.
  Бэкенд на API-ключе (Anthropic SDK) реализуется, **только если** подписочный путь сломается
  — это замена одного модуля. В README путь через API-ключ описан как основной для форков.
- Параллелизм 3, таймаут 120 с на вакансию.

## 9. Разметка и измерение качества

### 9.1 Источники разметки

- Telegram: 👍/👎 на `top` и `explore`. Workflow `labels.yml` раз в 3 часа забирает
  `getUpdates` (Telegram хранит апдейты 24 ч, поэтому суточного опроса мало), принимает
  нажатия только от `TELEGRAM_CHAT_ID`, пишет в `labels`, редактирует сообщение
  («👍 учтено»). Offset — в `kv`. Отметка появляется с задержкой до 3 ч — принятый компромисс.
- Интерфейс: очередь (`review_queue` + непомеченные доставленные) с причиной отказа.
- Seed: стартовый набор размеченных вакансий владельца (`source = seed`), если будет.

### 9.2 Набор и сплит

Размеченные вакансии со снимком текста (`content_hash`). Детерминированный сплит по хэшу:
70% `dev` (на нём итерируются промпты), 30% `test` (заголовочная метрика; защищает от
подгонки промпта под маленький набор).

### 9.3 Метрики

- **Онлайн (из доставок):** precision@k по дням; доля 👍 среди 🎲 — оценка того, сколько
  хорошего остаётся ниже порога; доля 👍 среди `rule_rejected_sample` — ложные отсевы правил.
- **Офлайн (`eval`):** ROC-AUC и pairwise accuracy `final_score` против меток; precision@5
  на симулированных днях; разбор по осям (для 👎 с причиной `geo` — что предсказал
  `geo_eligible`, и т. д.).
- **Значимость:** парный бутстрап по вакансиям, 95% ДИ разности кандидат − база; вердикт
  «лучше / хуже / в пределах шума». При < 100 метках отчёт прямо пишет, что выборка мала.

### 9.4 Прогон

`aijobradar eval --prompt prompts/scoring_v2.md --model claude-opus-5-5 --baseline <eval_run_id|current>`
→ оценки берутся из кэша `llm_scores`, недостающие досчитываются → markdown-отчёт +
строка в `eval_runs` (видно в интерфейсе).

`eval.yml` запускается на PR, меняющих `prompts/**`, `src/aijobradar/scoring/**` или
`config/scoring.yaml`, и комментирует PR **только агрегатами** (метрики, ДИ, вердикт).

## 10. Локальный интерфейс

### 10.1 Стек (по `~/.claude/frontend-rules.md`)

Next.js App Router, React, TypeScript `strict`, yarn, CSS Modules + дизайн-токены в
`globals.css`, папка на компонент, ESLint `max-lines: 200` / `complexity: 16`, алиас `@/*`,
state-ladder (persistence hook → domain hook → context). Без работы над a11y (решение
владельца); нативные элементы (`button`, `a`, `table`) используются как обычно.

### 10.2 API (FastAPI, `127.0.0.1:8000`)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/runs`, `/api/runs/{id}` | прогоны, статусы источников, отсевы по правилам, стоимость |
| GET | `/api/jobs/{id}` | вакансия, источники, решения правил, оценки по версиям, метки |
| GET | `/api/review-queue?kind=` | очередь разметки |
| POST | `/api/labels` | поставить метку |
| GET | `/api/eval-runs`, `/api/eval-runs/{id}` | результаты оценки качества |
| GET | `/api/eval-runs/compare?a=&b=` | сравнение двух прогонов, расхождения по вакансиям |
| GET/POST | `/api/profile-versions` | просмотр и создание версии профиля |

Контракт: TS-типы генерируются из OpenAPI FastAPI (`openapi-typescript`) в
`web/src/types/api.gen.ts`; преобразование snake_case ↔ camelCase — единственное место
`web/src/lib/contract.ts`; HTTP — один модуль `web/src/lib/api.ts` с общим `request<T>()`.

### 10.3 Экраны

1. **Разметка** — очередь, текст вакансии, оси и обоснование LLM, 👍/👎 + причина, горячие
   клавиши (через `onKeyDown`, без `addEventListener`).
2. **Прогоны** — список и детали: источники, воронка отбора, отсевы по `rule_id`, статусы LLM.
3. **Оценка качества** — список `eval_runs`, метрики с ДИ, сравнение двух прогонов.
4. **Профиль** — текущая версия резюме/целей, создание новой версии.

Безопасность: текст вакансий — недоверенный контент, рендерится только как текст
(никакого `dangerouslySetInnerHTML`), внешние ссылки с `rel="noopener noreferrer"`;
API слушает только `127.0.0.1`.

Запуск: `make dev` поднимает API и `yarn dev`; `DATABASE_URL` из локального `.env`.

## 11. Конфигурация, секреты, workflows

GitHub Secrets: `CLAUDE_CODE_OAUTH_TOKEN`, `DATABASE_URL`, `TELEGRAM_BOT_TOKEN` (отдельный
бот, не от hn-ai-monitor — иначе два потребителя `getUpdates` крадут апдейты друг у друга),
`TELEGRAM_CHAT_ID`. Локально — те же в `.env` (в `.gitignore`), шаблон `.env.example`.

| Workflow | Триггер | Что делает |
|---|---|---|
| `daily.yml` | `cron: "0 6 * * *"` (время запуска задаётся в workflow) + ручной запуск | `alembic upgrade head` → `aijobradar run` |
| `labels.yml` | `cron: "15 */3 * * *"` | `aijobradar labels-poll` |
| `ci.yml` | push / PR | ruff, mypy, pytest (Postgres service container), `yarn lint`, `yarn typecheck`, `yarn build`, vitest |
| `eval.yml` | PR с изменениями промпта/скоринга + ручной | `aijobradar eval`, комментарий с агрегатами |

`concurrency` на каждый workflow; версия Claude Code зафиксирована.
Риск: GitHub отключает cron в публичном репо после 60 дней без активности, а состояние в
БД коммитов не создаёт → keepalive-механизм (конкретный способ выбирается на этапе 4 и
проверяется).

## 12. Тестирование

- **Парсинг:** синтетические фикстуры, повторяющие схему реальных ответов из `private/raw/`
  (реальные записи в публичный репозиторий не попадают — условия источников), + испорченные
  варианты (пропуск полей, смена типа) → проверка `ok` / `degraded` / `failed`.
- **Правила:** табличные тесты на каждое правило, позитивные и негативные случаи, включая
  русские тексты и пограничные («EU timezone preferred» не режется).
- **Дедуп:** одна вакансия из двух источников, `utm`-варианты URL, перепост, похожие
  заголовки у разных компаний.
- **Скоринг:** граница `claude -p` мокается на уровне subprocess; фикстуры ответов:
  валидный, невалидный JSON, нарушение схемы, отказ, таймаут → правильный статус;
  `combine.py` — юнит-тесты.
- **Telegram:** экранирование `< & > _ * [`, лимит 4096, формат `callback_data`.
- **Исходы:** матрица §6.7 — каждая строка тестом (exit code + текст статуса).
- **БД:** интеграционные тесты против Postgres в CI (service container).
- **Web:** vitest на `contract.ts` и чистые функции `lib/`.
- Живая сеть — только `aijobradar smoke` (ручной запуск), не в CI.

## 13. Этапы

0. **Источники:** сверка ToS → `docs/sources.md` с цитатами; запись фикстур; решение по
   русскоязычным источникам.
1. **Каркас:** uv-проект, схема БД + alembic, адаптеры Himalayas / Jobicy / WWR,
   normalize, dedup, тесты, `ci.yml`.
2. **Правила** + журнал отсевов + выборка в `review_queue`.
3. **Скоринг** через `claude -p`: схема, статусы, бюджет, оценка стоимости; импорт профиля.
4. **Telegram + `daily.yml` + `labels.yml`**, отчёт и матрица исходов, keepalive.
5. **Eval:** набор, метрики, бутстрап, CLI, `eval.yml`.
6. **Интерфейс:** FastAPI + Next.js, четыре экрана.
7. **Остальные источники** (RemoteOK, HN, Remocate, Хабр Карьера, Djinni),
   затем v2 — ATS-список компаний.

Каждый этап заканчивается зелёным CI и коротким демо; коммиты — только по явной команде.

## 14. Открытые вопросы

1. **БД:** Neon (умолчание) или Postgres на своём сервере.
2. **Стартовая разметка:** папка `~/Documents/Claude/Projects/AISearhJobs/` недоступна
   (macOS не выдала приложению доступ к «Документам»). Если там размеченные вакансии —
   скопировать в `AiJobRadar/private/seed/`.
3. **Стартовый список компаний** для Greenhouse/Lever/Ashby (v2) — предлагаю я, владелец правит.
