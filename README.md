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

Команды CLI: `db upgrade`, `run [--deliver|--no-deliver] [--limit N]`, `score`,
`profile import`, `rules recheck`, `labels-poll`.

## Ежедневный запуск (GitHub Actions)

Workflow `daily.yml` запускается по cron в 06:00 UTC и вручную; `labels.yml` раз в три часа
забирает оценки из Telegram (кнопки под вакансиями). Оба используют группы `concurrency`,
а CLI берёт advisory lock в БД, так что параллельные прогоны не пересекаются.

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

Дальше `daily.yml` сам применяет миграции (`db upgrade`) перед каждым прогоном.

### Ручной запуск

```bash
gh workflow run daily.yml --repo <owner>/<repo> -f limit=3
```

`limit` — максимум вызовов модели за прогон; пустое значение берёт лимит из конфига.
Первые прогоны лучше гнать с небольшим `limit`.

### Что попадает в публичные логи

Только статусы и счётчики. Названия компаний, заголовки и тексты вакансий уходят
исключительно в приватный чат Telegram. Токены в логи не печатаются.

### Keepalive

GitHub отключает scheduled-workflows после 60 дней без активности в репозитории.
Последний шаг `daily.yml` (`gh workflow enable`) выполняется всегда и заново включает
оба workflow.

### Форки

Для форка основной путь — вызов модели по API-ключу вместо `claude -p` с OAuth-токеном
(дизайн-документ, §8). В этом этапе описан только вариант с `CLAUDE_CODE_OAUTH_TOKEN`.
