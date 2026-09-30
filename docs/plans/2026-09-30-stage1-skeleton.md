# Этап 1 — каркас: источники → нормализация → дедуп → Postgres

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** команда `aijobradar fetch` забирает вакансии из Himalayas, Jobicy и We Work Remotely, нормализует, склеивает дубли (между источниками и запусками), пишет в Postgres и печатает отчёт со статусом каждого источника.

**Architecture:** Python-пакет `aijobradar` (src-layout, uv). Адаптер источника = `fetch()` (HTTP) + `parse_record()` (чистая функция); общий `run_adapter()` превращает любой исход в `SourceResult` и никогда не бросает исключение. Нормализация и выбор дубля — чистые функции; доступ к БД — только через `store.py`. Схема — SQLAlchemy 2 + Alembic, миграции сверяются с моделями тестом.

**Tech Stack:** Python 3.12, uv, httpx, pydantic v2 + pydantic-settings, SQLAlchemy 2 + psycopg 3, Alembic, typer, beautifulsoup4, defusedxml, rapidfuzz, PyYAML; pytest, respx, ruff, mypy.

**Spec:** `docs/specs/2026-09-29-aijobradar-design.md` (§3–§6.2, §11–§12), `docs/sources.md`.

## Global Constraints

- Python `>=3.12`; зависимости и запуск только через `uv` (`uv sync`, `uv run …`).
- **Никаких коммитов** без явной команды владельца. Шаг «Checkpoint» = остановиться, показать `git status`/результат тестов, не коммитить.
- Реальные ответы источников — только в `private/raw/` (в `.gitignore`). Фикстуры в `tests/fixtures/` — **синтетические** (выдуманные компании и тексты), схема повторяет реальные ответы.
- User-Agent всех HTTP-запросов: `AiJobRadar/0.1 (personal job monitor)`; таймаут 30 с.
- Адаптер никогда не бросает исключение наружу — только `SourceResult` со статусом `ok | empty | degraded | failed`.
- Тесты не ходят в сеть (httpx мокается `respx`). Живая сеть — только ручной smoke в Task 10.
- Тестовая БД: `TEST_DATABASE_URL` (по умолчанию `postgresql+psycopg://localhost/aijobradar_test`); фикстура отказывается работать с базой, чьё имя не оканчивается на `_test`.
- Строки для пользователя (отчёт CLI) — на русском; код, идентификаторы, комментарии — на английском.
- ruff (line-length 100) и `mypy --strict` на `src/` — зелёные в конце каждой задачи.

## Уточнения к спецификации (внесены этим планом)

1. Нечёткое сравнение заголовков — `rapidfuzz.fuzz.token_sort_ratio ≥ 92`, **не** `token_set_ratio`: `token_set_ratio("senior backend engineer", "backend engineer") = 100` склеил бы разные грейды одной компании (проверено: `token_sort_ratio` = 82). Перед сравнением составные слова приводятся к одному виду (`full stack`/`full-stack` → `fullstack`, то же для `backend`, `frontend`): иначе «Full Stack» и «Fullstack» дают 78.
2. Одно окно дедупа `match_window_days = 60` вместо двух (30 для нечёткого + 60 для перепостов): перепост и есть совпадение по компании и заголовку.
3. Колонка `fingerprint` не нужна: её роль играют `company_norm` + `title_norm`.
4. RSS разбирается `defusedxml.ElementTree`, не `feedparser`: WWR кладёт данные в нестандартные элементы (`region`, `country`, `skills`, `type`), а defusedxml защищает от XXE / billion laughs.
5. `SourceResult` получает поле `out_of_scope` — записи, отброшенные фильтром области источника (у Himalayas нет параметра категории, фильтруем по `parentCategories`), считаются отдельно от невалидных.

## Файловая структура

```
pyproject.toml, uv.lock, .python-version, .env.example, alembic.ini
config/sources.yaml
src/aijobradar/
  __init__.py
  cli.py            # typer: `fetch`, `db upgrade`
  config.py         # Settings (env), AppConfig (YAML), to_sqlalchemy_url
  models.py         # RawJob, SourceResult, SourceStatus, SalaryPeriod
  text.py           # html_to_text, normalize_company, normalize_title, canonical_url, content_hash
  normalize.py      # NormalizedJob, normalize()
  dedup.py          # DedupOutcome, Candidate, best_fuzzy_match()
  store.py          # all DB reads/writes
  ingest.py         # ingest(): dedup decision + store calls
  pipeline.py       # RunStatus, FetchReport, run_fetch(), run_status(), format_report()
  sources/
    __init__.py     # build_adapters(cfg)
    base.py         # Adapter, Fetched, FeedsFailed, FeedRequest, fetch_feeds, run_adapter, describe_error
    common.py       # parse_iso_utc, parse_rfc822_utc, parse_salary_period, make_client
    himalayas.py, jobicy.py, wwr.py
  db/
    __init__.py
    models.py       # Base, Run, SourceRun, Job, JobSource
    session.py      # make_engine
    migrate.py      # alembic_config, upgrade
    alembic/env.py, alembic/script.py.mako, alembic/versions/0001_initial.py
tests/
  conftest.py       # engine/session fixtures (DB), fixture_path helper
  fixtures/himalayas/page1.json, page2.json
  fixtures/jobicy/engineering.json
  fixtures/wwr/fullstack.rss, backend.rss
  test_cli.py, test_config.py, test_text.py, test_base.py, test_himalayas.py,
  test_jobicy.py, test_wwr.py, test_normalize.py, test_db_schema.py,
  test_dedup.py, test_ingest.py, test_pipeline.py
.github/workflows/ci.yml
```

---

### Task 1: Каркас проекта, инструменты, CI

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.env.example`, `src/aijobradar/__init__.py`, `src/aijobradar/cli.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: консольная команда `aijobradar` (typer `app` в `aijobradar.cli`), под-приложение `db`.

- [ ] **Step 1: Создать `pyproject.toml`**

```toml
[project]
name = "aijobradar"
version = "0.1.0"
description = "Personal remote-job monitor with LLM ranking"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "sqlalchemy>=2.0.30",
  "psycopg[binary]>=3.2",
  "alembic>=1.13",
  "typer>=0.12",
  "beautifulsoup4>=4.13",  # 4.13+ ships type hints (mypy --strict)
  "defusedxml>=0.7",
  "rapidfuzz>=3.9",
  "pyyaml>=6.0",
]

[project.scripts]
aijobradar = "aijobradar.cli:app"

[dependency-groups]
dev = ["pytest>=8", "respx>=0.21", "ruff>=0.6", "mypy>=1.11", "types-PyYAML"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aijobradar"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.ruff.lint.flake8-bugbear]
# typer declares CLI options as default values; that is its API, not a mutable-default bug.
extend-immutable-calls = ["typer.Option", "typer.Argument", "pathlib.Path"]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["defusedxml.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Создать `.python-version`, `.env.example`, пакет**

`.python-version`:
```
3.12
```

`.env.example`:
```
# Postgres connection string (Neon: Dashboard → Connect). Never commit the real .env.
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

`src/aijobradar/__init__.py`:
```python
"""AiJobRadar — personal remote-job monitor."""
```

- [ ] **Step 3: Написать падающий тест CLI**

`tests/test_cli.py`:
```python
from typer.testing import CliRunner

from aijobradar.cli import app


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "fetch" in result.output
    assert "db" in result.output
```

- [ ] **Step 4: Установить зависимости и убедиться, что тест падает**

Run: `uv sync && uv run pytest tests/test_cli.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.cli'`

- [ ] **Step 5: Минимальный CLI**

`src/aijobradar/cli.py`:
```python
import typer

app = typer.Typer(no_args_is_help=True, help="AiJobRadar: personal remote-job monitor.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")


@app.command()
def fetch() -> None:
    """Fetch jobs from all enabled sources and store them."""
    raise typer.Exit(2)  # wired up in Task 10


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations."""
    raise typer.Exit(2)  # wired up in Task 8
```

- [ ] **Step 6: Тест проходит, линтеры зелёные**

Run: `uv run pytest tests/test_cli.py && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: `1 passed`; ruff и mypy без ошибок (если `ruff format --check` ругается — выполнить `uv run ruff format .`).

- [ ] **Step 7: CI workflow**

Сначала узнать актуальные мажорные версии экшенов:
Run: `gh api repos/actions/checkout/releases/latest --jq .tag_name && gh api repos/astral-sh/setup-uv/releases/latest --jq .tag_name`
Подставить мажор (например, `v5` / `v7`) в `uses:` ниже.

`.github/workflows/ci.yml`:
```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  python:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: aijobradar_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      TEST_DATABASE_URL: postgresql+psycopg://postgres:postgres@localhost:5432/aijobradar_test
      # In CI a missing database is a failure, not a skip.
      REQUIRE_DB: "1"
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src
      - run: uv run pytest
```

- [ ] **Step 8: Checkpoint** — `uv.lock` создан, тесты и линтеры зелёные. Не коммитить.

---

### Task 2: Доменные модели и текстовые утилиты

**Files:**
- Create: `src/aijobradar/models.py`, `src/aijobradar/text.py`, `tests/test_text.py`

**Interfaces:**
- Produces:
  - `SourceStatus(StrEnum)`: `OK="ok"`, `EMPTY="empty"`, `DEGRADED="degraded"`, `FAILED="failed"`
  - `SalaryPeriod(StrEnum)`: `HOUR`, `DAY`, `WEEK`, `MONTH`, `YEAR` (значения `"hour"`…`"year"`)
  - `RawJob(BaseModel)` — поля ниже; строки обрезаются по краям; `title`/`company` непустые
  - `SourceResult(BaseModel)` — поля ниже
  - `html_to_text(html: str) -> str`, `normalize_company(name: str) -> str`, `normalize_title(title: str) -> str`, `canonical_url(url: str) -> str`, `content_hash(*parts: str) -> str`

- [ ] **Step 1: Падающие тесты**

`tests/test_text.py`:
```python
import pytest
from pydantic import ValidationError

from aijobradar.models import RawJob
from aijobradar.text import (
    canonical_url,
    content_hash,
    html_to_text,
    normalize_company,
    normalize_title,
)


def test_html_to_text_strips_tags_scripts_and_nbsp() -> None:
    html = "<h3>About</h3><p>We build&nbsp;APIs.</p><ul><li>Node</li><li>Postgres</li></ul>"
    html += "<script>track()</script>"
    assert html_to_text(html) == "About\nWe build APIs.\nNode\nPostgres"


def test_html_to_text_keeps_single_blank_line_and_decodes_entities() -> None:
    assert html_to_text("<p>Invoices &amp; contracts.</p>\n\n\n<p>Second</p>") == (
        "Invoices & contracts.\n\nSecond"
    )


def test_html_to_text_empty() -> None:
    assert html_to_text("") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Initrode LLC", "initrode"),
        ("Vandelay Industries ", "vandelay industries"),
        ("Acme Ledger, Inc.", "acme ledger"),
        ("ООО «Ромашка»", "ромашка"),
        ("Co", "co"),  # name made only of a legal suffix is kept, not emptied
    ],
)
def test_normalize_company(raw: str, expected: str) -> None:
    assert normalize_company(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Senior Full-Stack Engineer (React/Node)", "senior fullstack engineer"),
        ("Senior Full Stack Engineer", "senior fullstack engineer"),
        ("Back-end Developer - Remote, US", "backend developer us"),
        ("AI Augmented Software Engineer [gn]", "ai augmented software engineer"),
        ("C++ / C# Engineer", "c++ c# engineer"),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_canonical_url_drops_tracking_and_normalizes() -> None:
    url = "https://remoteOK.com/remote-jobs/x-1137434/?utm_source=a&ref=b&u=aff&page=2#top"
    assert canonical_url(url) == "https://remoteok.com/remote-jobs/x-1137434?page=2"


def test_canonical_url_keeps_meaningful_params_sorted() -> None:
    url = "https://boards.greenhouse.io/acme?gh_jid=42&b=1"
    assert canonical_url(url) == "https://boards.greenhouse.io/acme?b=1&gh_jid=42"


def test_content_hash_is_stable_and_separator_safe() -> None:
    assert content_hash("a", "bc") == content_hash("a", "bc")
    assert content_hash("a", "bc") != content_hash("ab", "c")
    assert len(content_hash("x")) == 64


def test_raw_job_strips_and_rejects_empty_title() -> None:
    job = RawJob(source="s", source_job_id="1", source_url="https://x", title=" T ", company=" C ")
    assert (job.title, job.company) == ("T", "C")
    with pytest.raises(ValidationError):
        RawJob(source="s", source_job_id="1", source_url="https://x", title="  ", company="C")
```

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run pytest tests/test_text.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.models'`

- [ ] **Step 3: Реализация `models.py`**

```python
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    DEGRADED = "degraded"
    FAILED = "failed"


class SalaryPeriod(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class RawJob(BaseModel):
    """A job as one source reports it, before normalization. Datetimes are UTC-aware."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    source_job_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)  # page at the source; required for attribution
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    location_text: str | None = None
    location_restrictions: list[str] = Field(default_factory=list)  # [] = no stated restriction
    timezone_restrictions: list[float] | None = None
    employment_type: str | None = None
    seniority: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: SalaryPeriod | None = None
    posted_at: datetime | None = None
    description_html: str = ""
    tags: list[str] = Field(default_factory=list)


class SourceResult(BaseModel):
    source: str
    status: SourceStatus
    items: list[RawJob] = Field(default_factory=list)
    error: str | None = None
    http_status: int | None = None
    duration_ms: int = 0
    invalid_items: int = 0  # records that failed parsing/validation
    out_of_scope: int = 0  # records dropped by the source's own scope filter (e.g. category)
```

- [ ] **Step 4: Реализация `text.py`**

```python
import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

_SPACES = re.compile(r"[ \t ]+")
_LEGAL_SUFFIXES = frozenset(
    {"inc", "llc", "ltd", "limited", "gmbh", "corp", "corporation", "co", "plc", "ag",
     "sa", "bv", "oy", "ab", "srl", "sro", "ооо", "ао", "оао", "зао"}
)
_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_TITLE_NOISE = re.compile(r"\b(?:remote|wfh)\b")
# Same compound word written apart/hyphenated must compare equal ("full stack" == "fullstack").
_COMPOUNDS = re.compile(r"\b(full|back|front)[\s-]+(stack|end)\b")
_DROP_PARAMS = frozenset({"ref", "source", "src", "fbclid", "gclid", "u"})


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [_SPACES.sub(" ", line).strip() for line in soup.get_text("\n").splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):  # collapse runs of blank lines into one
            out.append(line)
    return "\n".join(out).strip()


def normalize_company(name: str) -> str:
    folded = re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", name).casefold())
    tokens = folded.split()
    kept = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return " ".join(kept or tokens)


def normalize_title(title: str) -> str:
    s = unicodedata.normalize("NFKC", title).casefold()
    s = _BRACKETED.sub(" ", s)
    s = _COMPOUNDS.sub(lambda m: m.group(1) + m.group(2), s)
    s = _TITLE_NOISE.sub(" ", s)
    s = re.sub(r"[^\w\s+#]", " ", s)  # keep c++ / c#
    return " ".join(s.split())


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _DROP_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    scheme = (parts.scheme or "https").lower()
    return urlunsplit((scheme, parts.netloc.lower(), path, urlencode(query), ""))


def content_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
```

- [ ] **Step 5: Тесты проходят**

Run: `uv run pytest tests/test_text.py && uv run ruff check . && uv run mypy src`
Expected: все PASS, линтеры чистые.

- [ ] **Step 6: Checkpoint** — не коммитить.

---

### Task 3: Контракт адаптера и `run_adapter`

**Files:**
- Create: `src/aijobradar/sources/__init__.py` (пустой пока), `src/aijobradar/sources/base.py`, `src/aijobradar/sources/common.py`, `tests/test_base.py`

**Interfaces:**
- Consumes: `RawJob`, `SourceResult`, `SourceStatus`, `SalaryPeriod` (Task 2)
- Produces:
  - `Fetched(records: list[Any], error: str | None = None, http_status: int | None = None)` (dataclass)
  - `FeedsFailed(Exception)` с атрибутом `http_status: int | None`
  - `FeedRequest(label: str, url: str, params: dict[str, str | int] | None = None)` (dataclass)
  - `Adapter(Protocol)`: `name: str`; `fetch(client: httpx.Client) -> Fetched`; `parse_record(record: Any) -> RawJob | None` (`None` = вне области; исключение = невалидная запись)
  - `describe_error(exc: BaseException) -> tuple[str, int | None]`
  - `fetch_feeds(client, requests: Sequence[FeedRequest], extract: Callable[[httpx.Response], list[Any]], key: Callable[[Any], str]) -> Fetched`
  - `run_adapter(adapter: Adapter, client: httpx.Client) -> SourceResult`
  - `DEGRADED_INVALID_SHARE = 0.2`
  - в `common.py`: `make_client(user_agent: str, timeout_s: float) -> httpx.Client`, `parse_iso_utc(value: str | None) -> datetime | None`, `parse_rfc822_utc(value: str | None) -> datetime | None`, `parse_salary_period(value: str | None) -> SalaryPeriod | None`

- [ ] **Step 1: Падающие тесты**

`tests/test_base.py`:
```python
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from aijobradar.models import RawJob, SalaryPeriod, SourceStatus
from aijobradar.sources.base import FeedRequest, FeedsFailed, Fetched, fetch_feeds, run_adapter
from aijobradar.sources.common import parse_iso_utc, parse_rfc822_utc, parse_salary_period


def _job(i: int) -> RawJob:
    return RawJob(source="fake", source_job_id=str(i), source_url=f"https://x/{i}",
                  title="Engineer", company="Acme")


@dataclass
class FakeAdapter:
    records: list[Any] = field(default_factory=list)
    error: str | None = None
    raise_exc: Exception | None = None
    name: str = "fake"

    def fetch(self, client: httpx.Client) -> Fetched:
        if self.raise_exc:
            raise self.raise_exc
        return Fetched(self.records, error=self.error)

    def parse_record(self, record: Any) -> RawJob | None:
        if record == "bad":
            raise ValueError("broken record")
        if record == "skip":
            return None
        return _job(int(record))


def _run(adapter: FakeAdapter) -> Any:
    with httpx.Client() as client:
        return run_adapter(adapter, client)


def test_ok_counts_items_invalid_and_out_of_scope() -> None:
    result = _run(FakeAdapter(records=["1", "2", "3", "4", "5", "6", "bad", "skip"]))
    assert result.status is SourceStatus.OK  # 1/8 invalid = 12.5% <= 20%
    assert (len(result.items), result.invalid_items, result.out_of_scope) == (6, 1, 1)


def test_degraded_when_invalid_share_above_threshold() -> None:
    result = _run(FakeAdapter(records=["1", "bad", "bad"]))
    assert result.status is SourceStatus.DEGRADED
    assert result.error is not None and "2/3 invalid" in result.error
    assert "broken record" in result.error


def test_empty_when_no_records() -> None:
    assert _run(FakeAdapter(records=[])).status is SourceStatus.EMPTY


def test_empty_when_everything_out_of_scope() -> None:
    result = _run(FakeAdapter(records=["skip", "skip"]))
    assert result.status is SourceStatus.EMPTY and result.out_of_scope == 2


def test_partial_fetch_error_with_records_is_degraded() -> None:
    result = _run(FakeAdapter(records=["1"], error="page 2: HTTP 429"))
    assert result.status is SourceStatus.DEGRADED and result.error == "page 2: HTTP 429"


def test_fetch_exception_is_failed_never_raised() -> None:
    request = httpx.Request("GET", "https://x")
    exc = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(503, request=request))
    result = _run(FakeAdapter(raise_exc=exc))
    assert result.status is SourceStatus.FAILED
    assert (result.error, result.http_status) == ("HTTP 503", 503)


def test_unexpected_exception_is_failed() -> None:
    result = _run(FakeAdapter(raise_exc=RuntimeError("kaboom")))
    assert result.status is SourceStatus.FAILED and result.error == "RuntimeError: kaboom"


@respx.mock
def test_fetch_feeds_dedups_and_reports_partial_failure() -> None:
    respx.get("https://a/1").mock(return_value=httpx.Response(200, json=[{"id": 1}, {"id": 2}]))
    respx.get("https://a/2").mock(return_value=httpx.Response(200, json=[{"id": 2}, {"id": 3}]))
    respx.get("https://a/3").mock(return_value=httpx.Response(500))
    reqs = [FeedRequest("one", "https://a/1"), FeedRequest("two", "https://a/2"),
            FeedRequest("three", "https://a/3")]
    with httpx.Client() as client:
        fetched = fetch_feeds(client, reqs, extract=lambda r: r.json(), key=lambda rec: str(rec["id"]))
    assert [r["id"] for r in fetched.records] == [1, 2, 3]
    assert (fetched.error, fetched.http_status) == ("three: HTTP 500", 500)


@respx.mock
def test_fetch_feeds_raises_when_all_fail() -> None:
    respx.get("https://a/1").mock(return_value=httpx.Response(403))
    with httpx.Client() as client, pytest.raises(FeedsFailed) as info:
        fetch_feeds(client, [FeedRequest("one", "https://a/1")], extract=lambda r: r.json(),
                    key=lambda rec: str(rec))
    assert info.value.http_status == 403 and "one: HTTP 403" in str(info.value)


def test_date_and_period_helpers() -> None:
    assert parse_iso_utc("2026-09-29T08:00:00+00:00") == datetime(2026, 9, 29, 8, tzinfo=UTC)
    assert parse_iso_utc("2026-09-18T15:10:28") == datetime(2026, 9, 18, 15, 10, 28, tzinfo=UTC)
    assert parse_iso_utc(None) is None
    assert parse_rfc822_utc("Thu, 17 Sep 2026 10:51:23 +0300") == datetime(
        2026, 9, 17, 7, 51, 23, tzinfo=UTC
    )
    assert parse_rfc822_utc("") is None
    assert parse_salary_period("annual") is SalaryPeriod.YEAR
    assert parse_salary_period("yearly") is SalaryPeriod.YEAR
    assert parse_salary_period("Hourly") is SalaryPeriod.HOUR
    assert parse_salary_period("fortnightly") is None
```

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run pytest tests/test_base.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.sources'`

- [ ] **Step 3: Реализация `sources/common.py`**

```python
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from aijobradar.models import SalaryPeriod

_PERIODS = {
    "hourly": SalaryPeriod.HOUR, "hour": SalaryPeriod.HOUR,
    "daily": SalaryPeriod.DAY, "day": SalaryPeriod.DAY,
    "weekly": SalaryPeriod.WEEK, "week": SalaryPeriod.WEEK,
    "monthly": SalaryPeriod.MONTH, "month": SalaryPeriod.MONTH,
    "annual": SalaryPeriod.YEAR, "yearly": SalaryPeriod.YEAR, "year": SalaryPeriod.YEAR,
}


def make_client(user_agent: str, timeout_s: float) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout_s,
                        follow_redirects=True)


def _as_utc(value: datetime) -> datetime:
    # Sources that omit the offset (Remotive-style) are treated as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_iso_utc(value: str | None) -> datetime | None:
    return _as_utc(datetime.fromisoformat(value)) if value else None


def parse_rfc822_utc(value: str | None) -> datetime | None:
    return _as_utc(parsedate_to_datetime(value)) if value else None


def parse_salary_period(value: str | None) -> SalaryPeriod | None:
    # Unknown periods (e.g. "fortnightly") yield None: salary stays, but rate rules skip it.
    return _PERIODS.get((value or "").strip().lower())
```

- [ ] **Step 4: Реализация `sources/base.py`**

```python
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from aijobradar.models import RawJob, SourceResult, SourceStatus

DEGRADED_INVALID_SHARE = 0.2


@dataclass
class Fetched:
    records: list[Any]
    error: str | None = None  # set when part of the fetch failed but some records arrived
    http_status: int | None = None


@dataclass
class FeedRequest:
    label: str
    url: str
    params: dict[str, str | int] | None = field(default=None)


class FeedsFailed(Exception):
    def __init__(self, message: str, http_status: int | None) -> None:
        super().__init__(message)
        self.http_status = http_status


class Adapter(Protocol):
    name: str

    def fetch(self, client: httpx.Client) -> Fetched: ...

    def parse_record(self, record: Any) -> RawJob | None: ...


def describe_error(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, FeedsFailed):
        return str(exc), exc.http_status
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}", exc.response.status_code
    return f"{type(exc).__name__}: {exc}"[:500], None


def fetch_feeds(
    client: httpx.Client,
    requests: Sequence[FeedRequest],
    extract: Callable[[httpx.Response], list[Any]],
    key: Callable[[Any], str],
) -> Fetched:
    """Fetch several feeds of one source; one broken feed degrades, all broken fails."""
    records: list[Any] = []
    seen: set[str] = set()
    errors: list[str] = []
    status: int | None = None
    for req in requests:
        try:
            response = client.get(req.url, params=req.params)
            response.raise_for_status()
            page = extract(response)
        except Exception as exc:  # a feed failure must not abort the other feeds
            message, code = describe_error(exc)
            errors.append(f"{req.label}: {message}")
            status = code or status
            continue
        for record in page:
            record_key = key(record)
            if record_key not in seen:
                seen.add(record_key)
                records.append(record)
    if requests and len(errors) == len(requests):
        raise FeedsFailed("; ".join(errors), status)
    return Fetched(records, error="; ".join(errors) or None, http_status=status)


def run_adapter(adapter: Adapter, client: httpx.Client) -> SourceResult:
    """Run one source end to end. Never raises: every outcome becomes a SourceResult."""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        fetched = adapter.fetch(client)
    except Exception as exc:
        message, code = describe_error(exc)
        return SourceResult(source=adapter.name, status=SourceStatus.FAILED, error=message,
                            http_status=code, duration_ms=elapsed())

    items: list[RawJob] = []
    invalid = out_of_scope = 0
    first_invalid: str | None = None
    for record in fetched.records:
        try:
            job = adapter.parse_record(record)
        except Exception as exc:  # one bad record must not sink the source
            invalid += 1
            first_invalid = first_invalid or f"{type(exc).__name__}: {exc}"[:300]
            continue
        if job is None:
            out_of_scope += 1
        else:
            items.append(job)

    total = len(fetched.records)
    error = fetched.error
    if fetched.error:
        status = SourceStatus.DEGRADED
    elif total and invalid / total > DEGRADED_INVALID_SHARE:
        status = SourceStatus.DEGRADED
        error = f"{invalid}/{total} invalid; first: {first_invalid}"
    elif not items and not invalid:
        status = SourceStatus.EMPTY
    else:
        status = SourceStatus.OK
    return SourceResult(source=adapter.name, status=status, items=items, error=error,
                        http_status=fetched.http_status, duration_ms=elapsed(),
                        invalid_items=invalid, out_of_scope=out_of_scope)
```

`src/aijobradar/sources/__init__.py`: пустой файл (заполняется в Task 10).

- [ ] **Step 5: Тесты проходят**

Run: `uv run pytest tests/test_base.py && uv run ruff check . && uv run mypy src`
Expected: PASS, линтеры чистые.

- [ ] **Step 6: Checkpoint** — не коммитить.

---

### Task 4: Адаптер Himalayas

**Files:**
- Create: `src/aijobradar/sources/himalayas.py`, `tests/fixtures/himalayas/page1.json`, `tests/fixtures/himalayas/page2.json`, `tests/__init__.py` (пустой), `tests/conftest.py` (только хелпер `fixture_path`; DB-фикстуры добавит Task 8), `tests/test_himalayas.py`

**Interfaces:**
- Consumes: `Fetched`, `describe_error`, `parse_salary_period`, `RawJob`
- Produces: `HimalayasAdapter(max_pages: int = 10, lookback_days: int = 3, parent_categories: tuple[str, ...] = ("Developer",), now: Callable[[], datetime] = …)`, `name = "himalayas"`, `BASE_URL = "https://himalayas.app/jobs/api"`

Факты из `docs/sources.md`: обход `GET /jobs/api?limit=20&cursor=…`, курсор в `nextCursor`; параметра категории нет → фильтр по `parentCategories`; `locationRestrictions: []` = без ограничений; все смещения в `timezoneRestrictions` = без ограничений; `pubDate` — unix-секунды; `guid` = `applicationLink` (страница Himalayas — нужна для атрибуции). Порядок выдачи обхода по дате не проверен: остановка по дате — оптимизация, жёсткий предел — `max_pages`.

- [ ] **Step 1: Хелпер фикстур**

`tests/conftest.py`:
```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_path(*parts: str) -> Path:
    return FIXTURES.joinpath(*parts)
```

- [ ] **Step 2: Синтетические фикстуры** (компании и тексты выдуманы)

`tests/fixtures/himalayas/page1.json`:
```json
{
  "comments": "synthetic fixture for tests",
  "updatedAt": 1790640000,
  "offset": 0,
  "limit": 20,
  "totalCount": 6,
  "nextCursor": "cursor-2",
  "jobs": [
    {
      "title": "Senior Backend Engineer",
      "excerpt": "Build billing APIs.",
      "companyName": "Acme Ledger Inc",
      "companySlug": "acme-ledger",
      "companyLogo": "https://example.test/logo.png",
      "employmentType": "Full Time",
      "minSalary": 150000,
      "maxSalary": 190000,
      "salaryPeriod": "annual",
      "seniority": ["Senior"],
      "currency": "USD",
      "locationRestrictions": ["United States"],
      "timezoneRestrictions": [-8, -7, -6, -5],
      "categories": ["Backend-Engineer", "Software-Engineer"],
      "parentCategories": ["Developer"],
      "description": "<p>Build billing APIs in <strong>Node.js</strong>.</p>",
      "pubDate": 1790640000,
      "expiryDate": 1793232000,
      "applicationLink": "https://himalayas.app/companies/acme-ledger/jobs/senior-backend-engineer-1001",
      "guid": "https://himalayas.app/companies/acme-ledger/jobs/senior-backend-engineer-1001"
    },
    {
      "title": "Integrations Engineer",
      "excerpt": "CRM integrations.",
      "companyName": "Northwind Docs",
      "companySlug": "northwind-docs",
      "companyLogo": "https://example.test/logo2.png",
      "employmentType": "Contractor",
      "minSalary": null,
      "maxSalary": null,
      "salaryPeriod": "annual",
      "seniority": ["Mid-level"],
      "currency": null,
      "locationRestrictions": [],
      "timezoneRestrictions": [-11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
      "categories": ["Integrations-Engineer"],
      "parentCategories": ["Developer"],
      "description": "<p>Sync HubSpot &amp; Salesforce data.</p>",
      "pubDate": 1790596800,
      "expiryDate": 1793188800,
      "applicationLink": "https://himalayas.app/companies/northwind-docs/jobs/integrations-engineer-1002",
      "guid": "https://himalayas.app/companies/northwind-docs/jobs/integrations-engineer-1002"
    },
    {
      "title": "Content Marketing Manager",
      "excerpt": "Write.",
      "companyName": "Globex Media",
      "companySlug": "globex-media",
      "companyLogo": "",
      "employmentType": "Full Time",
      "minSalary": null,
      "maxSalary": null,
      "salaryPeriod": "annual",
      "seniority": ["Manager"],
      "currency": null,
      "locationRestrictions": [],
      "timezoneRestrictions": [0],
      "categories": ["Content-Marketing"],
      "parentCategories": ["Marketing"],
      "description": "<p>Write things.</p>",
      "pubDate": 1790640000,
      "expiryDate": 1793232000,
      "applicationLink": "https://himalayas.app/companies/globex-media/jobs/content-marketing-manager-1003",
      "guid": "https://himalayas.app/companies/globex-media/jobs/content-marketing-manager-1003"
    },
    {
      "title": "Full Stack Engineer",
      "excerpt": "Ship features.",
      "companyName": "Initech",
      "companySlug": "initech",
      "companyLogo": "",
      "employmentType": "Full Time",
      "minSalary": 5000,
      "maxSalary": 6500,
      "salaryPeriod": "monthly",
      "seniority": ["Mid-level", "Senior"],
      "currency": "EUR",
      "locationRestrictions": ["Netherlands"],
      "timezoneRestrictions": [1],
      "categories": ["Full-Stack-Engineer"],
      "parentCategories": ["Developer"],
      "description": "<p>React and Node.</p>",
      "pubDate": 1790640000,
      "expiryDate": 1793232000,
      "applicationLink": "https://himalayas.app/companies/initech/jobs/full-stack-engineer-2001",
      "guid": "https://himalayas.app/companies/initech/jobs/full-stack-engineer-2001"
    },
    {
      "excerpt": "Record without a title must count as invalid.",
      "companyName": "Broken Co",
      "parentCategories": ["Developer"],
      "pubDate": 1790640000,
      "applicationLink": "https://himalayas.app/companies/broken/jobs/x-9999",
      "guid": "https://himalayas.app/companies/broken/jobs/x-9999"
    }
  ]
}
```

`tests/fixtures/himalayas/page2.json`:
```json
{
  "comments": "synthetic fixture for tests",
  "updatedAt": 1790640000,
  "offset": 20,
  "limit": 20,
  "totalCount": 6,
  "nextCursor": null,
  "jobs": [
    {
      "title": "Full Stack Engineer",
      "excerpt": "Ship features.",
      "companyName": "Initech",
      "companySlug": "initech",
      "companyLogo": "",
      "employmentType": "Full Time",
      "minSalary": null,
      "maxSalary": null,
      "salaryPeriod": "annual",
      "seniority": ["Mid-level"],
      "currency": null,
      "locationRestrictions": ["United Kingdom"],
      "timezoneRestrictions": [0],
      "categories": ["Full-Stack-Engineer"],
      "parentCategories": ["Developer"],
      "description": "<p>React and Node.</p>",
      "pubDate": 1790640000,
      "expiryDate": 1793232000,
      "applicationLink": "https://himalayas.app/companies/initech/jobs/full-stack-engineer-2002",
      "guid": "https://himalayas.app/companies/initech/jobs/full-stack-engineer-2002"
    }
  ]
}
```

- [ ] **Step 3: Падающие тесты**

`tests/test_himalayas.py`:
```python
import json
from datetime import UTC, datetime

import httpx
import respx

from aijobradar.models import SalaryPeriod, SourceStatus
from aijobradar.sources.base import run_adapter
from aijobradar.sources.himalayas import BASE_URL, HimalayasAdapter
from tests.conftest import fixture_path

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def _page(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads(fixture_path("himalayas", name).read_text())


def _adapter(**kw: object) -> HimalayasAdapter:
    return HimalayasAdapter(now=lambda: NOW, **kw)  # type: ignore[arg-type]


@respx.mock
def test_pages_through_cursor_and_classifies_records() -> None:
    route = respx.get(BASE_URL).mock(
        side_effect=[httpx.Response(200, json=_page("page1.json")),
                     httpx.Response(200, json=_page("page2.json"))]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert route.call_count == 2
    assert route.calls[1].request.url.params["cursor"] == "cursor-2"
    assert result.status is SourceStatus.OK  # 1 invalid of 6 = 16.7%
    assert (len(result.items), result.invalid_items, result.out_of_scope) == (4, 1, 1)


def test_parse_us_only_job_with_salary() -> None:
    job = _adapter().parse_record(_page("page1.json")["jobs"][0])
    assert job is not None
    assert job.source == "himalayas"
    assert job.source_job_id.endswith("senior-backend-engineer-1001")
    assert job.source_url == job.source_job_id
    assert (job.company, job.title) == ("Acme Ledger Inc", "Senior Backend Engineer")
    assert job.location_restrictions == ["United States"]
    assert job.location_text == "United States"
    assert job.timezone_restrictions == [-8.0, -7.0, -6.0, -5.0]
    assert (job.salary_min, job.salary_max, job.salary_currency) == (150000, 190000, "USD")
    assert job.salary_period is SalaryPeriod.YEAR
    assert job.seniority == "Senior"
    assert job.posted_at == datetime(2026, 9, 29, tzinfo=UTC)


def test_parse_worldwide_job_without_salary() -> None:
    job = _adapter().parse_record(_page("page1.json")["jobs"][1])
    assert job is not None
    assert job.location_restrictions == []
    assert job.location_text == "Worldwide"
    assert job.timezone_restrictions is None  # every offset allowed = no restriction
    assert (job.salary_min, job.salary_currency, job.salary_period) == (None, None, None)


def test_non_developer_category_is_out_of_scope() -> None:
    assert _adapter().parse_record(_page("page1.json")["jobs"][2]) is None


@respx.mock
def test_stops_when_page_older_than_lookback() -> None:
    old = _page("page1.json")
    for job in old["jobs"]:
        job["pubDate"] = 1789862400  # 2026-09-20, older than 3-day lookback
    route = respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=old))
    with httpx.Client() as client:
        run_adapter(_adapter(), client)
    assert route.call_count == 1


@respx.mock
def test_second_page_failure_keeps_first_page_as_degraded() -> None:
    respx.get(BASE_URL).mock(
        side_effect=[httpx.Response(200, json=_page("page1.json")), httpx.Response(429)]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.DEGRADED
    assert (result.error, result.http_status) == ("page 2: HTTP 429", 429)
    assert len(result.items) == 3


@respx.mock
def test_first_page_failure_is_failed() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(503))
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert (result.status, result.http_status) == (SourceStatus.FAILED, 503)


@respx.mock
def test_max_pages_is_a_hard_limit() -> None:
    page = _page("page1.json")  # always returns a next cursor
    route = respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=page))
    with httpx.Client() as client:
        run_adapter(_adapter(max_pages=3), client)
    assert route.call_count == 3
```

`tests/__init__.py`: создать пустым, чтобы работал импорт `tests.conftest`.

- [ ] **Step 4: Убедиться, что падают**

Run: `uv run pytest tests/test_himalayas.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.sources.himalayas'`

- [ ] **Step 5: Реализация `sources/himalayas.py`**

```python
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from aijobradar.models import RawJob
from aijobradar.sources.base import Fetched, describe_error
from aijobradar.sources.common import parse_salary_period

BASE_URL = "https://himalayas.app/jobs/api"
PAGE_LIMIT = 20
# 26 whole-hour offsets from UTC-11 to UTC+14: a job allowing all of them has no tz restriction.
_ALL_OFFSETS = 26


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class HimalayasAdapter:
    max_pages: int = 10
    lookback_days: int = 3
    parent_categories: tuple[str, ...] = ("Developer",)
    now: Callable[[], datetime] = field(default=_utcnow)
    name: str = "himalayas"

    def fetch(self, client: httpx.Client) -> Fetched:
        cutoff = (self.now() - timedelta(days=self.lookback_days)).timestamp()
        records: list[Any] = []
        cursor: str | None = None
        for page_no in range(1, self.max_pages + 1):
            params: dict[str, str | int] = {"limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            try:
                response = client.get(BASE_URL, params=params)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                if not records:
                    raise
                message, code = describe_error(exc)
                return Fetched(records, error=f"page {page_no}: {message}", http_status=code)
            page = payload.get("jobs") or []
            records.extend(page)
            cursor = payload.get("nextCursor")
            # Order of the browse endpoint is not guaranteed; max_pages is the real bound.
            newest = max((job.get("pubDate") or 0 for job in page), default=0)
            if not cursor or not page or newest < cutoff:
                break
        return Fetched(records)

    def parse_record(self, record: dict[str, Any]) -> RawJob | None:
        categories = record.get("parentCategories") or []
        if self.parent_categories and not set(categories) & set(self.parent_categories):
            return None
        restrictions = list(record.get("locationRestrictions") or [])
        offsets = [float(x) for x in record.get("timezoneRestrictions") or []]
        has_salary = record.get("minSalary") is not None or record.get("maxSalary") is not None
        pub = record.get("pubDate")
        return RawJob(
            source=self.name,
            source_job_id=str(record["guid"]),
            source_url=record.get("applicationLink") or record["guid"],
            title=record["title"],
            company=record["companyName"],
            location_text=", ".join(restrictions) or "Worldwide",
            location_restrictions=restrictions,
            timezone_restrictions=offsets if 0 < len(set(map(int, offsets))) < _ALL_OFFSETS
            else None,
            employment_type=record.get("employmentType"),
            seniority=", ".join(record.get("seniority") or []) or None,
            salary_min=record.get("minSalary"),
            salary_max=record.get("maxSalary"),
            salary_currency=record.get("currency") if has_salary else None,
            salary_period=parse_salary_period(record.get("salaryPeriod")) if has_salary else None,
            posted_at=datetime.fromtimestamp(pub, UTC) if pub else None,
            description_html=record.get("description") or "",
            tags=list(record.get("categories") or []),
        )
```

Примечание по `timezone_restrictions`: реальные данные содержат дробные смещения (`-9.5`, `-3.5`), поэтому «все смещения» определяется по числу **целых** часов (26 = от −11 до +14).

- [ ] **Step 6: Тесты проходят**

Run: `uv run pytest tests/test_himalayas.py && uv run ruff check . && uv run mypy src`
Expected: PASS.

- [ ] **Step 7: Checkpoint** — не коммитить.

---

### Task 5: Адаптер Jobicy

**Files:**
- Create: `src/aijobradar/sources/jobicy.py`, `tests/fixtures/jobicy/engineering.json`, `tests/test_jobicy.py`

**Interfaces:**
- Consumes: `fetch_feeds`, `FeedRequest`, `parse_iso_utc`, `parse_salary_period`, `RawJob`
- Produces: `JobicyAdapter(count: int = 50, industries: tuple[str, ...] = ("engineering",))`, `name = "jobicy"`, `API_URL = "https://jobicy.com/api/v2/remote-jobs"`

Факты: пагинации нет (последние N ≤ 200); ключи зарплаты **отсутствуют**, а не `null`; `jobGeo` — свободный текст с двойными пробелами (`"Europe,  Ukraine"`); `jobLevel = "Any"` = не указан; `success: false` в ответе = отказ.

- [ ] **Step 1: Синтетическая фикстура**

`tests/fixtures/jobicy/engineering.json`:
```json
{
  "apiVersion": "2.2.16",
  "documentationUrl": "https://example.test/apidocs",
  "friendlyNotice": "synthetic fixture for tests",
  "jobCount": 2,
  "lastUpdate": "2026-09-30T04:00:00+00:00",
  "appliedFilters": {"count": 50, "industry": "engineering"},
  "jobs": [
    {
      "id": 900001,
      "url": "https://jobicy.com/jobs/900001-senior-full-stack-engineer",
      "jobSlug": "900001-senior-full-stack-engineer",
      "jobTitle": "Senior Full-Stack Engineer (React/Node)",
      "companyName": "Umbrella Billing ",
      "companyLogo": "https://example.test/umbrella.png",
      "jobIndustry": ["Software Engineering"],
      "jobType": ["Full-Time"],
      "jobGeo": "Europe,  Armenia",
      "jobLevel": "Senior",
      "jobExcerpt": "Invoices and contracts&hellip;",
      "jobDescription": "<h3>About</h3><p>Invoices &amp; contracts.</p>",
      "pubDate": "2026-09-29T08:00:00+00:00",
      "salaryMin": 60000,
      "salaryMax": 80000,
      "salaryCurrency": "EUR",
      "salaryPeriod": "yearly"
    },
    {
      "id": 900002,
      "url": "https://jobicy.com/jobs/900002-qa-automation-engineer",
      "jobSlug": "900002-qa-automation-engineer",
      "jobTitle": "QA Automation Engineer",
      "companyName": "Hooli",
      "companyLogo": "https://example.test/hooli.png",
      "jobIndustry": ["QA & Testing"],
      "jobType": ["Full-Time", "Contract"],
      "jobGeo": "Anywhere",
      "jobLevel": "Any",
      "jobExcerpt": "Playwright&hellip;",
      "jobDescription": "<p>Playwright and CI.</p>",
      "pubDate": "2026-09-29T09:30:00+00:00"
    }
  ],
  "statusCode": 200,
  "success": true
}
```

- [ ] **Step 2: Падающие тесты**

`tests/test_jobicy.py`:
```python
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from aijobradar.models import SalaryPeriod, SourceStatus
from aijobradar.sources.base import run_adapter
from aijobradar.sources.jobicy import API_URL, JobicyAdapter
from tests.conftest import fixture_path


def _payload() -> dict:  # type: ignore[type-arg]
    return json.loads(fixture_path("jobicy", "engineering.json").read_text())


@respx.mock
def test_fetch_and_parse_ok() -> None:
    route = respx.get(API_URL).mock(return_value=httpx.Response(200, json=_payload()))
    with httpx.Client() as client:
        result = run_adapter(JobicyAdapter(), client)
    assert route.calls[0].request.url.params["industry"] == "engineering"
    assert route.calls[0].request.url.params["count"] == "50"
    assert result.status is SourceStatus.OK
    assert [j.source_job_id for j in result.items] == ["900001", "900002"]


def test_parse_job_with_salary_and_geo_list() -> None:
    job = JobicyAdapter().parse_record(_payload()["jobs"][0])
    assert job is not None
    assert job.company == "Umbrella Billing"
    assert job.source_url == "https://jobicy.com/jobs/900001-senior-full-stack-engineer"
    assert job.location_text == "Europe, Armenia"
    assert job.location_restrictions == ["Europe", "Armenia"]
    assert job.seniority == "Senior"
    assert (job.salary_min, job.salary_max, job.salary_currency) == (60000, 80000, "EUR")
    assert job.salary_period is SalaryPeriod.YEAR
    assert job.posted_at == datetime(2026, 9, 29, 8, tzinfo=UTC)
    assert job.tags == ["Software Engineering"]


def test_parse_anywhere_job_without_salary_keys() -> None:
    job = JobicyAdapter().parse_record(_payload()["jobs"][1])
    assert job is not None
    assert job.location_restrictions == []
    assert job.seniority is None
    assert job.employment_type == "Full-Time, Contract"
    assert (job.salary_min, job.salary_currency, job.salary_period) == (None, None, None)


def test_record_without_title_is_invalid() -> None:
    record = _payload()["jobs"][0]
    del record["jobTitle"]
    with pytest.raises(KeyError):
        JobicyAdapter().parse_record(record)


@respx.mock
def test_success_false_is_failed() -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(200, json={"success": False,
                                                                   "statusCode": 400}))
    with httpx.Client() as client:
        result = run_adapter(JobicyAdapter(), client)
    assert result.status is SourceStatus.FAILED
    assert result.error is not None and "success=false" in result.error


@respx.mock
def test_industries_are_merged_without_duplicates() -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(200, json=_payload()))
    with httpx.Client() as client:
        result = run_adapter(JobicyAdapter(industries=("engineering", "dev")), client)
    assert len(result.items) == 2
```

- [ ] **Step 3: Убедиться, что падают**

Run: `uv run pytest tests/test_jobicy.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.sources.jobicy'`

- [ ] **Step 4: Реализация `sources/jobicy.py`**

```python
from dataclasses import dataclass
from typing import Any

import httpx

from aijobradar.models import RawJob
from aijobradar.sources.base import FeedRequest, Fetched, fetch_feeds
from aijobradar.sources.common import parse_iso_utc, parse_salary_period

API_URL = "https://jobicy.com/api/v2/remote-jobs"
_NO_RESTRICTION = frozenset({"", "anywhere", "worldwide"})


def _extract(response: httpx.Response) -> list[Any]:
    payload = response.json()
    if payload.get("success") is False:
        raise ValueError(f"jobicy success=false (statusCode={payload.get('statusCode')})")
    return list(payload.get("jobs") or [])


@dataclass
class JobicyAdapter:
    count: int = 50
    industries: tuple[str, ...] = ("engineering",)
    name: str = "jobicy"

    def fetch(self, client: httpx.Client) -> Fetched:
        requests = [
            FeedRequest(industry, API_URL, {"count": self.count, "industry": industry})
            for industry in self.industries
        ]
        return fetch_feeds(client, requests, extract=_extract, key=lambda rec: str(rec.get("id")))

    def parse_record(self, record: dict[str, Any]) -> RawJob | None:
        geo = " ".join(str(record.get("jobGeo") or "").split())
        restrictions = (
            [] if geo.casefold() in _NO_RESTRICTION
            else [part.strip() for part in geo.split(",") if part.strip()]
        )
        level = record.get("jobLevel")
        return RawJob(
            source=self.name,
            source_job_id=str(record["id"]),
            source_url=record["url"],
            title=record["jobTitle"],
            company=record["companyName"],
            location_text=geo or None,
            location_restrictions=restrictions,
            employment_type=", ".join(record.get("jobType") or []) or None,
            seniority=None if level in (None, "", "Any") else level,
            salary_min=record.get("salaryMin"),
            salary_max=record.get("salaryMax"),
            salary_currency=record.get("salaryCurrency"),
            salary_period=parse_salary_period(record.get("salaryPeriod")),
            posted_at=parse_iso_utc(record.get("pubDate")),
            description_html=record.get("jobDescription") or "",
            tags=list(record.get("jobIndustry") or []),
        )
```

- [ ] **Step 5: Тесты проходят**

Run: `uv run pytest tests/test_jobicy.py && uv run ruff check . && uv run mypy src`
Expected: PASS.

- [ ] **Step 6: Checkpoint** — не коммитить.

---

### Task 6: Адаптер We Work Remotely (RSS)

**Files:**
- Create: `src/aijobradar/sources/wwr.py`, `tests/fixtures/wwr/fullstack.rss`, `tests/fixtures/wwr/backend.rss`, `tests/test_wwr.py`

**Interfaces:**
- Consumes: `fetch_feeds`, `FeedRequest`, `parse_rfc822_utc`, `RawJob`
- Produces: `WwrAdapter(feeds: tuple[str, ...] = ("remote-full-stack-programming-jobs", "remote-back-end-programming-jobs", "remote-programming-jobs"))`, `name = "wwr"`, `FEED_URL = "https://weworkremotely.com/categories/{slug}.rss"`, `split_countries(value: str) -> list[str]`

Факты: `title` = `"Компания: Роль"`; `country` — список с эмодзи-флагами, разделители `", "`, `", and "`, `" and "`; в названиях стран бывает « and » («Bosnia and Herzegovina»), поэтому делим **по флагам**, а не по «and»; ленты пересекаются — дедуп по `guid`.

- [ ] **Step 1: Синтетические фикстуры**

`tests/fixtures/wwr/fullstack.rss`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss">
  <channel>
    <title>We Work Remotely: Full-Stack Programming Jobs (synthetic fixture)</title>
    <ttl>60</ttl>
    <item>
      <title>Northwind Docs: Integrations Engineer</title>
      <region>Anywhere in the World</region>
      <country>🇦🇲 Armenia, 🇩🇪 Germany, and 🇺🇦 Ukraine</country>
      <state></state>
      <skills>TypeScript, Node.js, and PostgreSQL</skills>
      <category>Full-Stack Programming</category>
      <type>Contract</type>
      <description>&lt;p&gt;&lt;strong&gt;Headquarters:&lt;/strong&gt; Remote&lt;/p&gt;&lt;p&gt;Sync CRM data.&lt;/p&gt;</description>
      <pubDate>Tue, 29 Sep 2026 10:00:00 +0000</pubDate>
      <expires_at>Thu, 29 Oct 2026 10:00:00 +0000</expires_at>
      <guid>https://weworkremotely.com/remote-jobs/northwind-docs-integrations-engineer</guid>
      <link>https://weworkremotely.com/remote-jobs/northwind-docs-integrations-engineer</link>
    </item>
    <item>
      <title>Acme Ledger: Senior Backend Engineer</title>
      <region>North America Only</region>
      <country>🇨🇦 Canada and 🇺🇸 United States of America</country>
      <state>New York</state>
      <skills></skills>
      <category>Full-Stack Programming</category>
      <type>Full-Time</type>
      <description>&lt;p&gt;Location: Remote - US&lt;/p&gt;</description>
      <pubDate>Mon, 28 Sep 2026 12:00:00 +0000</pubDate>
      <expires_at>Wed, 28 Oct 2026 12:00:00 +0000</expires_at>
      <guid>https://weworkremotely.com/remote-jobs/acme-ledger-senior-backend-engineer</guid>
      <link>https://weworkremotely.com/remote-jobs/acme-ledger-senior-backend-engineer</link>
    </item>
  </channel>
</rss>
```

`tests/fixtures/wwr/backend.rss`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss">
  <channel>
    <title>We Work Remotely: Back-End Programming Jobs (synthetic fixture)</title>
    <item>
      <title>Acme Ledger: Senior Backend Engineer</title>
      <region>North America Only</region>
      <country>🇨🇦 Canada and 🇺🇸 United States of America</country>
      <type>Full-Time</type>
      <description>&lt;p&gt;Location: Remote - US&lt;/p&gt;</description>
      <pubDate>Mon, 28 Sep 2026 12:00:00 +0000</pubDate>
      <guid>https://weworkremotely.com/remote-jobs/acme-ledger-senior-backend-engineer</guid>
      <link>https://weworkremotely.com/remote-jobs/acme-ledger-senior-backend-engineer</link>
    </item>
    <item>
      <title>Hooli: Platform Engineer</title>
      <region>Europe Only</region>
      <country>🇧🇦 Bosnia and Herzegovina, 🇸🇰 Slovakia, and 🇺🇦 Ukraine</country>
      <type>Full-Time</type>
      <description>&lt;p&gt;Kubernetes.&lt;/p&gt;</description>
      <pubDate>Tue, 29 Sep 2026 08:00:00 +0000</pubDate>
      <guid>https://weworkremotely.com/remote-jobs/hooli-platform-engineer</guid>
      <link>https://weworkremotely.com/remote-jobs/hooli-platform-engineer</link>
    </item>
  </channel>
</rss>
```

- [ ] **Step 2: Падающие тесты**

`tests/test_wwr.py`:
```python
from datetime import UTC, datetime
from xml.etree.ElementTree import Element, SubElement

import httpx
import pytest
import respx

from aijobradar.models import SourceStatus
from aijobradar.sources.base import run_adapter
from aijobradar.sources.wwr import FEED_URL, WwrAdapter, split_countries
from tests.conftest import fixture_path

FULL = FEED_URL.format(slug="remote-full-stack-programming-jobs")
BACK = FEED_URL.format(slug="remote-back-end-programming-jobs")


def _mock_feeds() -> None:
    respx.get(FULL).mock(return_value=httpx.Response(
        200, content=fixture_path("wwr", "fullstack.rss").read_bytes()))
    respx.get(BACK).mock(return_value=httpx.Response(
        200, content=fixture_path("wwr", "backend.rss").read_bytes()))


@respx.mock
def test_feeds_are_merged_and_deduplicated_by_guid() -> None:
    _mock_feeds()
    adapter = WwrAdapter(feeds=("remote-full-stack-programming-jobs",
                                "remote-back-end-programming-jobs"))
    with httpx.Client() as client:
        result = run_adapter(adapter, client)
    assert result.status is SourceStatus.OK
    assert [j.company for j in result.items] == ["Northwind Docs", "Acme Ledger", "Hooli"]


@respx.mock
def test_parse_item_fields() -> None:
    _mock_feeds()
    adapter = WwrAdapter(feeds=("remote-full-stack-programming-jobs",))
    with httpx.Client() as client:
        job = run_adapter(adapter, client).items[0]
    assert (job.company, job.title) == ("Northwind Docs", "Integrations Engineer")
    assert job.source_url.endswith("northwind-docs-integrations-engineer")
    assert job.location_restrictions == ["Armenia", "Germany", "Ukraine"]
    assert job.location_text == "Anywhere in the World, Armenia, Germany, Ukraine"
    assert job.employment_type == "Contract"
    assert job.tags == ["TypeScript", "Node.js", "PostgreSQL"]
    assert job.posted_at == datetime(2026, 9, 29, 10, tzinfo=UTC)
    assert "Sync CRM data." in job.description_html


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("🇧🇦 Bosnia and Herzegovina, 🇸🇰 Slovakia, and 🇺🇦 Ukraine",
         ["Bosnia and Herzegovina", "Slovakia", "Ukraine"]),
        ("🇨🇦 Canada and 🇺🇸 United States of America", ["Canada", "United States of America"]),
        ("", []),
        ("Germany", ["Germany"]),
    ],
)
def test_split_countries(value: str, expected: list[str]) -> None:
    assert split_countries(value) == expected


def test_title_without_company_separator_is_invalid() -> None:
    item = Element("item")
    SubElement(item, "title").text = "Just a title"
    SubElement(item, "guid").text = "https://weworkremotely.com/remote-jobs/x"
    SubElement(item, "link").text = "https://weworkremotely.com/remote-jobs/x"
    with pytest.raises(ValueError, match="separator"):
        WwrAdapter().parse_record(item)


@respx.mock
def test_one_feed_blocked_is_degraded() -> None:
    _mock_feeds()
    respx.get(FEED_URL.format(slug="remote-programming-jobs")).mock(
        return_value=httpx.Response(403))
    with httpx.Client() as client:
        result = run_adapter(WwrAdapter(), client)
    assert result.status is SourceStatus.DEGRADED
    assert result.error == "remote-programming-jobs: HTTP 403"
    assert len(result.items) == 3
```

- [ ] **Step 3: Убедиться, что падают**

Run: `uv run pytest tests/test_wwr.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.sources.wwr'`

- [ ] **Step 4: Реализация `sources/wwr.py`**

```python
import re
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as SafeET

from aijobradar.models import RawJob
from aijobradar.sources.base import FeedRequest, Fetched, fetch_feeds
from aijobradar.sources.common import parse_rfc822_utc

FEED_URL = "https://weworkremotely.com/categories/{slug}.rss"
_FLAG = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")  # regional-indicator pair = one flag
_TRAILING_SEPARATOR = re.compile(r"(?:,\s*and|,|\s+and)\s*$")
_SKILL_SEPARATOR = re.compile(r",\s*(?:and\s+)?|\s+and\s+")


def split_countries(value: str) -> list[str]:
    """Split WWR's flag-prefixed list. Split on flags, not on 'and' (Bosnia and Herzegovina)."""
    names = []
    for chunk in _FLAG.split(value):
        name = _TRAILING_SEPARATOR.sub("", chunk.strip()).strip()
        if name:
            names.append(name)
    return names


def _text(item: Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def _extract(response: httpx.Response) -> list[Any]:
    root = SafeET.fromstring(response.content)
    return list(root.iter("item"))


@dataclass
class WwrAdapter:
    feeds: tuple[str, ...] = (
        "remote-full-stack-programming-jobs",
        "remote-back-end-programming-jobs",
        "remote-programming-jobs",
    )
    name: str = "wwr"

    def fetch(self, client: httpx.Client) -> Fetched:
        requests = [FeedRequest(slug, FEED_URL.format(slug=slug)) for slug in self.feeds]
        return fetch_feeds(client, requests, extract=_extract,
                           key=lambda item: _text(item, "guid") or _text(item, "link"))

    def parse_record(self, item: Element) -> RawJob | None:
        company, separator, title = _text(item, "title").partition(": ")
        if not separator:
            raise ValueError("title lacks the 'Company: Role' separator")
        link = _text(item, "link") or _text(item, "guid")
        region = _text(item, "region")
        countries = split_countries(_text(item, "country"))
        location = ", ".join(part for part in (region, ", ".join(countries)) if part)
        skills = [s.strip() for s in _SKILL_SEPARATOR.split(_text(item, "skills")) if s.strip()]
        return RawJob(
            source=self.name,
            source_job_id=_text(item, "guid") or link,
            source_url=link,
            title=title,
            company=company,
            location_text=location or None,
            location_restrictions=countries,
            employment_type=_text(item, "type") or None,
            posted_at=parse_rfc822_utc(_text(item, "pubDate")),
            description_html=_text(item, "description"),
            tags=skills,
        )
```

- [ ] **Step 5: Тесты проходят**

Run: `uv run pytest tests/test_wwr.py && uv run ruff check . && uv run mypy src`
Expected: PASS.

- [ ] **Step 6: Checkpoint** — не коммитить.

---

### Task 7: Нормализация

**Files:**
- Create: `src/aijobradar/normalize.py`, `tests/test_normalize.py`

**Interfaces:**
- Consumes: `RawJob`, `html_to_text`, `normalize_company`, `normalize_title`, `canonical_url`, `content_hash`
- Produces: `NormalizedJob(BaseModel)`: `raw: RawJob`, `company_norm: str`, `title_norm: str`, `description_text: str`, `apply_url_canonical: str`, `content_hash: str`; `normalize(raw: RawJob) -> NormalizedJob`

- [ ] **Step 1: Падающие тесты**

`tests/test_normalize.py`:
```python
from aijobradar.models import RawJob
from aijobradar.normalize import normalize


def _raw(**overrides: object) -> RawJob:
    data: dict[str, object] = {
        "source": "jobicy", "source_job_id": "1",
        "source_url": "https://jobicy.com/jobs/1-x/?utm_source=feed",
        "title": "Senior Full-Stack Engineer (React/Node)", "company": "Umbrella Billing Ltd",
        "description_html": "<p>Invoices &amp; contracts.</p>",
    }
    data.update(overrides)
    return RawJob.model_validate(data)


def test_normalize_fills_derived_fields() -> None:
    job = normalize(_raw())
    assert job.company_norm == "umbrella billing"
    assert job.title_norm == "senior fullstack engineer"
    assert job.description_text == "Invoices & contracts."
    assert job.apply_url_canonical == "https://jobicy.com/jobs/1-x"
    assert len(job.content_hash) == 64
    assert job.raw.company == "Umbrella Billing Ltd"


def test_content_hash_changes_with_description_only() -> None:
    a = normalize(_raw())
    b = normalize(_raw(description_html="<p>Different text.</p>"))
    c = normalize(_raw(source="himalayas", source_job_id="zzz"))
    assert a.content_hash != b.content_hash
    assert a.content_hash == c.content_hash  # same posting from another source hashes equal
```

- [ ] **Step 2: Убедиться, что падают**

Run: `uv run pytest tests/test_normalize.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.normalize'`

- [ ] **Step 3: Реализация `normalize.py`**

```python
from pydantic import BaseModel

from aijobradar.models import RawJob
from aijobradar.text import (
    canonical_url,
    content_hash,
    html_to_text,
    normalize_company,
    normalize_title,
)


class NormalizedJob(BaseModel):
    raw: RawJob
    company_norm: str
    title_norm: str
    description_text: str
    apply_url_canonical: str
    content_hash: str  # identity of the posting's content, independent of the source


def normalize(raw: RawJob) -> NormalizedJob:
    company = normalize_company(raw.company)
    title = normalize_title(raw.title)
    text = html_to_text(raw.description_html)
    return NormalizedJob(
        raw=raw,
        company_norm=company,
        title_norm=title,
        description_text=text,
        apply_url_canonical=canonical_url(raw.source_url),
        content_hash=content_hash(company, title, text),
    )
```

- [ ] **Step 4: Тесты проходят**

Run: `uv run pytest tests/test_normalize.py && uv run ruff check . && uv run mypy src`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — не коммитить.

---

### Task 8: Схема БД, Alembic, тестовая БД

**Files:**
- Create: `src/aijobradar/config.py`, `src/aijobradar/db/__init__.py`, `src/aijobradar/db/models.py`, `src/aijobradar/db/session.py`, `src/aijobradar/db/migrate.py`, `src/aijobradar/db/alembic/env.py`, `src/aijobradar/db/alembic/script.py.mako`, `src/aijobradar/db/alembic/versions/0001_initial.py`, `alembic.ini`, `config/sources.yaml`, `tests/test_config.py`, `tests/test_db_schema.py`
- Modify: `tests/conftest.py` (DB-фикстуры), `src/aijobradar/cli.py` (`db upgrade`)

**Interfaces:**
- Produces:
  - `config.Settings` (env/.env): `database_url: str`, `user_agent: str = "AiJobRadar/0.1 (personal job monitor)"`, `http_timeout_s: float = 30.0`, свойство `sqlalchemy_url: str`
  - `config.to_sqlalchemy_url(url: str) -> str`
  - `config.HimalayasConfig`, `JobicyConfig`, `WwrConfig`, `DedupConfig(title_similarity: int = 92, match_window_days: int = 60)`, `AppConfig(himalayas, jobicy, wwr, dedup)`, `load_config(path: Path) -> AppConfig`
  - `db.models.Base`, `Run`, `SourceRun`, `Job`, `JobSource` (колонки ниже)
  - `db.session.make_engine(url: str) -> Engine`
  - `db.migrate.alembic_config(url: str) -> Config`, `db.migrate.upgrade(url: str) -> None`
  - фикстуры pytest: `engine` (session-scope, схема накатывается миграциями), `session` (транзакция откатывается после теста)

- [ ] **Step 1: Падающие тесты конфигурации**

`tests/test_config.py`:
```python
from pathlib import Path

from aijobradar.config import AppConfig, load_config, to_sqlalchemy_url


def test_neon_url_gets_psycopg_driver() -> None:
    url = "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require"
    assert to_sqlalchemy_url(url) == (
        "postgresql+psycopg://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require"
    )
    assert to_sqlalchemy_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert to_sqlalchemy_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"


def test_repo_config_loads() -> None:
    cfg = load_config(Path(__file__).parent.parent / "config" / "sources.yaml")
    assert cfg.himalayas.parent_categories == ["Developer"]
    assert cfg.jobicy.industries == ["engineering"]
    assert "remote-back-end-programming-jobs" in cfg.wwr.feeds
    assert (cfg.dedup.title_similarity, cfg.dedup.match_window_days) == (92, 60)


def test_empty_config_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("")
    assert load_config(path) == AppConfig()
```

- [ ] **Step 2: Реализация `config.py` и `config/sources.yaml`**

`src/aijobradar/config.py`:
```python
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def to_sqlalchemy_url(url: str) -> str:
    """Neon and most providers hand out libpq URLs; SQLAlchemy needs the psycopg driver name."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    user_agent: str = "AiJobRadar/0.1 (personal job monitor)"
    http_timeout_s: float = 30.0

    @property
    def sqlalchemy_url(self) -> str:
        return to_sqlalchemy_url(self.database_url)


class HimalayasConfig(BaseModel):
    enabled: bool = True
    max_pages: int = 10
    lookback_days: int = 3
    parent_categories: list[str] = Field(default_factory=lambda: ["Developer"])


class JobicyConfig(BaseModel):
    enabled: bool = True
    count: int = 50
    industries: list[str] = Field(default_factory=lambda: ["engineering"])


class WwrConfig(BaseModel):
    enabled: bool = True
    feeds: list[str] = Field(default_factory=lambda: [
        "remote-full-stack-programming-jobs",
        "remote-back-end-programming-jobs",
        "remote-programming-jobs",
    ])


class DedupConfig(BaseModel):
    title_similarity: int = 92  # rapidfuzz token_sort_ratio threshold
    match_window_days: int = 60  # also covers reposts


class AppConfig(BaseModel):
    himalayas: HimalayasConfig = Field(default_factory=HimalayasConfig)
    jobicy: JobicyConfig = Field(default_factory=JobicyConfig)
    wwr: WwrConfig = Field(default_factory=WwrConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)


def load_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(path.read_text()) or {})
```

`config/sources.yaml`:
```yaml
# Source settings. Terms of use and rationale per source: docs/sources.md
himalayas:
  enabled: true
  max_pages: 10        # 20 jobs per page; Himalayas caches data for 24h, so one sync a day
  lookback_days: 3
  parent_categories: [Developer]
jobicy:
  enabled: true
  count: 50
  industries: [engineering]
wwr:
  enabled: true
  feeds:
    - remote-full-stack-programming-jobs
    - remote-back-end-programming-jobs
    - remote-programming-jobs
dedup:
  title_similarity: 92
  match_window_days: 60
```

Run: `uv run pytest tests/test_config.py`
Expected: PASS.

- [ ] **Step 3: Модели БД**

`src/aijobradar/db/__init__.py`: пустой.

`src/aijobradar/db/models.py`:
```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(16))
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SourceRun(Base):
    __tablename__ = "source_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    item_count: Mapped[int] = mapped_column(Integer)
    invalid_items: Mapped[int] = mapped_column(Integer)
    out_of_scope: Mapped[int] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          default=uuid.uuid4)
    content_hash: Mapped[str] = mapped_column(String(64))
    company_raw: Mapped[str] = mapped_column(Text)
    company_norm: Mapped[str] = mapped_column(Text, index=True)
    title_raw: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    location_restrictions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    timezone_restrictions: Mapped[list[float] | None] = mapped_column(JSONB)
    employment_type: Mapped[str | None] = mapped_column(Text)
    seniority: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    salary_period: Mapped[str | None] = mapped_column(String(8))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description_text: Mapped[str] = mapped_column(Text)
    apply_url_canonical: Mapped[str] = mapped_column(Text, index=True)
    first_seen_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(16), default="new")


class JobSource(Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_job_sources_source_job"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"),
                                              index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_job_id: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

`src/aijobradar/db/session.py`:
```python
from sqlalchemy import Engine, create_engine


def make_engine(url: str) -> Engine:
    # pre_ping: Neon suspends idle computes; a dead pooled connection must not fail the run.
    return create_engine(url, pool_pre_ping=True)
```

- [ ] **Step 4: Alembic**

`alembic.ini` (в корне — для `uv run alembic revision --autogenerate` при разработке):
```ini
[alembic]
script_location = src/aijobradar/db/alembic
```

`src/aijobradar/db/migrate.py`:
```python
from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    # configparser treats "%" as interpolation; URL-encoded passwords contain it.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str) -> None:
    command.upgrade(alembic_config(url), "head")
```

`src/aijobradar/db/alembic/env.py`:
```python
from alembic import context
from sqlalchemy import create_engine, pool

from aijobradar.config import Settings
from aijobradar.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or Settings().sqlalchemy_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported")
run_migrations_online()
```

`src/aijobradar/db/alembic/script.py.mako`:
```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`src/aijobradar/db/alembic/versions/0001_initial.py`:
```python
"""initial schema: runs, source_runs, jobs, job_sources

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "source_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("invalid_items", sa.Integer(), nullable=False),
        sa.Column("out_of_scope", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("company_raw", sa.Text(), nullable=False),
        sa.Column("company_norm", sa.Text(), nullable=False),
        sa.Column("title_raw", sa.Text(), nullable=False),
        sa.Column("title_norm", sa.Text(), nullable=False),
        sa.Column("location_text", sa.Text(), nullable=True),
        sa.Column("location_restrictions", postgresql.JSONB(), nullable=False),
        sa.Column("timezone_restrictions", postgresql.JSONB(), nullable=True),
        sa.Column("employment_type", sa.Text(), nullable=True),
        sa.Column("seniority", sa.Text(), nullable=True),
        sa.Column("salary_min", sa.Float(), nullable=True),
        sa.Column("salary_max", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.String(8), nullable=True),
        sa.Column("salary_period", sa.String(8), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=False),
        sa.Column("apply_url_canonical", sa.Text(), nullable=False),
        sa.Column("first_seen_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
    )
    op.create_index("ix_jobs_company_norm", "jobs", ["company_norm"])
    op.create_index("ix_jobs_apply_url_canonical", "jobs", ["apply_url_canonical"])
    op.create_index("ix_jobs_last_seen_at", "jobs", ["last_seen_at"])
    op.create_table(
        "job_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_job_id", name="uq_job_sources_source_job"),
    )
    op.create_index("ix_job_sources_job_id", "job_sources", ["job_id"])


def downgrade() -> None:
    op.drop_table("job_sources")
    op.drop_table("jobs")
    op.drop_table("source_runs")
    op.drop_table("runs")
```

- [ ] **Step 5: DB-фикстуры — заменить `tests/conftest.py` целиком** (хелпер `fixture_path` сохраняется)

```python
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from aijobradar.db.migrate import upgrade

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://localhost/aijobradar_test"
)


def fixture_path(*parts: str) -> Path:
    return FIXTURES.joinpath(*parts)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    # The schema is dropped below: refuse anything that is not an explicit test database.
    if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
        pytest.fail(f"TEST_DATABASE_URL must point to a *_test database, got {TEST_DATABASE_URL}")
    eng = create_engine(TEST_DATABASE_URL)
    try:
        with eng.begin() as conn:
            # Two statements, two calls: psycopg 3 rejects multi-statement prepared queries.
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as exc:
        if os.environ.get("REQUIRE_DB"):
            raise
        pytest.skip(f"Postgres unavailable ({exc.__class__.__name__}); set TEST_DATABASE_URL")
    upgrade(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield db
    db.close()
    transaction.rollback()
    connection.close()
```

- [ ] **Step 6: Падающий тест схемы**

`tests/test_db_schema.py`:
```python
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from aijobradar.db.models import Base


def test_migrations_create_all_tables(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"runs", "source_runs", "jobs", "job_sources", "alembic_version"} <= tables


def test_models_match_migrations(engine: Engine) -> None:
    # Guards against editing a model without writing a migration (or vice versa).
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
```

Run: `uv run pytest tests/test_db_schema.py -v`
Expected: PASS (миграции уже написаны в Step 4). Если `test_models_match_migrations` показывает расхождения — привести модель и миграцию к одному виду (имена индексов, nullable, типы), не отключая тест.

Проверка изоляции: `uv run pytest tests/test_db_schema.py` с `TEST_DATABASE_URL=postgresql+psycopg://localhost/aijobradar` должен **упасть** с сообщением про `*_test`.

- [ ] **Step 7: Подключить `db upgrade` в CLI**

В `src/aijobradar/cli.py` заменить заглушку:
```python
@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations to DATABASE_URL."""
    from aijobradar.config import Settings
    from aijobradar.db.migrate import upgrade

    upgrade(Settings().sqlalchemy_url)
    typer.echo("Миграции применены.")
```

Run: `DATABASE_URL=postgresql://localhost/aijobradar_test uv run aijobradar db upgrade`
Expected: `Миграции применены.`

- [ ] **Step 8: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run mypy src`
Expected: все тесты PASS.

- [ ] **Step 9: Checkpoint** — не коммитить.

---

### Task 9: Дедупликация и запись в БД

**Files:**
- Create: `src/aijobradar/dedup.py`, `src/aijobradar/store.py`, `src/aijobradar/ingest.py`, `tests/test_dedup.py`, `tests/test_ingest.py`

**Interfaces:**
- Consumes: `NormalizedJob`, `normalize`, `RawJob`, `SourceResult`, `DedupConfig`, `db.models.*`, фикстура `session`
- Produces:
  - `dedup.DedupOutcome(StrEnum)`: `SEEN="seen"`, `MERGED="merged"`, `NEW="new"`
  - `dedup.Candidate(job_id: uuid.UUID, title_norm: str)` (frozen dataclass)
  - `dedup.best_fuzzy_match(title_norm: str, candidates: Sequence[Candidate], threshold: int) -> Candidate | None`
  - `store.start_run(session, kind: str, now: datetime) -> Run`
  - `store.finish_run(session, run: Run, status: str, counts: dict[str, int], now: datetime) -> None`
  - `store.record_source_result(session, run_id: uuid.UUID, result: SourceResult) -> None`
  - `store.find_source(session, source: str, source_job_id: str) -> JobSource | None`
  - `store.find_job_id_by_url(session, url: str, since: datetime) -> uuid.UUID | None`
  - `store.company_candidates(session, company_norm: str, since: datetime) -> list[Candidate]`
  - `store.insert_job(session, job: NormalizedJob, run_id: uuid.UUID, now: datetime) -> Job`
  - `store.attach_source(session, job_id: uuid.UUID, raw: RawJob, now: datetime) -> None`
  - `store.touch_source(session, link: JobSource, now: datetime) -> None`
  - `ingest.ingest(session, job: NormalizedJob, *, run_id: uuid.UUID, now: datetime, cfg: DedupConfig) -> DedupOutcome`

Порядок решения (спецификация §6.2 с уточнениями этого плана):
1. `(source, source_job_id)` уже есть → `SEEN`, обновить `last_seen_at` у связи и вакансии.
2. Совпал `apply_url_canonical` у вакансии, виденной в окне → `MERGED`.
3. Та же `company_norm` и `token_sort_ratio(title_norm) ≥ threshold` в окне → `MERGED`.
4. Иначе → `NEW`.

- [ ] **Step 1: Падающие тесты чистой функции**

`tests/test_dedup.py`:
```python
import uuid

from aijobradar.dedup import Candidate, best_fuzzy_match
from aijobradar.text import normalize_title


def _c(title: str) -> Candidate:
    return Candidate(job_id=uuid.uuid4(), title_norm=normalize_title(title))


def test_identical_titles_match() -> None:
    target = _c("Full Stack Engineer")
    assert best_fuzzy_match(normalize_title("Full Stack Engineer"), [target], 92) == target


def test_spelling_variants_match() -> None:
    target = _c("Senior Fullstack Engineer")
    assert best_fuzzy_match(normalize_title("Senior Full-Stack Engineer"), [target], 92) == target


def test_different_seniority_does_not_match() -> None:
    # token_set_ratio would score this 100 and merge two different roles.
    assert best_fuzzy_match(normalize_title("Backend Engineer"),
                            [_c("Senior Backend Engineer")], 92) is None


def test_best_of_several_candidates_wins() -> None:
    near, exact = _c("Integration Engineer"), _c("Integrations Engineer")
    assert best_fuzzy_match(normalize_title("Integrations Engineer"), [near, exact], 92) == exact


def test_no_candidates() -> None:
    assert best_fuzzy_match("anything", [], 92) is None
```

Run: `uv run pytest tests/test_dedup.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.dedup'`

- [ ] **Step 2: Реализация `dedup.py`**

```python
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz


class DedupOutcome(StrEnum):
    SEEN = "seen"  # same source already reported this posting
    MERGED = "merged"  # another source (or a repost) of a job we already have
    NEW = "new"


@dataclass(frozen=True)
class Candidate:
    job_id: uuid.UUID
    title_norm: str


def best_fuzzy_match(
    title_norm: str, candidates: Sequence[Candidate], threshold: int
) -> Candidate | None:
    # token_sort_ratio, not token_set_ratio: the latter scores "backend engineer" vs
    # "senior backend engineer" as 100 and would merge different seniority levels.
    best: Candidate | None = None
    best_score = -1.0
    for candidate in candidates:
        score = fuzz.token_sort_ratio(title_norm, candidate.title_norm)
        if score >= threshold and score > best_score:
            best, best_score = candidate, score
    return best
```

Run: `uv run pytest tests/test_dedup.py`
Expected: PASS.

- [ ] **Step 3: Падающие тесты записи и дедупа в БД**

`tests/test_ingest.py`:
```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import DedupConfig
from aijobradar.db.models import Job, JobSource
from aijobradar.dedup import DedupOutcome
from aijobradar.ingest import ingest
from aijobradar.models import RawJob
from aijobradar.normalize import normalize

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)
CFG = DedupConfig()


def _raw(source: str, sid: str, title: str, company: str = "Initech",
         url: str | None = None) -> RawJob:
    return RawJob(source=source, source_job_id=sid, source_url=url or f"https://{source}/{sid}",
                  title=title, company=company, description_html="<p>x</p>")


def _ingest(session: Session, raw: RawJob, now: datetime = NOW) -> DedupOutcome:
    run = store.start_run(session, "fetch", now)
    return ingest(session, normalize(raw), run_id=run.id, now=now, cfg=CFG)


def _count(session: Session, model: type[Job] | type[JobSource]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_new_job_is_inserted_with_source_link(session: Session) -> None:
    assert _ingest(session, _raw("himalayas", "1", "Full Stack Engineer")) is DedupOutcome.NEW
    job = session.scalars(select(Job)).one()
    assert (job.company_norm, job.title_norm, job.state) == ("initech", "fullstack engineer", "new")
    link = session.scalars(select(JobSource)).one()
    assert (link.job_id, link.source, link.source_job_id) == (job.id, "himalayas", "1")


def test_same_source_id_again_is_seen_and_touched(session: Session) -> None:
    _ingest(session, _raw("jobicy", "7", "QA Engineer"))
    later = NOW + timedelta(days=1)
    assert _ingest(session, _raw("jobicy", "7", "QA Engineer"), later) is DedupOutcome.SEEN
    assert _count(session, Job) == 1
    assert session.scalars(select(Job)).one().last_seen_at == later
    assert session.scalars(select(JobSource)).one().last_seen_at == later


def test_other_source_same_company_similar_title_is_merged(session: Session) -> None:
    _ingest(session, _raw("himalayas", "1", "Integrations Engineer", "Northwind Docs"))
    outcome = _ingest(session, _raw("wwr", "a", "Integration Engineer", "Northwind Docs, Inc."))
    assert outcome is DedupOutcome.MERGED
    assert (_count(session, Job), _count(session, JobSource)) == (1, 2)


def test_per_country_variants_collapse_into_one_job(session: Session) -> None:
    _ingest(session, _raw("himalayas", "2001", "Full Stack Engineer"))
    assert _ingest(session, _raw("himalayas", "2002", "Full Stack Engineer")) is (
        DedupOutcome.MERGED
    )
    assert _count(session, Job) == 1


def test_same_canonical_url_is_merged_even_with_different_title(session: Session) -> None:
    _ingest(session, _raw("a", "1", "Engineer", url="https://jobs.example/42?utm_source=x"))
    outcome = _ingest(session, _raw("b", "9", "Software Engineer II", "Other Name",
                                    url="https://jobs.example/42/"))
    assert outcome is DedupOutcome.MERGED


def test_different_seniority_is_a_new_job(session: Session) -> None:
    _ingest(session, _raw("jobicy", "1", "Senior Backend Engineer"))
    assert _ingest(session, _raw("jobicy", "2", "Backend Engineer")) is DedupOutcome.NEW


def test_match_outside_window_is_new(session: Session) -> None:
    _ingest(session, _raw("jobicy", "1", "Full Stack Engineer"), NOW - timedelta(days=61))
    assert _ingest(session, _raw("wwr", "x", "Full Stack Engineer")) is DedupOutcome.NEW
```

Run: `uv run pytest tests/test_ingest.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.store'` (или skip, если Postgres не запущен — тогда `brew services start postgresql@17`).

- [ ] **Step 4: Реализация `store.py`**

```python
"""All database reads and writes. Nothing else in the package touches the ORM session."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from aijobradar.db.models import Job, JobSource, Run, SourceRun
from aijobradar.dedup import Candidate
from aijobradar.models import RawJob, SourceResult
from aijobradar.normalize import NormalizedJob


def start_run(session: Session, kind: str, now: datetime) -> Run:
    run = Run(kind=kind, started_at=now, counts={})
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: Run, status: str, counts: dict[str, int],
               now: datetime) -> None:
    run.status = status
    run.counts = counts
    run.finished_at = now
    session.flush()


def record_source_result(session: Session, run_id: uuid.UUID, result: SourceResult) -> None:
    session.add(SourceRun(
        run_id=run_id, source=result.source, status=result.status.value,
        item_count=len(result.items), invalid_items=result.invalid_items,
        out_of_scope=result.out_of_scope, http_status=result.http_status,
        error=result.error, duration_ms=result.duration_ms,
    ))
    session.flush()


def find_source(session: Session, source: str, source_job_id: str) -> JobSource | None:
    return session.scalars(
        select(JobSource).where(JobSource.source == source,
                                JobSource.source_job_id == source_job_id)
    ).one_or_none()


def find_job_id_by_url(session: Session, url: str, since: datetime) -> uuid.UUID | None:
    return session.scalars(
        select(Job.id).where(Job.apply_url_canonical == url, Job.last_seen_at >= since).limit(1)
    ).first()


def company_candidates(session: Session, company_norm: str, since: datetime) -> list[Candidate]:
    rows = session.execute(
        select(Job.id, Job.title_norm).where(Job.company_norm == company_norm,
                                             Job.last_seen_at >= since)
    ).all()
    return [Candidate(job_id=row.id, title_norm=row.title_norm) for row in rows]


def insert_job(session: Session, job: NormalizedJob, run_id: uuid.UUID, now: datetime) -> Job:
    raw = job.raw
    row = Job(
        content_hash=job.content_hash, company_raw=raw.company, company_norm=job.company_norm,
        title_raw=raw.title, title_norm=job.title_norm, location_text=raw.location_text,
        location_restrictions=raw.location_restrictions,
        timezone_restrictions=raw.timezone_restrictions, employment_type=raw.employment_type,
        seniority=raw.seniority, salary_min=raw.salary_min, salary_max=raw.salary_max,
        salary_currency=raw.salary_currency,
        salary_period=raw.salary_period.value if raw.salary_period else None,
        posted_at=raw.posted_at, description_text=job.description_text,
        apply_url_canonical=job.apply_url_canonical, first_seen_run_id=run_id,
        first_seen_at=now, last_seen_at=now, state="new",
    )
    session.add(row)
    session.flush()  # later records in the same run must see this job when deduplicating
    return row


def attach_source(session: Session, job_id: uuid.UUID, raw: RawJob, now: datetime) -> None:
    session.add(JobSource(job_id=job_id, source=raw.source, source_job_id=raw.source_job_id,
                          source_url=raw.source_url, first_seen_at=now, last_seen_at=now))
    session.execute(update(Job).where(Job.id == job_id).values(last_seen_at=now))
    session.flush()


def touch_source(session: Session, link: JobSource, now: datetime) -> None:
    link.last_seen_at = now
    session.execute(update(Job).where(Job.id == link.job_id).values(last_seen_at=now))
    session.flush()
```

- [ ] **Step 5: Реализация `ingest.py`**

```python
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import DedupConfig
from aijobradar.dedup import DedupOutcome, best_fuzzy_match
from aijobradar.normalize import NormalizedJob


def ingest(session: Session, job: NormalizedJob, *, run_id: uuid.UUID, now: datetime,
           cfg: DedupConfig) -> DedupOutcome:
    raw = job.raw
    link = store.find_source(session, raw.source, raw.source_job_id)
    if link is not None:
        store.touch_source(session, link, now)
        return DedupOutcome.SEEN

    since = now - timedelta(days=cfg.match_window_days)
    match_id = store.find_job_id_by_url(session, job.apply_url_canonical, since)
    if match_id is None:
        candidates = store.company_candidates(session, job.company_norm, since)
        match = best_fuzzy_match(job.title_norm, candidates, cfg.title_similarity)
        match_id = match.job_id if match else None
    if match_id is not None:
        store.attach_source(session, match_id, raw, now)
        return DedupOutcome.MERGED

    row = store.insert_job(session, job, run_id, now)
    store.attach_source(session, row.id, raw, now)
    return DedupOutcome.NEW
```

- [ ] **Step 6: Тесты проходят**

Run: `uv run pytest tests/test_dedup.py tests/test_ingest.py && uv run ruff check . && uv run mypy src`
Expected: PASS. Внимание к `test_match_outside_window_is_new`: у старой вакансии `last_seen_at = NOW − 61 дн.` — вне 60-дневного окна.

- [ ] **Step 7: Checkpoint** — не коммитить.

---

### Task 10: Пайплайн `fetch`, отчёт, CLI, smoke

**Files:**
- Create: `src/aijobradar/pipeline.py`, `tests/test_pipeline.py`
- Modify: `src/aijobradar/sources/__init__.py`, `src/aijobradar/cli.py`

**Interfaces:**
- Consumes: всё выше
- Produces:
  - `sources.build_adapters(cfg: AppConfig) -> list[Adapter]` (только `enabled`)
  - `pipeline.RunStatus(StrEnum)`: `OK="ok"`, `PARTIAL="partial"`, `FAILED="failed"`
  - `pipeline.FetchReport(run_id: uuid.UUID, status: RunStatus, sources: list[SourceResult], outcomes: dict[DedupOutcome, int])`
  - `pipeline.run_status(results: Sequence[SourceResult]) -> RunStatus`
  - `pipeline.run_fetch(session, adapters: Sequence[Adapter], client: httpx.Client, *, now: datetime, dedup_cfg: DedupConfig) -> FetchReport`
  - `pipeline.format_report(report: FetchReport) -> str`

Правило статуса прогона (спецификация §6.7, часть «источники»): все источники `failed` (или источников нет) → `failed`; есть `failed`/`degraded` → `partial`; иначе `ok`. CLI выходит с кодом 1 только при `failed`.

- [ ] **Step 1: Падающие тесты**

`tests/test_pipeline.py`:
```python
import json
from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar.config import AppConfig, DedupConfig
from aijobradar.db.models import Run, SourceRun
from aijobradar.dedup import DedupOutcome
from aijobradar.models import SourceResult, SourceStatus
from aijobradar.pipeline import RunStatus, format_report, run_fetch, run_status
from aijobradar.sources import build_adapters
from aijobradar.sources.himalayas import BASE_URL, HimalayasAdapter
from aijobradar.sources.jobicy import API_URL, JobicyAdapter
from aijobradar.sources.wwr import FEED_URL, WwrAdapter
from tests.conftest import fixture_path

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)


def _r(status: SourceStatus) -> SourceResult:
    return SourceResult(source="s", status=status)


def test_run_status_rules() -> None:
    assert run_status([]) is RunStatus.FAILED
    assert run_status([_r(SourceStatus.FAILED)] * 2) is RunStatus.FAILED
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.FAILED)]) is RunStatus.PARTIAL
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.DEGRADED)]) is RunStatus.PARTIAL
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.EMPTY)]) is RunStatus.OK


def test_build_adapters_respects_enabled() -> None:
    cfg = AppConfig.model_validate({"jobicy": {"enabled": False}})
    assert [a.name for a in build_adapters(cfg)] == ["himalayas", "wwr"]


def _mock_sources(jobicy_status: int = 200) -> None:
    pages = [json.loads(fixture_path("himalayas", n).read_text())
             for n in ("page1.json", "page2.json")]
    respx.get(BASE_URL).mock(side_effect=[httpx.Response(200, json=p) for p in pages])
    respx.get(API_URL).mock(return_value=httpx.Response(
        jobicy_status, json=json.loads(fixture_path("jobicy", "engineering.json").read_text())))
    respx.get(FEED_URL.format(slug="remote-full-stack-programming-jobs")).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "fullstack.rss").read_bytes()))


def _adapters() -> list[object]:
    return [HimalayasAdapter(now=lambda: NOW), JobicyAdapter(),
            WwrAdapter(feeds=("remote-full-stack-programming-jobs",))]


@respx.mock
def test_run_fetch_end_to_end_with_cross_source_dedup(session: Session) -> None:
    _mock_sources()
    with httpx.Client() as client:
        report = run_fetch(session, _adapters(), client, now=NOW,  # type: ignore[arg-type]
                           dedup_cfg=DedupConfig())
    assert report.status is RunStatus.OK
    # himalayas: Acme, Northwind, Initech x2 (collapsed) -> 3 new + 1 merged
    # jobicy: Umbrella, Hooli -> 2 new
    # wwr: Northwind + Acme already known from himalayas -> 2 merged
    assert report.outcomes == {DedupOutcome.NEW: 5, DedupOutcome.MERGED: 3}
    run = session.get(Run, report.run_id)
    assert run is not None and run.status == "ok" and run.finished_at == NOW
    assert run.counts == {"new": 5, "merged": 3, "seen": 0}
    statuses = {r.source: r.status for r in session.scalars(select(SourceRun))}
    assert statuses == {"himalayas": "ok", "jobicy": "ok", "wwr": "ok"}


@respx.mock
def test_second_run_sees_everything_again(session: Session) -> None:
    for _ in range(2):
        _mock_sources()
        with httpx.Client() as client:
            report = run_fetch(session, _adapters(), client, now=NOW,  # type: ignore[arg-type]
                               dedup_cfg=DedupConfig())
    assert report.outcomes == {DedupOutcome.SEEN: 8}


@respx.mock
def test_failed_source_is_reported_not_hidden(session: Session) -> None:
    _mock_sources(jobicy_status=503)
    with httpx.Client() as client:
        report = run_fetch(session, _adapters(), client, now=NOW,  # type: ignore[arg-type]
                           dedup_cfg=DedupConfig())
    assert report.status is RunStatus.PARTIAL
    text = format_report(report)
    assert "jobicy: ❌ failed" in text
    assert "engineering: HTTP 503" in text
    assert "himalayas: ok" in text
    assert "Прогон: partial" in text


def test_format_report_lines() -> None:
    import uuid

    from aijobradar.pipeline import FetchReport

    report = FetchReport(
        run_id=uuid.UUID(int=1), status=RunStatus.PARTIAL,
        sources=[
            SourceResult(source="himalayas", status=SourceStatus.OK, invalid_items=1,
                         out_of_scope=12, duration_ms=1500),
            SourceResult(source="wwr", status=SourceStatus.DEGRADED,
                         error="remote-programming-jobs: HTTP 403"),
        ],
        outcomes={DedupOutcome.NEW: 3, DedupOutcome.MERGED: 1},
    )
    assert format_report(report) == "\n".join([
        "Прогон: partial (00000000-0000-0000-0000-000000000001)",
        "himalayas: ok, записей 0 (невалидных 1, вне области 12), 1500 мс",
        "wwr: ⚠️ degraded, записей 0 (невалидных 0, вне области 0), 0 мс"
        " — remote-programming-jobs: HTTP 403",
        "Вакансии: новых 3, склеено с известными 1, уже виденных 0",
    ])
```

Run: `uv run pytest tests/test_pipeline.py`
Expected: FAIL — `ImportError: cannot import name 'build_adapters'`

- [ ] **Step 2: `sources/__init__.py`**

```python
from aijobradar.config import AppConfig
from aijobradar.sources.base import Adapter
from aijobradar.sources.himalayas import HimalayasAdapter
from aijobradar.sources.jobicy import JobicyAdapter
from aijobradar.sources.wwr import WwrAdapter


def build_adapters(cfg: AppConfig) -> list[Adapter]:
    adapters: list[Adapter] = []
    if cfg.himalayas.enabled:
        adapters.append(HimalayasAdapter(
            max_pages=cfg.himalayas.max_pages, lookback_days=cfg.himalayas.lookback_days,
            parent_categories=tuple(cfg.himalayas.parent_categories)))
    if cfg.jobicy.enabled:
        adapters.append(JobicyAdapter(count=cfg.jobicy.count,
                                      industries=tuple(cfg.jobicy.industries)))
    if cfg.wwr.enabled:
        adapters.append(WwrAdapter(feeds=tuple(cfg.wwr.feeds)))
    return adapters
```

- [ ] **Step 3: `pipeline.py`**

```python
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import httpx
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import DedupConfig
from aijobradar.dedup import DedupOutcome
from aijobradar.ingest import ingest
from aijobradar.models import SourceResult, SourceStatus
from aijobradar.normalize import normalize
from aijobradar.sources.base import Adapter, run_adapter

_STATUS_MARK = {SourceStatus.FAILED: "❌ ", SourceStatus.DEGRADED: "⚠️ "}


class RunStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class FetchReport:
    run_id: uuid.UUID
    status: RunStatus
    sources: list[SourceResult]
    outcomes: dict[DedupOutcome, int]


def run_status(results: Sequence[SourceResult]) -> RunStatus:
    if not results or all(r.status is SourceStatus.FAILED for r in results):
        return RunStatus.FAILED
    if any(r.status in (SourceStatus.FAILED, SourceStatus.DEGRADED) for r in results):
        return RunStatus.PARTIAL
    return RunStatus.OK


def run_fetch(session: Session, adapters: Sequence[Adapter], client: httpx.Client, *,
              now: datetime, dedup_cfg: DedupConfig) -> FetchReport:
    run = store.start_run(session, "fetch", now)
    results: list[SourceResult] = []
    outcomes: Counter[DedupOutcome] = Counter()
    for adapter in adapters:
        result = run_adapter(adapter, client)
        store.record_source_result(session, run.id, result)
        for raw in result.items:
            outcomes[ingest(session, normalize(raw), run_id=run.id, now=now, cfg=dedup_cfg)] += 1
        results.append(result)
    status = run_status(results)
    counts = {outcome.value: outcomes.get(outcome, 0) for outcome in
              (DedupOutcome.NEW, DedupOutcome.MERGED, DedupOutcome.SEEN)}
    store.finish_run(session, run, status.value, counts, now)
    return FetchReport(run_id=run.id, status=status, sources=results, outcomes=dict(outcomes))


def format_report(report: FetchReport) -> str:
    lines = [f"Прогон: {report.status.value} ({report.run_id})"]
    for r in report.sources:
        line = (f"{r.source}: {_STATUS_MARK.get(r.status, '')}{r.status.value}, "
                f"записей {len(r.items)} (невалидных {r.invalid_items}, "
                f"вне области {r.out_of_scope}), {r.duration_ms} мс")
        if r.error:
            line += f" — {r.error}"
        lines.append(line)
    o = report.outcomes
    lines.append(f"Вакансии: новых {o.get(DedupOutcome.NEW, 0)}, "
                 f"склеено с известными {o.get(DedupOutcome.MERGED, 0)}, "
                 f"уже виденных {o.get(DedupOutcome.SEEN, 0)}")
    return "\n".join(lines)
```

- [ ] **Step 4: Команда `fetch` в CLI**

В `src/aijobradar/cli.py` заменить заглушку `fetch`:
```python
from datetime import UTC, datetime
from pathlib import Path


@app.command()
def fetch(
    config: Path = typer.Option(Path("config/sources.yaml"), help="Sources config (YAML)."),
) -> None:
    """Fetch jobs from all enabled sources, deduplicate and store them."""
    from sqlalchemy.orm import Session

    from aijobradar.config import Settings, load_config
    from aijobradar.db.session import make_engine
    from aijobradar.pipeline import RunStatus, format_report, run_fetch
    from aijobradar.sources import build_adapters
    from aijobradar.sources.common import make_client

    settings = Settings()
    cfg = load_config(config)
    engine = make_engine(settings.sqlalchemy_url)
    with (
        make_client(settings.user_agent, settings.http_timeout_s) as client,
        Session(engine) as session,
        session.begin(),  # a failed run is still committed: its statuses are the evidence
    ):
        report = run_fetch(session, build_adapters(cfg), client, now=datetime.now(UTC),
                           dedup_cfg=cfg.dedup)
    typer.echo(format_report(report))
    if report.status is RunStatus.FAILED:
        raise typer.Exit(1)
```
(импорты `datetime`/`Path` — в начало модуля.)

- [ ] **Step 5: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: все тесты PASS.

- [ ] **Step 6: Ручной smoke против живых источников и локальной БД** (один раз; это единственный шаг с сетью)

```bash
createdb aijobradar_dev
```

```bash
DATABASE_URL=postgresql://localhost/aijobradar_dev uv run aijobradar db upgrade
```

```bash
DATABASE_URL=postgresql://localhost/aijobradar_dev uv run aijobradar fetch
```

Expected: строка статуса по каждому из трёх источников. Сверить с реальностью:
- у Himalayas `вне области` > 0 (не-Developer категории), записей > 0; если записей 0 при `ok` — проверить, что обход без `sort` возвращает свежие вакансии (порядок выдачи не подтверждён на этапе 0);
- повторный запуск той же командой → `новых 0`, почти всё `уже виденных`.
Записать фактический вывод в отчёт задачи. Боевой Neon не трогать — это этап 4.

- [ ] **Step 7: Checkpoint** — показать владельцу вывод smoke и `git status`. Не коммитить.

---

## Self-review (выполнено при написании)

- **Покрытие спецификации для этапа 1:** адаптеры с контрактом и статусами (§4) — Tasks 3–6; нормализация (§6.1) — Tasks 2, 7; дедуп в 3 слоя + окно (§6.2) — Task 9; схема `runs/source_runs/jobs/job_sources` (§5, подмножество этапа 1) — Task 8; статус прогона по источникам (§6.7, часть «источники») — Task 10; CI с Postgres (§11) — Task 1; синтетические фикстуры (§12, `docs/sources.md`) — Tasks 4–6. Правила, LLM, Telegram, eval, UI — следующие этапы.
- **Согласованность имён:** `run_adapter`, `fetch_feeds`, `FeedRequest`, `Fetched`, `DedupOutcome`, `Candidate(job_id, title_norm)`, `ingest(..., run_id, now, cfg)`, `RunStatus` — одинаковы во всех задачах.
- **Проверенные предпосылки:** `token_sort_ratio`/`token_set_ratio` на примерах, unix-время фикстур (1790640000 = 2026-09-29 00:00 UTC, 1790596800 = 2026-09-28 12:00 UTC, 1789862400 = 2026-09-20), разбор стран по флагам, `html_to_text` — прогнаны отдельно до написания плана.
