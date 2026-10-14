# Этап 4 — Telegram, ежедневный прогон в GitHub Actions — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** каждый день в 06:00 UTC GitHub Actions прогоняет сбор → правила → оценку против БД Neon и присылает в Telegram до 5 вакансий выше порога плюс 2 случайные ниже порога (🎲) с кнопками 👍/👎; нажатия раз в 3 часа собираются в таблицу `labels`; исходы прогона честные по матрице §6.7; cron не отключается через 60 дней.

**Architecture:** выбор (`select.py`) и рендер (`telegram/render.py`) — чистые функции; HTTP к Telegram — только `telegram/client.py` (ретраи, токен никогда не попадает в ошибки); доставка (`telegram/deliver.py`) пишет строку `deliveries` до отправки и коммитит её после, поэтому вакансия не уходит дважды. Оценка помечает вакансию ссылкой на свою оценку (`jobs.llm_score_id`) — выбор не зависит от того, взята оценка из кэша или посчитана сейчас. CLI собирает всё по матрице исходов; workflows только вызывают CLI.

**Tech Stack:** как в этапах 1–3 (Python 3.12, uv, httpx, SQLAlchemy 2, Alembic, pydantic v2, typer, pytest + respx, ruff, mypy --strict) + Telegram Bot API (HTTP, `parse_mode: HTML`) + GitHub Actions + Neon Postgres.

**Spec:** `docs/specs/2026-09-29-aijobradar-design.md` (§3 архитектура и публичность логов, §5 `deliveries`/`labels`/`kv`, §6.5 Select, §6.6 Deliver, §6.7 Report и исходы, §8 рантайм, §11 секреты и workflows, §12 тесты), `docs/plans/stage1-followups.md` (разделы «До этапа 4» и «После этапа 3»).

## Global Constraints

- Всё из этапов 1–3 в силе: uv, ruff (100), `mypy --strict` на `src/`, тесты без сети, без реального `claude` и без реального Telegram (respx); исполнители не коммитят; `docs/` и `README.md` правит только задача 9.
- **Никаких личных данных в отслеживаемых файлах.** Токены, chat id, `DATABASE_URL`, профиль правил — только секреты GitHub, `.env` и `private/` (всё в `.gitignore`). Тесты — вымышленные значения (`123:TEST`, chat id `42`).
- **Публичные логи Actions и `$GITHUB_STEP_SUMMARY`** — только статусы и счётчики, без названий компаний, заголовков и текстов вакансий (§3). Названия и тексты — только в личный чат Telegram.
- **Токен бота никогда не печатается и не попадает в текст исключений** (он часть URL Bot API): ошибки Telegram — имя метода + код, без URL и без `description` от Telegram (она может цитировать наш текст).
- Telegram: `parse_mode: HTML`, всё стороннее и LLM-содержимое через `html.escape`; одно сообщение ≤ 4096 символов; `callback_data` ≤ 64 байт (§6.6).
- Ошибка отправки не проглатывается: ретрай ×3 с backoff, затем прогон `failed`, код 1 (§6.6).
- Строки CLI, отчёта и сообщений Telegram — на русском; код и комментарии — на английском.
- Версия Claude Code в workflow зафиксирована: `@anthropic-ai/claude-code@2.1.287` (минимум для `claude-opus-5-5` — 2.1.280).

## Уточнения к спецификации (вносит этот план)

1. **`jobs.llm_score_id`** — ссылка на оценку, по которой вакансия стала `scored`: собственная строка, строка из кэша, строка «близнеца» или строка конкурента после конфликта. Закрывает пункт follow-ups «у вакансии из кэша нет своей строки». FK с `use_alter` (цикл `jobs ↔ llm_scores`).
2. **`callback_data` = `l:<delivery_id>:<1|0>`** — id строки `deliveries` (bigint), а не короткий id вакансии: короче, однозначен, ведёт и к вакансии, и к прогону.
3. **Порог и выдача** — в `config/scoring.yaml`: `threshold` (60, стартовое значение до eval этапа 5), `top_n` 5, `explore_n` 2, `max_age_days` 14 (старее по `coalesce(posted_at, first_seen_at)` в выдачу не идёт). Каждая вакансия доставляется не более одного раза (`deliveries.job_id` уникален).
4. **`run` по умолчанию не отправляет в Telegram** (`--no-deliver`); `daily.yml` вызывает `run --deliver`. Локальные прогоны против dev-БД не требуют токенов.
5. **Профиль правил в CI** — секрет `PROFILE_YAML`, workflow пишет его в `private/profile.yaml` с правами 600. LLM-профиль уже в БД (`profile import` один раз против Neon).
6. **Блокировка прогона** — `pg_try_advisory_lock` на время `run`/`score`/`labels-poll`: второй одновременный прогон (cron + ручной) выходит с кодом 1 и сообщением, не тратя вызовы модели (пункт follow-ups).
7. **Окно Himalayas** — от последнего успешного (`ok`) прогона Himalayas + 6 ч запаса, не меньше `lookback_hours` и не больше `max_lookback_hours` (96) (пункт follow-ups).
8. **Keepalive** — шаг в `daily.yml` после прогона (`if: always()`): `gh workflow enable` для `daily.yml` и `labels.yml` (разрешение `actions: write`, без коммитов в репозиторий). Проверить удастся только по истечении 60 дней; страховка — отсутствие дайджеста заметно сразу.
9. **`labels-poll`** принимает нажатия только из `TELEGRAM_CHAT_ID`; повтор того же нажатия не создаёт вторую метку (`labels.source_ref` = id callback-запроса, уникален); `answerCallbackQuery` — по возможности (запрос старше ~15 с Telegram отклоняет, это не ошибка прогона).

## Файловая структура

```
config/scoring.yaml                        # + threshold, top_n, explore_n, max_age_days
config/sources.yaml                        # + himalayas.max_lookback_hours
src/aijobradar/config.py                   # + ScoringConfig fields, HimalayasConfig field, Settings telegram
src/aijobradar/db/models.py                # + Job.llm_score_id, Delivery, Label, Kv
src/aijobradar/db/alembic/versions/0004_delivery.py
src/aijobradar/store.py                    # + mark_scored, deliveries, labels, kv, sources, last success
src/aijobradar/scoring/engine.py           # sets llm_score_id via store.mark_scored
src/aijobradar/select.py                   # Pick, select_digest()
src/aijobradar/telegram/__init__.py
src/aijobradar/telegram/render.py          # pure: header, job, footer, crash, callback data
src/aijobradar/telegram/client.py          # TelegramClient, TelegramError — the only Telegram HTTP
src/aijobradar/telegram/deliver.py         # deliver_digest(), DeliveryReport
src/aijobradar/telegram/labels.py          # poll_labels(), LabelsReport
src/aijobradar/sources/__init__.py         # build_adapters(..., himalayas_lookback_hours)
src/aijobradar/pipeline.py                 # + FetchReport.delivery, delivery line, himalayas_lookback()
src/aijobradar/cli.py                      # + --deliver, matrix, lock, summary, labels-poll, settings errors
.github/workflows/daily.yml, labels.yml
.env.example, README.md, docs/plans/stage1-followups.md
tests/test_delivery_store.py, test_select.py, test_telegram_render.py, test_telegram_client.py,
tests/test_telegram_deliver.py, test_labels_poll.py, test_cli_daily.py
tests/test_scoring_engine.py, test_pipeline.py, test_cli.py, test_config.py   # extended
```

## Review Focus

1. **HTML и спецсимволы в полях вакансии** (`<script>`, `&`, `<b>`, `_*[` в заголовке, компании, URL и заметках модели) — экранируются, сообщение валидно для `parse_mode: HTML`. Тест — задача 3.
2. **Telegram отвечает 429 с `retry_after` или 5xx / обрыв сети** — клиент ждёт и повторяет до 3 раз, затем `TelegramError` без токена и без URL. Тест — задача 4.
3. **Одна и та же вакансия в двух прогонах подряд** — во второй раз не выбирается и не отправляется. Тест — задачи 2 и 5.
4. **Нажатие из чужого чата, подделанные или мусорные `callback_data` (`l:999999:1`, `x`, пусто)** — игнорируются, offset всё равно двигается, прогон не падает. Тест — задача 7.
5. **Отправка падает посреди дайджеста** — уже отправленные вакансии записаны в `deliveries`, неотправленная — нет (завтра уйдёт снова), прогон `failed`, код 1. Тест — задача 5.

---

### Task 1: Схема доставки и ссылка вакансии на её оценку

**Files:**
- Create: `src/aijobradar/db/alembic/versions/0004_delivery.py`, `tests/test_delivery_store.py`
- Modify: `src/aijobradar/db/models.py`, `src/aijobradar/store.py`, `src/aijobradar/scoring/engine.py`, `tests/test_scoring_engine.py`

**Interfaces:**
- Consumes: существующие `Job`, `LlmScore`, `Run`, `JobSource`, `SourceRun`; `store.set_job_state`, `store.find_ok_score`, `store.record_llm_score`.
- Produces:
  - модели `Delivery`, `Label`, `Kv`; поле `Job.llm_score_id: int | None`;
  - `store.record_llm_score(...) -> int` (id новой строки; раньше `None`);
  - `store.mark_scored(session, jobs: Sequence[Job], score_id: int) -> None`;
  - `store.create_delivery(session, *, job_id: uuid.UUID, run_id: uuid.UUID, slot: str, position: int, now: datetime) -> Delivery` (flush, id доступен);
  - `store.add_label(session, *, job_id: uuid.UUID, label: str, source: str, source_ref: str | None, now: datetime) -> bool` (False — такая `source_ref` уже есть);
  - `store.get_kv(session, key: str) -> str | None`, `store.set_kv(session, key: str, value: str) -> None`;
  - `store.job_sources(session, job_id: uuid.UUID) -> list[JobSource]` (по `id`);
  - `store.last_source_success(session, source: str) -> datetime | None` — `max(runs.started_at)` среди `source_runs` с этим источником и `status = 'ok'`.

- [ ] **Step 1: Модели** — в `src/aijobradar/db/models.py`:

`Job` получает поле (после `state`):

```python
    # The score this job was marked `scored` by: its own, a cached one, or a twin's.
    llm_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_scores.id", ondelete="SET NULL", use_alter=True, name="fk_jobs_llm_score_id")
    )
```

В конец файла:

```python
class Delivery(Base):
    __tablename__ = "deliveries"
    # A job is sent at most once, ever.
    __table_args__ = (UniqueConstraint("job_id", name="uq_deliveries_job"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    slot: Mapped[str] = mapped_column(String(8))  # top | explore
    position: Mapped[int] = mapped_column(Integer)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Label(Base):
    __tablename__ = "labels"
    # One label per Telegram button press, however many times the update is re-read.
    __table_args__ = (UniqueConstraint("source_ref", name="uq_labels_source_ref"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(8))  # fit | no_fit
    reason: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))  # telegram | ui | seed
    source_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Kv(Base):
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
```

- [ ] **Step 2: Миграция** `src/aijobradar/db/alembic/versions/0004_delivery.py`:

```python
"""deliveries, labels, kv; jobs.llm_score_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("llm_score_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_jobs_llm_score_id", "jobs", "llm_scores", ["llm_score_id"], ["id"], ondelete="SET NULL"
    )
    # Jobs scored before this column existed point at their own ok row when they have one.
    op.execute(
        "UPDATE jobs SET llm_score_id = s.id FROM llm_scores s "
        "WHERE s.job_id = jobs.id AND s.status = 'ok' AND jobs.state = 'scored'"
    )
    op.create_table(
        "deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot", sa.String(8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_deliveries_job"),
    )
    op.create_table(
        "labels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(16), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_ref", name="uq_labels_source_ref"),
    )
    op.create_index("ix_labels_job_id", "labels", ["job_id"])
    op.create_table(
        "kv",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("kv")
    op.drop_index("ix_labels_job_id", table_name="labels")
    op.drop_table("labels")
    op.drop_table("deliveries")
    op.drop_constraint("fk_jobs_llm_score_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "llm_score_id")
```

- [ ] **Step 3: Падающие тесты** `tests/test_delivery_store.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Job, SourceRun
from aijobradar.models import RawJob
from aijobradar.normalize import normalize

NOW = datetime(2026, 10, 14, 6, tzinfo=UTC)


def _job(session: Session, n: int = 0) -> tuple[Job, object]:
    run = store.start_run(session, "fetch", NOW)
    raw = RawJob(
        source="himalayas",
        source_job_id=f"h-{n}",
        source_url=f"https://himalayas.example/jobs/{n}",
        title=f"Engineer {n}",
        company=f"Company {n}",
        description_html="<p>x</p>",
    )
    job = store.insert_job(session, normalize(raw), run.id, NOW)
    store.attach_source(session, job.id, raw, NOW)
    return job, run


def test_delivery_is_unique_per_job(session: Session) -> None:
    job, run = _job(session)
    first = store.create_delivery(session, job_id=job.id, run_id=run.id, slot="top", position=1, now=NOW)
    assert first.id is not None
    with pytest.raises(IntegrityError), session.begin_nested():
        store.create_delivery(session, job_id=job.id, run_id=run.id, slot="top", position=2, now=NOW)


def test_label_is_recorded_once_per_source_ref(session: Session) -> None:
    job, _ = _job(session)
    assert store.add_label(session, job_id=job.id, label="fit", source="telegram", source_ref="cb-1", now=NOW)
    assert not store.add_label(
        session, job_id=job.id, label="no_fit", source="telegram", source_ref="cb-1", now=NOW
    )


def test_kv_round_trip(session: Session) -> None:
    assert store.get_kv(session, "telegram_update_offset") is None
    store.set_kv(session, "telegram_update_offset", "10")
    store.set_kv(session, "telegram_update_offset", "11")
    assert store.get_kv(session, "telegram_update_offset") == "11"


def test_job_sources_are_listed(session: Session) -> None:
    job, _ = _job(session)
    assert [s.source for s in store.job_sources(session, job.id)] == ["himalayas"]


def test_last_source_success_ignores_failed_runs(session: Session) -> None:
    assert store.last_source_success(session, "himalayas") is None
    for hours_ago, status in ((30, "ok"), (5, "failed")):
        run = store.start_run(session, "fetch", NOW - timedelta(hours=hours_ago))
        session.add(
            SourceRun(
                run_id=run.id, source="himalayas", status=status, item_count=0,
                invalid_items=0, out_of_scope=0, http_status=None, error=None, duration_ms=1,
            )
        )
    session.flush()
    assert store.last_source_success(session, "himalayas") == NOW - timedelta(hours=30)
```

В `tests/test_scoring_engine.py` дописать (там уже есть `_setup`, `_scorer`, `_ok`, `_twins`, `CFG`, `NOW`):

```python
def test_scored_jobs_point_at_their_score(session: Session) -> None:
    run_id, profile, _ = _setup(session, 1)
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_ok()), now=NOW)
    job = session.scalars(select(Job)).one()
    row = session.scalars(select(LlmScore)).one()
    assert job.llm_score_id == row.id


def test_cached_and_twin_jobs_point_at_the_shared_score(session: Session) -> None:
    run_id, profile = _twins(session)
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_ok()), now=NOW)
    row = session.scalars(select(LlmScore)).one()
    assert {j.llm_score_id for j in session.scalars(select(Job))} == {row.id}
    for job in session.scalars(select(Job)):
        store.set_job_state(session, job, "pending_score")
        job.llm_score_id = None
    session.commit()
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_ok()), now=NOW)
    assert {j.llm_score_id for j in session.scalars(select(Job))} == {row.id}  # from the cache
```

Run: `uv run pytest tests/test_delivery_store.py tests/test_scoring_engine.py tests/test_db_schema.py` → FAIL.

- [ ] **Step 4: Store** — в `src/aijobradar/store.py` (импорты: `Delivery`, `Kv`, `Label`, `JobSource`, `SourceRun`, `Run` из `aijobradar.db.models`; `from sqlalchemy.dialects.postgresql import insert as pg_insert`, если ещё не импортирован; `Sequence` из `collections.abc`):

`record_llm_score` — вместо финального `session.add(...)`/`session.flush()`:

```python
    row = LlmScore(
        ...  # the same fields as now
    )
    session.add(row)
    session.flush()
    return row.id
```

и тип возврата `-> int`. Новые функции:

```python
def mark_scored(session: Session, jobs: Sequence[Job], score_id: int) -> None:
    for job in jobs:
        job.state = "scored"
        job.llm_score_id = score_id
    session.flush()


def create_delivery(
    session: Session, *, job_id: uuid.UUID, run_id: uuid.UUID, slot: str, position: int, now: datetime
) -> Delivery:
    delivery = Delivery(job_id=job_id, run_id=run_id, slot=slot, position=position, sent_at=now)
    session.add(delivery)
    session.flush()
    return delivery


def add_label(
    session: Session,
    *,
    job_id: uuid.UUID,
    label: str,
    source: str,
    source_ref: str | None,
    now: datetime,
) -> bool:
    inserted = session.execute(
        pg_insert(Label)
        .values(job_id=job_id, label=label, source=source, source_ref=source_ref, created_at=now)
        .on_conflict_do_nothing(constraint="uq_labels_source_ref")
        .returning(Label.id)
    ).first()
    return inserted is not None


def get_kv(session: Session, key: str) -> str | None:
    row = session.get(Kv, key)
    return row.value if row is not None else None


def set_kv(session: Session, key: str, value: str) -> None:
    session.execute(
        pg_insert(Kv).values(key=key, value=value).on_conflict_do_update(
            index_elements=[Kv.key], set_={"value": value}
        )
    )


def job_sources(session: Session, job_id: uuid.UUID) -> list[JobSource]:
    return list(
        session.scalars(select(JobSource).where(JobSource.job_id == job_id).order_by(JobSource.id))
    )


def last_source_success(session: Session, source: str) -> datetime | None:
    return session.scalar(
        select(func.max(Run.started_at))
        .join(SourceRun, SourceRun.run_id == Run.id)
        .where(SourceRun.source == source, SourceRun.status == "ok")
    )
```

- [ ] **Step 5: Движок** — в `src/aijobradar/scoring/engine.py` каждое место, где вакансия становится `scored`, ставит ссылку:
  - ветка кэша при планировании: `existing = store.find_ok_score(...)`; если не `None` — `store.mark_scored(session, [job], existing.id)` вместо `store.set_job_state(session, job, "scored")`;
  - `_write`: `row_id = store.record_llm_score(...)`; при `OK` — `store.mark_scored(session, [pending.job, *pending.followers], row_id)` (вместо цикла `set_job_state(..., "scored")`); ветка `score_failed` не меняется;
  - `_adopt_existing_score`: `store.mark_scored(session, [pending.job, *pending.followers], existing.id)`.

- [ ] **Step 6: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS (включая `test_models_match_migrations`: пустой дифф метаданных).

---

### Task 2: Выбор вакансий для дайджеста

**Files:**
- Create: `src/aijobradar/select.py`, `tests/test_select.py`
- Modify: `src/aijobradar/config.py` (`ScoringConfig`), `config/scoring.yaml`, `tests/test_scoring_schema_combine.py`

**Interfaces:**
- Consumes: `Job.llm_score_id`, `Delivery`, `LlmScore`, `store.job_sources` (задача 1).
- Produces:
  - `ScoringConfig.threshold: float` (0–100), `top_n: int` (0–10), `explore_n: int` (0–5), `max_age_days: int` (≥ 1);
  - `select.Pick` (frozen dataclass): `job: Job`, `score: LlmScore`, `slot: str` (`"top"`/`"explore"`), `sources: list[JobSource]`;
  - `select.select_digest(session, *, cfg: ScoringConfig, now: datetime, rng: random.Random) -> list[Pick]` — сначала `top` по убыванию балла, затем `explore`.

- [ ] **Step 1: Конфиг** — в `ScoringConfig` (после `max_description_chars`):

```python
    # Digest selection (spec §6.5). The threshold is a starting point until eval (stage 5).
    threshold: float = Field(60.0, ge=0, le=100)
    top_n: int = Field(5, ge=0, le=10)
    explore_n: int = Field(2, ge=0, le=5)
    max_age_days: int = Field(14, ge=1)  # older postings (by posted_at, else first seen) are not sent
```

В `config/scoring.yaml` после `max_description_chars`:

```yaml

# Digest (spec §6.5): up to top_n jobs scored >= threshold, plus explore_n random ones below it.
threshold: 60          # starting point; tune with eval (stage 5)
top_n: 5
explore_n: 2
max_age_days: 14       # postings older than this are never sent
```

В `tests/test_scoring_schema_combine.py` дописать:

```python
@pytest.mark.parametrize(
    ("field", "bad"), [("threshold", 101), ("threshold", -1), ("top_n", 11), ("explore_n", 6)]
)
def test_digest_settings_are_bounded(field: str, bad: float) -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    data[field] = bad
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(data)
```

- [ ] **Step 2: Падающие тесты** `tests/test_select.py`:

```python
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import load_scoring_config
from aijobradar.db.models import Job, LlmScore
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.select import select_digest

NOW = datetime(2026, 10, 14, 6, tzinfo=UTC)
CFG = load_scoring_config(Path(__file__).parent.parent / "config" / "scoring.yaml").model_copy(
    update={"threshold": 60.0, "top_n": 2, "explore_n": 1, "max_age_days": 14}
)


def _scored(session: Session, n: int, score: float, *, age_days: int = 1) -> Job:
    run = store.start_run(session, "fetch", NOW)
    profile, _ = store.add_profile_version(session, resume_md="R", targets_md="T", note=None, now=NOW)
    raw = RawJob(
        source="jobicy",
        source_job_id=str(n),
        source_url=f"https://jobicy.example/{n}",
        title=f"Engineer {n}",
        company=f"Company {n}",
        description_html="<p>x</p>",
    )
    job = store.insert_job(session, normalize(raw), run.id, NOW)
    store.attach_source(session, job.id, raw, NOW)
    job.posted_at = NOW - timedelta(days=age_days)
    row = LlmScore(
        job_id=job.id, run_id=run.id, input_hash=f"h{n}", prompt_version="v", model="m",
        profile_version_id=profile.id, status="ok", final_score=score, scored_at=NOW,
    )
    session.add(row)
    session.flush()
    store.mark_scored(session, [job], row.id)
    return job


def test_top_by_score_then_explore_below_threshold(session: Session) -> None:
    for n, score in enumerate((90, 75, 65, 40, 10)):
        _scored(session, n, score)
    picks = select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1))
    assert [(p.slot, p.score.final_score) for p in picks][:2] == [("top", 90), ("top", 75)]
    assert [p.slot for p in picks] == ["top", "top", "explore"]
    assert picks[2].score.final_score is not None and picks[2].score.final_score < 60
    assert picks[0].sources[0].source == "jobicy"


def test_delivered_and_stale_jobs_are_never_picked(session: Session) -> None:
    sent = _scored(session, 1, 95)
    _scored(session, 2, 99, age_days=30)
    fresh = _scored(session, 3, 80)
    run = store.start_run(session, "daily", NOW)
    store.create_delivery(session, job_id=sent.id, run_id=run.id, slot="top", position=1, now=NOW)
    picks = select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1))
    assert [p.job.id for p in picks] == [fresh.id]


def test_nothing_above_threshold_still_explores(session: Session) -> None:
    _scored(session, 1, 30)
    picks = select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1))
    assert [p.slot for p in picks] == ["explore"]


def test_unscored_and_failed_jobs_are_ignored(session: Session) -> None:
    job = _scored(session, 1, 95)
    store.set_job_state(session, job, "score_failed")
    assert select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1)) == []
```

Run: `uv run pytest tests/test_select.py` → FAIL.

- [ ] **Step 3: `src/aijobradar/select.py`**

```python
"""Which scored jobs go into today's digest (spec §6.5). Pure selection, no sending."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import ScoringConfig
from aijobradar.db.models import Delivery, Job, JobSource, LlmScore


@dataclass(frozen=True)
class Pick:
    job: Job
    score: LlmScore
    slot: str  # "top" | "explore"
    sources: list[JobSource]


def select_digest(
    session: Session, *, cfg: ScoringConfig, now: datetime, rng: random.Random
) -> list[Pick]:
    cutoff = now - timedelta(days=cfg.max_age_days)
    rows = session.execute(
        select(Job, LlmScore)
        .join(LlmScore, Job.llm_score_id == LlmScore.id)
        .where(
            Job.state == "scored",
            LlmScore.final_score.is_not(None),
            func.coalesce(Job.posted_at, Job.first_seen_at) >= cutoff,
            ~exists().where(Delivery.job_id == Job.id),  # a job is sent at most once
        )
        .order_by(LlmScore.final_score.desc(), Job.posted_at.desc().nulls_last(), Job.id)
    ).all()
    above = [(job, score) for job, score in rows if score.final_score >= cfg.threshold]
    below = [(job, score) for job, score in rows if score.final_score < cfg.threshold]
    top = above[: cfg.top_n]
    # Random jobs below the threshold show what the ranking misses (spec §6.5).
    explore = rng.sample(below, min(cfg.explore_n, len(below)))
    return [
        Pick(job, score, slot, store.job_sources(session, job.id))
        for slot, chosen in (("top", top), ("explore", explore))
        for job, score in chosen
    ]
```

- [ ] **Step 4: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 3: Рендер сообщений Telegram

**Files:**
- Create: `src/aijobradar/telegram/__init__.py` (пустой), `src/aijobradar/telegram/render.py`, `tests/test_telegram_render.py`

**Interfaces:**
- Consumes: `select.Pick` (задача 2).
- Produces (всё чистое, возвращает HTML-строки ≤ 4096 символов):
  - `render.MAX_MESSAGE = 4096`;
  - `render.callback_data(delivery_id: int, fit: bool) -> str` → `"l:<id>:1"` / `"l:<id>:0"`;
  - `render.parse_callback(data: str) -> tuple[int, bool] | None`;
  - `render.keyboard(delivery_id: int) -> dict[str, Any]` (`inline_keyboard`, кнопки «👍 подходит» / «👎 мимо»);
  - `render.render_header(day: date, top: int, explore: int) -> str`;
  - `render.render_job(pick: Pick, position: int) -> str`;
  - `render.render_footer(report_text: str) -> str`;
  - `render.render_crash(reason: str, report_text: str | None) -> str`.

- [ ] **Step 1: Падающие тесты** `tests/test_telegram_render.py`:

```python
import uuid
from datetime import UTC, date, datetime

import pytest

from aijobradar.db.models import Job, JobSource, LlmScore
from aijobradar.select import Pick
from aijobradar.telegram.render import (
    MAX_MESSAGE,
    callback_data,
    keyboard,
    parse_callback,
    render_crash,
    render_footer,
    render_header,
    render_job,
)

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _pick(*, title: str = "Backend Engineer", company: str = "Acme", url: str = "https://x.example/1",
          slot: str = "top", note: str = "Node.js и интеграции.") -> Pick:
    job = Job(
        id=uuid.uuid4(), company_raw=company, title_raw=title, salary_min=60000.0,
        salary_max=80000.0, salary_currency="EUR", salary_period="year",
    )
    score = LlmScore(final_score=82.5, why_fit=note, main_risk="Нет ставки.", gaps="Нет Kafka.")
    sources = [JobSource(source="himalayas", source_url=url), JobSource(source="wwr", source_url=url)]
    return Pick(job=job, score=score, slot=slot, sources=sources)


def test_job_message_has_score_salary_links_and_notes() -> None:
    text = render_job(_pick(), 1)
    for fragment in ("Backend Engineer", "Acme", "82/100", "60000–80000 EUR/year",
                     'href="https://x.example/1"', "via Himalayas", "via We Work Remotely",
                     "Node.js и интеграции.", "Нет ставки.", "Нет Kafka."):
        assert fragment in text


def test_everything_third_party_is_escaped() -> None:
    evil = '<script>alert(1)</script> & <b>bold</b> _*['
    text = render_job(_pick(title=evil, company=evil, note=evil, url='https://x.example/"><b>'), 1)
    assert "<script>" not in text and "<b>bold" not in text
    assert "&lt;script&gt;" in text and "&amp;" in text
    assert '"><b>' not in text


def test_non_http_links_are_dropped() -> None:
    text = render_job(_pick(url="javascript:alert(1)"), 1)
    assert "javascript:" not in text and "href" not in text


def test_explore_jobs_are_marked() -> None:
    assert "🎲" in render_job(_pick(slot="explore"), 1)
    assert "🎲" not in render_job(_pick(slot="top"), 1)


def test_huge_fields_still_fit_one_message() -> None:
    text = render_job(_pick(title="T" * 10_000, company="C" * 10_000, url="https://x.example/" + "a" * 5000), 1)
    assert len(text) <= MAX_MESSAGE


def test_footer_is_escaped_and_capped_on_a_line_boundary() -> None:
    report = "\n".join(f"line {i} <&>" for i in range(2000))
    text = render_footer(report)
    assert len(text) <= MAX_MESSAGE and "&lt;&amp;&gt;" in text
    assert text.endswith("…")


@pytest.mark.parametrize("fit", [True, False])
def test_callback_data_round_trip(fit: bool) -> None:
    data = callback_data(123456789012, fit)
    assert len(data.encode()) <= 64
    assert parse_callback(data) == (123456789012, fit)
    assert keyboard(7)["inline_keyboard"][0][0]["callback_data"] == "l:7:1"


@pytest.mark.parametrize("data", ["", "x", "l:abc:1", "l:1:2", "l:-1:1", "l:1", "k:1:1"])
def test_garbage_callback_data_is_rejected(data: str) -> None:
    assert parse_callback(data) is None


def test_header_and_crash() -> None:
    assert "14.10.2026" in render_header(date(2026, 10, 14), 3, 2)
    assert "подходящих нет" in render_header(date(2026, 10, 14), 0, 2)
    crash = render_crash("БД недоступна", None)
    assert "❌" in crash and "БД недоступна" in crash
```

Run: `uv run pytest tests/test_telegram_render.py` → FAIL.

- [ ] **Step 2: `src/aijobradar/telegram/render.py`**

```python
"""Telegram message text (parse_mode HTML). Pure: every third-party string is escaped here."""

import html
import re
from datetime import date
from typing import Any

from aijobradar.db.models import Job
from aijobradar.select import Pick

MAX_MESSAGE = 4096
_TITLE_CHARS = 200
_COMPANY_CHARS = 100
_URL_CHARS = 1000
_NOTE_CHARS = 400
_CALLBACK = re.compile(r"l:(\d{1,18}):([01])")
# Attribution required by the sources' terms ("via <Source>", docs/sources.md).
_SOURCE_NAMES = {"himalayas": "Himalayas", "jobicy": "Jobicy", "wwr": "We Work Remotely"}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _e(text: str, limit: int) -> str:
    return html.escape(_cut(text, limit))


def callback_data(delivery_id: int, fit: bool) -> str:
    return f"l:{delivery_id}:{1 if fit else 0}"


def parse_callback(data: str) -> tuple[int, bool] | None:
    match = _CALLBACK.fullmatch(data)
    return (int(match.group(1)), match.group(2) == "1") if match else None


def keyboard(delivery_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "👍 подходит", "callback_data": callback_data(delivery_id, True)},
                {"text": "👎 мимо", "callback_data": callback_data(delivery_id, False)},
            ]
        ]
    }


def _salary(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return "ставка не указана"
    low = f"{job.salary_min:.0f}" if job.salary_min is not None else "?"
    high = f"{job.salary_max:.0f}" if job.salary_max is not None else "?"
    return f"{low}–{high} {job.salary_currency or '?'}/{job.salary_period or '?'}"


def _link(source: str, url: str) -> str | None:
    if not url.startswith(("https://", "http://")) or len(url) > _URL_CHARS:
        return None
    name = _SOURCE_NAMES.get(source, source)
    return f'<a href="{html.escape(url, quote=True)}">via {html.escape(name)}</a>'


def render_header(day: date, top: int, explore: int) -> str:
    stamp = day.strftime("%d.%m.%Y")
    if top == 0:
        line = "Сегодня подходящих нет"
    else:
        line = f"Выше порога: {top}"
    if explore:
        line += f" · 🎲 для проверки: {explore}"
    return f"<b>AiJobRadar · {stamp}</b>\n{line}"


def render_job(pick: Pick, position: int) -> str:
    job, score = pick.job, pick.score
    mark = " 🎲" if pick.slot == "explore" else ""
    links = [link for s in pick.sources if (link := _link(s.source, s.source_url))]
    lines = [
        f"<b>{position}. {_e(job.title_raw, _TITLE_CHARS)}</b> — {_e(job.company_raw, _COMPANY_CHARS)}{mark}",
        f"Оценка: {score.final_score or 0:.0f}/100 · {html.escape(_salary(job))}",
    ]
    if links:
        lines.append(" · ".join(links))
    for icon, note in (("✅", score.why_fit), ("⚠️", score.main_risk), ("➖", score.gaps)):
        if note:
            lines.append(f"{icon} {_e(note, _NOTE_CHARS)}")
    return "\n".join(lines)


def _cap_lines(text: str, limit: int) -> str:
    """Cut escaped text at a line break, so no HTML entity is split."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 2)
    return text[: cut if cut > 0 else 0] + "\n…"


def render_footer(report_text: str) -> str:
    return _cap_lines(f"<b>Статус прогона</b>\n{html.escape(report_text)}", MAX_MESSAGE).rstrip()


def render_crash(reason: str, report_text: str | None) -> str:
    text = f"❌ <b>AiJobRadar: прогон не удался</b>\n{html.escape(reason)}"
    if report_text:
        text += f"\n\n{html.escape(report_text)}"
    return _cap_lines(text, MAX_MESSAGE)
```

(`render_footer` должен заканчиваться `…` при обрезке: `_cap_lines` добавляет `"\n…"`, `rstrip()` убирает только пробелы.)

- [ ] **Step 3: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 4: Клиент Telegram Bot API

**Files:**
- Create: `src/aijobradar/telegram/client.py`, `tests/test_telegram_client.py`

**Interfaces:**
- Produces:
  - `client.TelegramError(Exception)` — текст: `"<method>: <причина>"`, без токена, URL и `description`;
  - `client.TelegramClient(token: str, http: httpx.Client, sleep: Callable[[float], None] = time.sleep, attempts: int = 3)` (dataclass);
  - `.send_message(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> int` (message_id);
  - `.get_updates(offset: int) -> list[dict[str, Any]]` (`timeout=0`, `allowed_updates=["callback_query"]`);
  - `.answer_callback(callback_id: str, text: str) -> None`.

- [ ] **Step 1: Падающие тесты** `tests/test_telegram_client.py`:

```python
import httpx
import pytest
import respx

from aijobradar.telegram.client import TelegramClient, TelegramError

TOKEN = "123:TEST-SECRET"
BASE = f"https://api.telegram.org/bot{TOKEN}"


def _client(sleeps: list[float]) -> TelegramClient:
    return TelegramClient(token=TOKEN, http=httpx.Client(), sleep=sleeps.append)


@respx.mock
def test_send_message_returns_message_id_and_uses_html() -> None:
    route = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
    )
    assert _client([]).send_message("42", "<b>hi</b>", {"inline_keyboard": []}) == 77
    body = route.calls[0].request.read().decode()
    assert '"parse_mode":"HTML"' in body.replace(" ", "") and '"chat_id":"42"' in body.replace(" ", "")


@respx.mock
def test_429_waits_retry_after_then_succeeds() -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        side_effect=[
            httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
        ]
    )
    sleeps: list[float] = []
    assert _client(sleeps).send_message("42", "x") == 1
    assert sleeps == [3]


@respx.mock
def test_network_errors_retry_three_times_then_fail_without_token() -> None:
    respx.post(f"{BASE}/sendMessage").mock(side_effect=httpx.ConnectError("boom " + BASE))
    sleeps: list[float] = []
    with pytest.raises(TelegramError) as info:
        _client(sleeps).send_message("42", "x")
    assert len(sleeps) == 2  # 3 attempts, 2 waits
    assert TOKEN not in str(info.value) and "api.telegram.org" not in str(info.value)
    assert str(info.value) == "sendMessage: ConnectError"


@respx.mock
def test_bad_request_is_not_retried_and_hides_description() -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "can't parse: SECRET JOB TEXT"}
        )
    )
    sleeps: list[float] = []
    with pytest.raises(TelegramError) as info:
        _client(sleeps).send_message("42", "x")
    assert sleeps == [] and str(info.value) == "sendMessage: HTTP 400"


@respx.mock
def test_get_updates_and_answer_callback() -> None:
    respx.post(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]})
    )
    answer = respx.post(f"{BASE}/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    client = _client([])
    assert client.get_updates(5) == [{"update_id": 5}]
    client.answer_callback("cb-1", "Записал")
    assert answer.called
```

Run: `uv run pytest tests/test_telegram_client.py` → FAIL.

- [ ] **Step 2: `src/aijobradar/telegram/client.py`**

```python
"""The only place that talks to the Telegram Bot API. The token is part of every URL, so no
error raised here carries a URL, an httpx message or Telegram's own description."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

_API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


@dataclass
class TelegramClient:
    token: str
    http: httpx.Client
    sleep: Callable[[float], None] = field(default=time.sleep)
    attempts: int = 3

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"{_API}/bot{self.token}/{method}"
        for attempt in range(1, self.attempts + 1):
            wait = 2.0 ** (attempt - 1)
            try:
                response = self.http.post(url, json=payload)
            except httpx.HTTPError as exc:
                if attempt == self.attempts:
                    raise TelegramError(f"{method}: {type(exc).__name__}") from None
                self.sleep(wait)
                continue
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code == 200 and body.get("ok"):
                return body.get("result")
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.attempts:
                raise TelegramError(f"{method}: HTTP {response.status_code}")
            retry_after = (body.get("parameters") or {}).get("retry_after")
            self.sleep(float(retry_after) if isinstance(retry_after, int | float) else wait)
        raise TelegramError(f"{method}: no attempts")  # attempts < 1

    def send_message(
        self, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None
    ) -> int:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._call("sendMessage", payload)
        return int(result["message_id"])

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        result = self._call(
            "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["callback_query"]}
        )
        return list(result or [])

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
```

- [ ] **Step 3: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 5: Отправка дайджеста

**Files:**
- Create: `src/aijobradar/telegram/deliver.py`, `tests/test_telegram_deliver.py`

**Interfaces:**
- Consumes: `select.Pick`, `store.create_delivery`, `render.*`, `TelegramClient`, `TelegramError`.
- Produces:
  - `deliver.DeliveryReport` (dataclass): `top: int = 0`, `explore: int = 0`;
  - `deliver.deliver_digest(session, client: TelegramClient, *, chat_id: str, run_id: uuid.UUID, picks: list[Pick], report_text: str, now: datetime) -> DeliveryReport` — заголовок → по сообщению на вакансию (строка `deliveries` пишется до отправки и коммитится после неё; при ошибке отправки — `rollback` и `TelegramError` наружу) → подвал со статусом;
  - `deliver.send_crash(client: TelegramClient, *, chat_id: str, reason: str, report_text: str | None) -> None` — без БД.

- [ ] **Step 1: Падающие тесты** `tests/test_telegram_deliver.py`:

```python
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Delivery, LlmScore
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.select import Pick
from aijobradar.telegram.client import TelegramError
from aijobradar.telegram.deliver import deliver_digest, send_crash

NOW = datetime(2026, 10, 14, 6, tzinfo=UTC)


class FakeTelegram:
    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.fail_on = fail_on

    def send_message(self, chat_id: str, text: str, reply_markup: Any = None) -> int:
        if self.fail_on is not None and len(self.sent) + 1 == self.fail_on:
            raise TelegramError("sendMessage: HTTP 500")
        self.sent.append((text, reply_markup))
        return 1000 + len(self.sent)


def _picks(session: Session, n: int) -> tuple[Any, list[Pick]]:
    run = store.start_run(session, "daily", NOW)
    profile, _ = store.add_profile_version(session, resume_md="R", targets_md="T", note=None, now=NOW)
    picks = []
    for i in range(n):
        raw = RawJob(source="wwr", source_job_id=str(i), source_url=f"https://wwr.example/{i}",
                     title=f"Engineer {i}", company=f"Co {i}", description_html="<p>x</p>")
        job = store.insert_job(session, normalize(raw), run.id, NOW - timedelta(hours=1))
        store.attach_source(session, job.id, raw, NOW)
        row = LlmScore(job_id=job.id, run_id=run.id, input_hash=f"h{i}", prompt_version="v",
                       model="m", profile_version_id=profile.id, status="ok", final_score=90 - i,
                       why_fit="Да.", scored_at=NOW)
        session.add(row)
        session.flush()
        store.mark_scored(session, [job], row.id)
        picks.append(Pick(job, row, "top" if i < 2 else "explore", store.job_sources(session, job.id)))
    session.commit()
    return run, picks


def test_digest_is_header_jobs_footer_with_buttons(session: Session) -> None:
    run, picks = _picks(session, 3)
    tg = FakeTelegram()
    report = deliver_digest(session, tg, chat_id="42", run_id=run.id, picks=picks,  # type: ignore[arg-type]
                            report_text="Прогон: ok", now=NOW)
    assert (report.top, report.explore) == (2, 1)
    texts = [t for t, _ in tg.sent]
    assert "AiJobRadar" in texts[0] and "Статус прогона" in texts[-1] and len(texts) == 5
    rows = session.scalars(select(Delivery).order_by(Delivery.position)).all()
    assert [r.slot for r in rows] == ["top", "top", "explore"]
    assert [r.tg_message_id for r in rows] == [1002, 1003, 1004]
    assert tg.sent[1][1]["inline_keyboard"][0][0]["callback_data"] == f"l:{rows[0].id}:1"


def test_failure_mid_digest_keeps_sent_jobs_and_drops_the_unsent(session: Session) -> None:
    run, picks = _picks(session, 3)
    tg = FakeTelegram(fail_on=3)  # header ok, job 1 ok, job 2 fails
    with pytest.raises(TelegramError):
        deliver_digest(session, tg, chat_id="42", run_id=run.id, picks=picks,  # type: ignore[arg-type]
                       report_text="x", now=NOW)
    delivered = {d.job_id for d in session.scalars(select(Delivery))}
    assert delivered == {picks[0].job.id}


def test_nothing_today_still_sends_header_and_footer(session: Session) -> None:
    run, _ = _picks(session, 0)
    tg = FakeTelegram()
    report = deliver_digest(session, tg, chat_id="42", run_id=run.id, picks=[],  # type: ignore[arg-type]
                            report_text="Прогон: ok", now=NOW)
    assert (report.top, report.explore) == (0, 0)
    assert "подходящих нет" in tg.sent[0][0] and len(tg.sent) == 2


def test_crash_message_needs_no_database() -> None:
    tg = FakeTelegram()
    send_crash(tg, chat_id="42", reason="БД недоступна", report_text=None)  # type: ignore[arg-type]
    assert "БД недоступна" in tg.sent[0][0]
```

Run: `uv run pytest tests/test_telegram_deliver.py` → FAIL.

- [ ] **Step 2: `src/aijobradar/telegram/deliver.py`**

```python
"""Send the digest. A delivery row is written before each send and committed after it, so a
job is never sent twice and a failed send leaves the job for the next run."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.select import Pick
from aijobradar.telegram import render
from aijobradar.telegram.client import TelegramClient


@dataclass
class DeliveryReport:
    top: int = 0
    explore: int = 0


def deliver_digest(
    session: Session,
    client: TelegramClient,
    *,
    chat_id: str,
    run_id: uuid.UUID,
    picks: list[Pick],
    report_text: str,
    now: datetime,
) -> DeliveryReport:
    report = DeliveryReport()
    top = sum(p.slot == "top" for p in picks)
    client.send_message(chat_id, render.render_header(now.date(), top, len(picks) - top))
    for position, pick in enumerate(picks, start=1):
        delivery = store.create_delivery(
            session, job_id=pick.job.id, run_id=run_id, slot=pick.slot, position=position, now=now
        )
        try:
            message_id = client.send_message(
                chat_id, render.render_job(pick, position), render.keyboard(delivery.id)
            )
        except Exception:
            session.rollback()  # not sent: the job stays available for the next digest
            raise
        delivery.tg_message_id = message_id
        session.commit()
        if pick.slot == "top":
            report.top += 1
        else:
            report.explore += 1
    client.send_message(chat_id, render.render_footer(report_text))
    return report


def send_crash(
    client: TelegramClient, *, chat_id: str, reason: str, report_text: str | None
) -> None:
    client.send_message(chat_id, render.render_crash(reason, report_text))
```

- [ ] **Step 3: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 6: `run --deliver`, матрица исходов, блокировка, сводка Actions

**Files:**
- Create: `tests/test_cli_daily.py`
- Modify: `src/aijobradar/config.py` (`Settings`), `src/aijobradar/pipeline.py`, `src/aijobradar/cli.py`, `tests/test_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `select_digest`, `deliver_digest`, `send_crash`, `TelegramClient`, `TelegramError`, `DeliveryReport` (задачи 2–5); существующие `run_fetch`, `_score_after_fetch`, `format_report`, `update_run_result`.
- Produces:
  - `Settings.telegram_bot_token: str | None = None`, `Settings.telegram_chat_id: str | None = None`;
  - `pipeline.FetchReport.delivery: DeliveryReport | None = None`; `pipeline.format_delivery_line(d: DeliveryReport) -> str` (`"Выдача: 3 выше порога + 2 🎲"` / `"Выдача: подходящих нет + 2 🎲"` / `"Выдача: подходящих нет"`); `format_report` дописывает её, если `delivery` задан;
  - CLI `run`: опция `--deliver/--no-deliver` (по умолчанию `--no-deliver`); `cli._settings() -> Settings` (нет `DATABASE_URL` → `Нет DATABASE_URL: задайте переменную окружения или .env.`, код 2); `cli._run_lock(engine)` — контекстный менеджер с `pg_try_advisory_lock(_LOCK_ID)`; занято → `Другой прогон уже идёт.`, код 1; `run` и `score` берут блокировку;
  - сводка: если задан `GITHUB_STEP_SUMMARY` — текст отчёта дописывается туда (в блоке кода).

Матрица исходов `run --deliver` (§6.7), каждая строка — тест в `tests/test_cli_daily.py`:

| Ситуация | Статус | Код | Telegram |
|---|---|---|---|
| всё ок | `ok` | 0 | заголовок, вакансии, подвал |
| часть источников `failed`/`degraded` или часть LLM упала | `partial` | 0 | дайджест, в подвале ⚠️/❌ строки |
| подходящих 0 | `ok` | 0 | «Сегодня подходящих нет» + подвал |
| все источники `failed` | `failed` | 1 | `render_crash("Все источники недоступны", отчёт)` |
| БД недоступна | — | 1 | `render_crash("БД недоступна — без неё нельзя исключить повторы", None)` |
| Telegram не принял | `failed` | 1 | — (`Telegram не принял: <ошибка>` в stderr) |

- [ ] **Step 1: Падающие тесты**

В `tests/test_pipeline.py`:

```python
from aijobradar.pipeline import format_delivery_line
from aijobradar.telegram.deliver import DeliveryReport


@pytest.mark.parametrize(
    ("report", "line"),
    [
        (DeliveryReport(top=3, explore=2), "Выдача: 3 выше порога + 2 🎲"),
        (DeliveryReport(top=0, explore=2), "Выдача: подходящих нет + 2 🎲"),
        (DeliveryReport(), "Выдача: подходящих нет"),
    ],
)
def test_delivery_line(report: DeliveryReport, line: str) -> None:
    assert format_delivery_line(report) == line
```

В `tests/test_cli.py`:

```python
def test_missing_database_url_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    result = CliRunner().invoke(app, ["rules", "recheck", "--rules", str(ROOT / "config" / "rules.yaml")])
    assert result.exit_code == 2
    assert "Нет DATABASE_URL" in result.output and "Traceback" not in result.output
```

(`ROOT = Path(__file__).parent.parent` — добавить в файл, если его нет.)

`tests/test_cli_daily.py` — интеграция через `cli_db` (перенести фикстуры `cli_db`, `_seed`, `_FakeScorer` из `tests/test_cli.py` в общий `tests/cli_support.py` и импортировать в обоих файлах; поведение фикстур не менять). Сеть источников — respx с минимальными валидными ответами Jobicy и WWR из `tests/fixtures/` (как в существующих тестах `run --score` в `tests/test_cli.py`), Himalayas выключен временным `sources.yaml`; Telegram — respx на `https://api.telegram.org/bot123:TEST/...`; `TELEGRAM_BOT_TOKEN=123:TEST`, `TELEGRAM_CHAT_ID=42` через `monkeypatch.setenv`; `_make_scorer` подменён `_FakeScorer` с `ok`-результатом (балл 100 → выше порога). Тесты:

```python
def test_daily_ok_sends_digest_and_records_deliveries(...) -> None:
    # exit 0; sendMessage called: header + N jobs + footer; deliveries rows == N; runs.status ok;
    # runs.counts has delivered_top / delivered_explore; stdout has "Выдача:" and no job titles.


def test_daily_partial_when_a_source_fails(...) -> None:
    # one source mocked as HTTP 503 -> exit 0, status partial, footer text contains "❌".


def test_daily_nothing_above_threshold(...) -> None:
    # scorer gives final_score 0 (geo_eligible "no") -> header "Сегодня подходящих нет", exit 0.


def test_daily_all_sources_failed_sends_crash_and_exits_1(...) -> None:
    # every source 503 -> exactly one sendMessage with "прогон не удался", exit 1, no deliveries.


def test_daily_database_down_sends_crash_and_exits_1(monkeypatch, ...) -> None:
    # DATABASE_URL points at an unreachable port (postgresql://localhost:1/x) -> one crash message
    # "БД недоступна", exit 1, no source request was made (respx routes not called).


def test_daily_telegram_rejects_exits_1_and_marks_run_failed(...) -> None:
    # sendMessage -> HTTP 400 every time -> exit 1, stderr "Telegram не принял: sendMessage: HTTP 400",
    # runs.status failed, token "123:TEST" not in output.


def test_deliver_without_telegram_settings_exits_2_before_network(...) -> None:
    # no TELEGRAM_* env -> exit 2 "Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID", respx not called.


def test_second_concurrent_run_is_refused(...) -> None:
    # hold pg_advisory_lock(_LOCK_ID) on a separate connection -> `run --no-score` exits 1 with
    # "Другой прогон уже идёт.", no source request was made.


def test_step_summary_gets_counters_only(tmp_path, ...) -> None:
    # GITHUB_STEP_SUMMARY=tmp_path/"summary.md" -> file contains "Прогон:" and "Выдача:",
    # and no job title / company from the fixtures.
```

Каждый тест пишется полностью по образцу существующих CLI-тестов `run --score` в `tests/test_cli.py` (те же фикстуры источников и проверки `runs`), с конкретными утверждениями из комментариев выше.

Run: `uv run pytest tests/test_pipeline.py tests/test_cli.py tests/test_cli_daily.py` → FAIL.

- [ ] **Step 2: Реализация**

`Settings` (в `config.py`):

```python
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
```

`pipeline.py`: импорт `from aijobradar.telegram.deliver import DeliveryReport`; поле `delivery: DeliveryReport | None = None` в `FetchReport`;

```python
def format_delivery_line(d: DeliveryReport) -> str:
    line = f"Выдача: {d.top} выше порога" if d.top else "Выдача: подходящих нет"
    return line + (f" + {d.explore} 🎲" if d.explore else "")
```

и в конце `format_report`: `if report.delivery is not None: lines.append(format_delivery_line(report.delivery))`.

`cli.py`:

```python
_LOCK_ID = 4_204_2026  # one AiJobRadar run (run/score/labels-poll) per database at a time


def _settings() -> "Settings":
    from aijobradar.config import Settings

    try:
        return Settings()
    except ValidationError:
        typer.echo("Нет DATABASE_URL: задайте переменную окружения или .env.", err=True)
        raise typer.Exit(2) from None


@contextmanager
def _run_lock(engine: "Engine") -> Iterator[None]:
    from sqlalchemy import func, select

    with engine.connect() as conn:
        if not conn.execute(select(func.pg_try_advisory_lock(_LOCK_ID))).scalar():
            typer.echo("Другой прогон уже идёт.", err=True)
            raise typer.Exit(1)
        try:
            yield
        finally:
            conn.execute(select(func.pg_advisory_unlock(_LOCK_ID)))


def _write_step_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"```\n{text}\n```\n")
```

(импорты: `os`, `from collections.abc import Iterator`, `from contextlib import contextmanager`; `Settings` — под `TYPE_CHECKING`). Все команды, которые сейчас вызывают `Settings()`, переходят на `_settings()`.

`run`: новая опция `deliver: bool = typer.Option(False, "--deliver/--no-deliver", help="Send the digest to Telegram.")`. Порядок:
1. профиль, правила, оценка проверяются как сейчас; если `deliver` и нет `telegram_bot_token`/`telegram_chat_id` в `_settings()` — `Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID.`, код 2;
2. `engine = make_engine(...)`; проверка БД: `with engine.connect(): pass`; `OperationalError` → если `deliver` — `send_crash(..., reason="БД недоступна — без неё нельзя исключить повторы", report_text=None)` (ошибку Telegram здесь только печатать), `БД недоступна.` в stderr, код 1;
3. всё дальнейшее — внутри `with _run_lock(engine):`;
4. сбор и оценка — как сейчас;
5. если `deliver`:
   - `report.status is FAILED` → `send_crash(reason="Все источники недоступны", report_text=format_report(report))`, сводка, код 1;
   - иначе: в новой сессии `picks = select_digest(session, cfg=scoring_cfg or load_scoring_config(scoring), now=..., rng=random.Random())`; `delivery = deliver_digest(..., report_text=format_report(report))`; `report = dataclasses.replace(report, delivery=delivery)`; `store.update_run_result(..., status=report.status.value, extra_counts={"delivered_top": delivery.top, "delivered_explore": delivery.explore})`;
   - `TelegramError as exc` → `Telegram не принял: {exc}` в stderr, `update_run_result(status="failed", extra_counts={})`, сводка, код 1;
6. `typer.echo(format_report(report))`, `_write_step_summary(format_report(report))`, код 1 при `FAILED`.

`--deliver` без `--score` допустим: тогда `scoring_cfg` для порога грузится через `_load_scoring(scoring)` (только конфиг).

`score`: тело оборачивается в `with _run_lock(engine):`.

- [ ] **Step 3: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 7: `aijobradar labels-poll`

**Files:**
- Create: `src/aijobradar/telegram/labels.py`, `tests/test_labels_poll.py`
- Modify: `src/aijobradar/cli.py`

**Interfaces:**
- Consumes: `TelegramClient.get_updates/answer_callback`, `render.parse_callback`, `store.add_label/get_kv/set_kv`, `Delivery`, `cli._settings`, `cli._run_lock`.
- Produces:
  - `labels.LabelsReport` (dataclass): `updates: int = 0`, `labeled: int = 0`, `ignored: int = 0`;
  - `labels.OFFSET_KEY = "telegram_update_offset"`;
  - `labels.poll_labels(session, client: TelegramClient, *, chat_id: str, now: datetime) -> LabelsReport` — один `getUpdates` с сохранённым offset; метки и новый offset коммитятся вместе;
  - CLI `labels-poll`: прогон `kind = "labels"`, печать `Метки: получено N, записано M, пропущено K.`, код 0; ошибка Telegram → `Telegram не принял: …`, код 1.

- [ ] **Step 1: Падающие тесты** `tests/test_labels_poll.py`:

```python
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Label
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.telegram.client import TelegramError
from aijobradar.telegram.labels import OFFSET_KEY, poll_labels

NOW = datetime(2026, 10, 14, 9, tzinfo=UTC)


class FakeTelegram:
    def __init__(self, updates: list[dict[str, Any]], answer_fails: bool = False) -> None:
        self.updates = updates
        self.offsets: list[int] = []
        self.answers: list[str] = []
        self.answer_fails = answer_fails

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        return [u for u in self.updates if u["update_id"] >= offset]

    def answer_callback(self, callback_id: str, text: str) -> None:
        if self.answer_fails:
            raise TelegramError("answerCallbackQuery: HTTP 400")
        self.answers.append(callback_id)


def _delivery(session: Session) -> int:
    run = store.start_run(session, "daily", NOW)
    raw = RawJob(source="wwr", source_job_id="1", source_url="https://wwr.example/1",
                 title="Engineer", company="Co", description_html="<p>x</p>")
    job = store.insert_job(session, normalize(raw), run.id, NOW)
    return store.create_delivery(session, job_id=job.id, run_id=run.id, slot="top", position=1, now=NOW).id


def _press(update_id: int, data: str, chat: int = 42, cb: str | None = None) -> dict[str, Any]:
    return {"update_id": update_id, "callback_query": {
        "id": cb or f"cb-{update_id}", "data": data, "message": {"chat": {"id": chat}}}}


def test_presses_become_labels_and_offset_moves(session: Session) -> None:
    d = _delivery(session)
    tg = FakeTelegram([_press(10, f"l:{d}:1"), _press(11, f"l:{d}:0")])
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert (report.updates, report.labeled, report.ignored) == (2, 2, 0)
    assert [l.label for l in session.scalars(select(Label).order_by(Label.id))] == ["fit", "no_fit"]
    assert store.get_kv(session, OFFSET_KEY) == "12"
    assert poll_labels(session, tg, chat_id="42", now=NOW).updates == 0  # type: ignore[arg-type]
    assert tg.offsets == [0, 12]


def test_foreign_chats_and_garbage_are_ignored_but_consumed(session: Session) -> None:
    d = _delivery(session)
    tg = FakeTelegram([
        _press(1, f"l:{d}:1", chat=999), _press(2, "l:999999:1"), _press(3, "garbage"),
        {"update_id": 4, "message": {"text": "hi"}},
    ])
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert (report.updates, report.labeled, report.ignored) == (4, 0, 4)
    assert session.scalars(select(Label)).all() == []
    assert store.get_kv(session, OFFSET_KEY) == "5"


def test_reread_press_is_not_labeled_twice(session: Session) -> None:
    d = _delivery(session)
    poll_labels(session, FakeTelegram([_press(7, f"l:{d}:1", cb="same")]), chat_id="42", now=NOW)  # type: ignore[arg-type]
    store.set_kv(session, OFFSET_KEY, "0")  # as if the offset write had been lost
    poll_labels(session, FakeTelegram([_press(7, f"l:{d}:1", cb="same")]), chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert len(session.scalars(select(Label)).all()) == 1


def test_a_failed_answer_does_not_fail_the_poll(session: Session) -> None:
    d = _delivery(session)
    report = poll_labels(session, FakeTelegram([_press(1, f"l:{d}:1")], answer_fails=True),  # type: ignore[arg-type]
                         chat_id="42", now=NOW)
    assert report.labeled == 1
```

Run: `uv run pytest tests/test_labels_poll.py` → FAIL.

- [ ] **Step 2: `src/aijobradar/telegram/labels.py`**

```python
"""Turn 👍/👎 presses into labels. Runs every few hours, so answering a press is best effort:
Telegram rejects answers to callback queries older than ~15 seconds."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Delivery
from aijobradar.telegram.client import TelegramClient, TelegramError
from aijobradar.telegram.render import parse_callback

OFFSET_KEY = "telegram_update_offset"


@dataclass
class LabelsReport:
    updates: int = 0
    labeled: int = 0
    ignored: int = 0


def _label_from(session: Session, query: dict[str, Any], chat_id: str, now: datetime) -> bool:
    chat = ((query.get("message") or {}).get("chat") or {}).get("id")
    if str(chat) != chat_id:
        return False  # only the owner's chat may label
    parsed = parse_callback(str(query.get("data") or ""))
    if parsed is None:
        return False
    delivery = session.get(Delivery, parsed[0])
    if delivery is None:
        return False
    store.add_label(
        session,
        job_id=delivery.job_id,
        label="fit" if parsed[1] else "no_fit",
        source="telegram",
        source_ref=str(query.get("id") or "")[:64] or None,
        now=now,
    )
    return True


def poll_labels(
    session: Session, client: TelegramClient, *, chat_id: str, now: datetime
) -> LabelsReport:
    report = LabelsReport()
    offset = int(store.get_kv(session, OFFSET_KEY) or 0)
    updates = client.get_updates(offset)
    answered: list[tuple[str, bool]] = []
    for update in updates:
        report.updates += 1
        offset = max(offset, int(update["update_id"]) + 1)
        query = update.get("callback_query")
        if isinstance(query, dict) and _label_from(session, query, chat_id, now):
            report.labeled += 1
            answered.append((str(query.get("id")), str(query.get("data", "")).endswith(":1")))
        else:
            report.ignored += 1
    store.set_kv(session, OFFSET_KEY, str(offset))
    session.commit()  # labels and the new offset land together
    for callback_id, fit in answered:
        try:
            client.answer_callback(callback_id, "Записал: 👍" if fit else "Записал: 👎")
        except TelegramError:
            pass  # too old to answer: the label is already stored
    return report
```

CLI `labels-poll` в `cli.py`:

```python
@app.command("labels-poll")
def labels_poll() -> None:
    """Collect 👍/👎 presses from Telegram into the labels table."""
    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.db.models import Run
    from aijobradar.db.session import make_engine
    from aijobradar.sources.common import make_client
    from aijobradar.telegram.client import TelegramClient, TelegramError
    from aijobradar.telegram.labels import poll_labels

    settings = _settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        typer.echo("Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID.", err=True)
        raise typer.Exit(2)
    engine = make_engine(settings.sqlalchemy_url)
    with _run_lock(engine), make_client(settings.user_agent, settings.http_timeout_s) as http:
        client = TelegramClient(token=settings.telegram_bot_token, http=http)
        with Session(engine) as session:
            run = store.start_run(session, "labels", datetime.now(UTC))
            session.commit()
            try:
                report = poll_labels(session, client, chat_id=settings.telegram_chat_id, now=datetime.now(UTC))
            except TelegramError as exc:
                typer.echo(f"Telegram не принял: {exc}", err=True)
                store.finish_run(session, run, "failed", {}, datetime.now(UTC))
                session.commit()
                raise typer.Exit(1) from None
            counts = {"updates": report.updates, "labeled": report.labeled, "ignored": report.ignored}
            store.finish_run(session, run, "ok", counts, datetime.now(UTC))
            session.commit()
    typer.echo(f"Метки: получено {report.updates}, записано {report.labeled}, пропущено {report.ignored}.")
```

Тест CLI в `tests/test_cli_daily.py`: `cli_db` + respx `getUpdates` с одним нажатием на засеянную доставку → код 0, строка `Метки: получено 1, записано 1, пропущено 0.`, одна строка `labels`, прогон `kind = "labels"` со статусом `ok`.

- [ ] **Step 3: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 8: Окно Himalayas от последнего успешного прогона

**Files:**
- Modify: `src/aijobradar/config.py` (`HimalayasConfig`), `config/sources.yaml`, `src/aijobradar/sources/__init__.py`, `src/aijobradar/pipeline.py`, `src/aijobradar/cli.py`, `tests/test_pipeline.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `store.last_source_success` (задача 1).
- Produces:
  - `HimalayasConfig.max_lookback_hours: int = Field(96, ge=1)` (валидатор: `max_lookback_hours >= lookback_hours`);
  - `pipeline.himalayas_lookback(last_ok: datetime | None, now: datetime, *, base_hours: int, max_hours: int, margin_hours: int = 6) -> int`;
  - `sources.build_adapters(cfg: AppConfig, *, himalayas_lookback_hours: int | None = None) -> list[Adapter]` (None → `cfg.himalayas.lookback_hours`).

- [ ] **Step 1: Падающие тесты** в `tests/test_pipeline.py`:

```python
from aijobradar.pipeline import himalayas_lookback

NOW_H = datetime(2026, 10, 14, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    ("last_ok", "expected"),
    [
        (None, 30),                               # first run: the configured window
        (NOW_H - timedelta(hours=24), 30),        # daily cadence: base window covers it
        (NOW_H - timedelta(hours=50), 56),        # a missed day: gap + 6 h margin
        (NOW_H - timedelta(hours=50, minutes=1), 57),  # partial hours round up
        (NOW_H - timedelta(days=10), 96),         # long outage: capped
    ],
)
def test_himalayas_window_covers_the_gap_since_last_success(last_ok: datetime | None, expected: int) -> None:
    assert himalayas_lookback(last_ok, NOW_H, base_hours=30, max_hours=96) == expected
```

В `tests/test_config.py`: `max_lookback_hours` меньше `lookback_hours` → `ValidationError`.

Run: `uv run pytest tests/test_pipeline.py tests/test_config.py` → FAIL.

- [ ] **Step 2: Реализация**

`pipeline.py` (`import math`):

```python
def himalayas_lookback(
    last_ok: datetime | None, now: datetime, *, base_hours: int, max_hours: int, margin_hours: int = 6
) -> int:
    """Walk back to the last successful Himalayas run (plus a margin), so a late or missed cron
    does not silently lose jobs; never less than the configured window, never more than the cap."""
    if last_ok is None:
        return base_hours
    gap = math.ceil((now - last_ok).total_seconds() / 3600) + margin_hours
    return min(max(base_hours, gap), max_hours)
```

`HimalayasConfig`: поле и `model_validator(mode="after")`, который бросает `ValueError("max_lookback_hours must be >= lookback_hours")`. `config/sources.yaml` под `himalayas`: `max_lookback_hours: 96  # cap after an outage (gap since the last ok run + 6 h)`.

`build_adapters`: параметр `himalayas_lookback_hours`, в `HimalayasAdapter(lookback_hours=himalayas_lookback_hours or cfg.himalayas.lookback_hours, ...)`.

`cli.run`: до сети, внутри `_run_lock`, если Himalayas включён:

```python
        with Session(engine) as session:
            last_ok = store.last_source_success(session, "himalayas")
        lookback = himalayas_lookback(
            last_ok, now, base_hours=cfg.himalayas.lookback_hours, max_hours=cfg.himalayas.max_lookback_hours
        )
```

и `build_adapters(cfg, himalayas_lookback_hours=lookback)`.

- [ ] **Step 3: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 9: Workflows `daily.yml` и `labels.yml`, keepalive, документация

**Files:**
- Create: `.github/workflows/daily.yml`, `.github/workflows/labels.yml`
- Modify: `.env.example`, `README.md`, `docs/plans/stage1-followups.md`

**Interfaces:**
- Consumes: CLI `db upgrade`, `run --deliver`, `labels-poll` (задачи 6–7).
- Produces: workflows; инструкция по секретам.

- [ ] **Step 1: `.github/workflows/daily.yml`** (SHA экшенов — те же, что в `ci.yml`):

```yaml
name: daily

on:
  schedule:
    - cron: "0 6 * * *"   # 06:00 UTC
  workflow_dispatch:
    inputs:
      limit:
        description: "At most N model calls (empty = config default)"
        required: false
        default: ""

permissions:
  contents: read
  actions: write   # keepalive: re-enable the scheduled workflows

concurrency:
  group: daily
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
      - run: uv sync --locked
      - name: Claude Code (pinned)
        run: npm install --global @anthropic-ai/claude-code@2.1.287
      - name: Rules profile from secret
        env:
          PROFILE_YAML: ${{ secrets.PROFILE_YAML }}
        run: |
          mkdir -p private
          umask 077
          printf '%s\n' "$PROFILE_YAML" > private/profile.yaml
      - run: uv run aijobradar db upgrade
      - name: Fetch, rules, score, deliver
        env:
          LIMIT: ${{ inputs.limit }}
        run: uv run aijobradar run --deliver ${LIMIT:+--limit "$LIMIT"}
      - name: Keepalive (scheduled workflows stop after 60 days without repo activity)
        if: always()
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh workflow enable daily.yml --repo "$GITHUB_REPOSITORY"
          gh workflow enable labels.yml --repo "$GITHUB_REPOSITORY"
```

- [ ] **Step 2: `.github/workflows/labels.yml`**:

```yaml
name: labels

on:
  schedule:
    - cron: "15 */3 * * *"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: labels
  cancel-in-progress: false

jobs:
  poll:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
      - run: uv sync --locked
      - run: uv run aijobradar labels-poll
```

(`labels-poll` при пересечении с ежедневным прогоном получит «Другой прогон уже идёт.» и код 1 — допустимо: через 3 часа повторит. Если это начнёт краснить Actions, разделить ключи блокировки.)

- [ ] **Step 3: `.env.example`** — дописать:

```
# Telegram (a dedicated bot: one getUpdates consumer per bot). Never commit the real .env.
TELEGRAM_BOT_TOKEN=123456:replace-me
TELEGRAM_CHAT_ID=123456789
```

- [ ] **Step 4: `README.md`** — раздел «Ежедневный запуск (GitHub Actions)»: какие секреты нужны (`DATABASE_URL` — direct-строка Neon, не `-pooler`; `TELEGRAM_BOT_TOKEN`; `TELEGRAM_CHAT_ID`; `CLAUDE_CODE_OAUTH_TOKEN` из `claude setup-token`; `PROFILE_YAML` — содержимое `private/profile.yaml`), команды `gh secret set NAME --repo <owner>/<repo>` (значение вводится интерактивно, не в командной строке), разовый `aijobradar db upgrade` и `aijobradar profile import` против Neon, ручной запуск `gh workflow run daily.yml -f limit=3`. Для форков — путь через API-ключ описан как основной (§8), в этом этапе только упоминание.

- [ ] **Step 5: `docs/plans/stage1-followups.md`** — в «До этапа 4» отметить сделанное (advisory lock, окно Himalayas, понятная ошибка без `DATABASE_URL`, direct-эндпоинт Neon в README) и перенести оставшееся в «После этапа 4».

- [ ] **Step 6: Проверка**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`; workflows проверяются `actionlint`, если он установлен (`actionlint .github/workflows/*.yml`), иначе — синтаксис YAML: `uv run python -c "import yaml,sys; [yaml.safe_load(open(p)) for p in sys.argv[1:]]" .github/workflows/*.yml`.
Expected: PASS.

---

### Task 10: Запуск в бою (контроллер, с владельцем)

Только контроллер; действия с аккаунтами и токенами выполняет владелец.

- [ ] Владелец: проект Neon (регион ближе к `us-east` GitHub runners), direct-строка подключения.
- [ ] Владелец: бот через @BotFather (новый, не от hn-ai-monitor), написать боту `/start`, получить chat id.
- [ ] Владелец: `claude setup-token` → токен на год.
- [ ] Владелец: `gh secret set` для пяти секретов (`DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN`, `PROFILE_YAML`) — значения вводит сам.
- [ ] Контроллер: локально с `DATABASE_URL` Neon в окружении владельца — `aijobradar db upgrade`, `aijobradar profile import private/resume.md private/targets.md --note "v2"`.
- [ ] Контроллер: `gh workflow run daily.yml -f limit=5` → дождаться завершения → дайджест пришёл; в логе Actions и в сводке нет названий компаний и текстов; `runs` в Neon со статусом и счётчиками.
- [ ] Владелец нажимает 👍/👎; контроллер: `gh workflow run labels.yml` → в `labels` появились строки.
- [ ] Контроллер: обновить `stage1-followups.md` по итогам живого прогона.
