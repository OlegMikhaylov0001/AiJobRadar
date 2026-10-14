# AiJobRadar

Ежедневный дайджест удалённых вакансий: официальные API и RSS → дедупликация →
детерминированные правила → LLM-оценка через `claude -p` → доставка в Telegram.

Стек: Python (uv), PostgreSQL (Neon) + Alembic, GitHub Actions, Telegram Bot API.
Дизайн и решения: [`docs/specs/2026-09-29-aijobradar-design.md`](docs/specs/2026-09-29-aijobradar-design.md).

## Локальная установка

```bash
uv sync
cp .env.example .env            # заполнить DATABASE_URL (и Telegram, если нужна доставка)
uv run aijobradar db upgrade
uv run aijobradar profile import private/resume.md private/targets.md
cp config/profile.example.yaml private/profile.yaml   # профиль для правил, отредактировать под себя
uv run aijobradar run --no-deliver
```

Каталог `private/` и файл `.env` в git не попадают. Личные данные (резюме, цели,
профиль правил, токены) хранятся только там и в секретах GitHub.

Команды CLI: `db upgrade`, `run [--deliver|--no-deliver] [--migrate|--no-migrate] [--limit N]`, `score`,
`profile import`, `rules recheck`, `labels-poll`.

## Ежедневный запуск (GitHub Actions)

Workflow `daily.yml` запускается по cron в 06:00 UTC и вручную; `labels.yml` раз в три часа
забирает оценки из Telegram (кнопки под вакансиями). Оба используют группы `concurrency`,
а CLI берёт advisory lock в БД, так что параллельные прогоны не пересекаются. У `run`/`score`
и у `labels-poll` разные ключи блокировки: опрос меток в 06:15 не мешает ежедневному прогону.

### Секреты

| Секрет | Что это |
| --- | --- |
| `DATABASE_URL` | direct-строка подключения Neon (хост без `-pooler`: advisory lock и миграции не работают через пулер) |
| `TELEGRAM_BOT_TOKEN` | токен отдельного бота (на бота — один потребитель `getUpdates`) |
| `TELEGRAM_CHAT_ID` | id приватного чата, куда бот пишет |
| `CLAUDE_CODE_OAUTH_TOKEN` | токен из `claude setup-token` |
| `PROFILE_YAML` | содержимое `private/profile.yaml` целиком |

Значение вводится интерактивно, не в командной строке (иначе оно попадёт в историю шелла):

```bash
gh secret set DATABASE_URL --repo <owner>/<repo>
gh secret set TELEGRAM_BOT_TOKEN --repo <owner>/<repo>
gh secret set TELEGRAM_CHAT_ID --repo <owner>/<repo>
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo <owner>/<repo>
gh secret set PROFILE_YAML --repo <owner>/<repo> < private/profile.yaml
```

### Первичная подготовка БД

Один раз локально, с `DATABASE_URL` на ту же direct-строку Neon:

```bash
uv run aijobradar db upgrade
uv run aijobradar profile import private/resume.md private/targets.md
```

Импорт профиля в Neon (`profile import`) обязателен до первого `run --deliver`: без профиля в БД
оценка останавливается с ошибкой «Нет профиля в БД».

Дальше миграции применяются сами: `daily.yml` запускает `run --deliver --migrate` (миграции идут
после проверки доступности БД; если БД недоступна или миграция упала, в Telegram уходит
сообщение о сбое и прогон завершается с кодом 1), а `labels.yml` перед опросом выполняет
`db upgrade`; обе миграции берут общий advisory lock, так что одновременные `daily` и `labels`
не выполняют один и тот же DDL дважды. Сбой БД в любой момент — на проверке доступности или
посреди прогона (lock, профиль, сбор, оценка, выдача) — печатается одной строкой с именем класса
ошибки, без трейсбека и без сообщения драйвера, поэтому хост в публичный лог не попадает;
`run --deliver` при этом шлёт в Telegram сообщение о сбое и завершается с кодом 1.

### Ручной запуск

```bash
gh workflow run daily.yml --repo <owner>/<repo> -f limit=3
```

`limit` — максимум вызовов модели за прогон; пустое значение берёт лимит из конфига.
Первые прогоны лучше гнать с небольшим `limit`.

### Что попадает в публичные логи

Только статусы и счётчики. Названия компаний, заголовки и тексты вакансий уходят
исключительно в приватный чат Telegram. Токены в логи не печатаются. Ошибки в отчёте прогона
(лог Actions и step summary) — только коды HTTP, имена классов исключений, SQLSTATE и наши
собственные фиксированные тексты: текст исключения может цитировать ответ источника или
значение из БД, поэтому он не печатается.

### Выдача: не больше одного раза

Каждая вакансия приходит в чат не больше одного раза. Строка `deliveries` коммитится в
состоянии `pending` до отправки и становится `sent` после ответа Telegram. Если Telegram
точно не принял сообщение (ответил 4xx или запрос не ушёл), строка удаляется и вакансия
уйдёт в следующей выдаче. Если исход неизвестен (таймаут чтения, обрыв после отправки, 5xx —
его мог вернуть прокси, потерявший ответ Telegram, — или непонятный ответ 200), `sendMessage`
не повторяется, строка остаётся в состоянии `unknown`, вакансия больше автоматически не
отправляется — такие вакансии проверяются вручную (см. ниже). Строка, оставшаяся `pending`
после прогона, который умер посреди отправки (убит, или не записался `sent` после ответа
Telegram), в начале следующей выдачи переводится в `unknown`. Счётчиков два:
`runs.counts.delivered_unknown` — новые `unknown` этого прогона (его неоднозначные отправки и
переведённые им `pending`), `runs.counts.unresolved_unknown` — все неразобранные `unknown` в
БД; «N не подтверждено» в отчёте показывает второе.

### Неподтверждённые доставки

`unknown` значит: прогон начал отправку вакансии, но не знает, дошла ли она до чата. Повторно
такая вакансия не отправляется. Пока в БД есть хоть одна строка `unknown` (из любого прогона),
каждая выдача напоминает о них: строка `⚠️ Не подтверждено доставок: N — проверьте вручную
(README)` в подвале Telegram, «· N не подтверждено» в отчёте прогона и step summary,
`runs.counts.unresolved_unknown`. Напоминание исчезает, только когда строки разобраны.

Как разобрать (в SQL-консоли Neon):

```sql
SELECT d.id, d.sent_at, j.title_raw, j.company_raw
FROM deliveries d JOIN jobs j ON j.id = d.job_id
WHERE d.state = 'unknown';
```

Найти вакансию в чате по дате и названию, затем:

- дошла — `UPDATE deliveries SET state = 'sent' WHERE id = …;`
- не дошла — `DELETE FROM deliveries WHERE id = …;` (вакансия снова сможет попасть в выдачу,
  если ещё свежая).

### Keepalive

GitHub отключает scheduled-workflows после 60 дней без активности в default-ветке, а повторное
включение workflow (`gh workflow enable`) активностью не считается. Поэтому отдельный
`keepalive.yml` раз в неделю (понедельник, 05:30 UTC) смотрит на дату последнего коммита и,
если он старше 45 дней, пушит пустой коммит `chore: keepalive (…)` от `github-actions[bot]`
(права `contents: write`, без секретов). 45, а не ближе к 60: до отключения остаются минимум
две еженедельные попытки, даже если GitHub пропустит один scheduled-запуск. Цена — не больше
одного пустого коммита примерно раз в 45 дней, и только в тихом репозитории. Если default-ветка защищена от прямых пушей, этому
workflow нужно разрешение на push.

### Форки

Для форка основной путь — вызов модели по API-ключу вместо `claude -p` с OAuth-токеном
(дизайн-документ, §8). В этом этапе описан только вариант с `CLAUDE_CODE_OAUTH_TOKEN`.
