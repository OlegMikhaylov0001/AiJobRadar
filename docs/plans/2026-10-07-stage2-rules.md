# Этап 2 — детерминированные правила отсева до LLM

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** после сбора и дедупликации каждая новая вакансия проходит семь дешёвых правил (спецификация §6.3); явно неподходящие получают статус `rejected` с записанной причиной, остальные — `pending_score` (вход этапа 3). Команда `aijobradar run` = сбор + правила + отчёт с разбивкой отсева по правилам.

**Architecture:** правила — чистые функции `rule(facts, ctx) -> str | None` (строка = улика). `RuleContext` один раз компилирует все регулярные выражения из `config/rules.yaml` и личного `private/profile.yaml`. Движок `apply_rules` берёт вакансии в состоянии `new`, пишет `rule_decisions`, меняет состояние, кладёт выборку отсеянных в `review_queue`. Всё в той же транзакции прогона, каждая вакансия — в своём savepoint.

**Tech Stack:** как в этапе 1 (Python 3.12, uv, SQLAlchemy 2, Alembic, pydantic v2, pytest).

**Spec:** `docs/specs/2026-09-29-aijobradar-design.md` (§2, §5, §6.3, §6.7), `docs/sources.md`, `docs/plans/stage1-followups.md`.

## Global Constraints

- Всё из этапа 1 в силе: uv, ruff (100), `mypy --strict` на `src/`, тесты без сети, `docs/` не трогать, никаких коммитов исполнителями.
- **Никаких личных значений в отслеживаемых файлах.** Страна кандидата, минимальная ставка, исключённые страны работодателя — только в `private/profile.yaml` (в `.gitignore`). В репозитории — `config/profile.example.yaml` с вымышленными значениями. В тестах — вымышленный профиль (Португалия, ставка 20).
- **Улики правил в отчёт и логи не печатаются** (там только счётчики по `rule_id`): улики содержат тексты вакансий и порог ставки из профиля; они хранятся только в БД (`rule_decisions.details`).
- Правило отсекает **только явное**. Неизвестное место, отсутствие ставки, отсутствие даты, неизвестная валюта — не отсев.
- Строки отчёта — на русском; код, идентификаторы, комментарии — на английском.

## Уточнения к спецификации (вносит этот план)

1. **Семантика склейки** (пункт из `stage1-followups.md`): все поля вакансии — «побеждает первый источник», кроме гео (расширяется: `[]`/`None` = без ограничений побеждает, иначе объединение) и `last_seen_at` (максимум). **Новое:** если склейка расширила гео у вакансии в состоянии `rejected`, она возвращается в `new` и проходит правила заново — иначе вакансия, отсеянная по US-варианту, осталась бы отсеянной после прихода worldwide-варианта.
2. **Профиль для правил** (`private/profile.yaml`): `eligible_places` — коды мест (из `config/rules.yaml → places`), откуда кандидата могут нанять, включая неоднозначные регионы, которые кандидат готов считать «своими» (например, `EUROPE`, `EMEA`, `WORLDWIDE`); `excluded_employer_countries` — коды стран работодателя под отсев; `min_rate_usd_per_hour`.
3. **Справочник мест** (`places`) — публичный, общий: ~70 стран и регионов с синонимами. Короткие коды, совпадающие с обычными словами (`US`, `UK`, `EU`, `CIS`…), в тексте ищутся **с учётом регистра** — иначе «contact us only» читалось бы как «US only» (проверено прототипом). Georgia намеренно отсутствует: страна и штат США.
4. **Гео по структуре:** отсев, только если **все** ограничения локации — известные места и **ни одно** не входит в `eligible_places`. Любое неизвестное значение (город, «Remote») = неоднозначно = не отсев.
5. `rule_decisions.rule_ids` — `JSONB` (список строк), а не `text[]` из спецификации §5: проще для сверки миграций и для UI; функционально то же.
6. CLI: команда `fetch` переименована в `run` (как в спецификации §11) и получает `--rules`, `--profile`.
7. Ошибка внутри правила (баг) не роняет прогон: вакансия остаётся `new` (повторится в следующем прогоне), счётчик «ошибок правил» в отчёте, прогон `partial`.
8. **Повтор той же записи источника** (`(source, source_job_id)` уже известен, исход `SEEN`) тоже расширяет гео вакансии по тем же правилам, что и склейка, и так же возвращает `rejected` → `new`; обновляет `source_url`; `JobSource.last_seen_at` не уходит назад. Сужение гео не применяется (политика «только расширение»). Закрывает P2 ревью PR #1.
9. **WWR: регион без списка стран.** Замер 49 реальных записей: «Anywhere in the World» без стран — 31 (без ограничений), «Anywhere in the World» со странами — 14 (на деле «только эти страны», чаще США), «North America Only» без стран — 4. Правило: есть страны → ограничение = страны (как сейчас); иначе регион «X Only» → `[X]`; «Anywhere in the World» или пусто → `[]`. Раньше «North America Only» без стран превращался в `[]` = «без ограничений». Закрывает P2 ревью PR #1.

## Файловая структура

```
config/rules.yaml                    # rule parameters, places gazetteer (public, generic)
config/profile.example.yaml          # fictional profile; real one: private/profile.yaml
src/aijobradar/profile.py            # Profile, ProfileMissing, load_profile
src/aijobradar/config.py             # + RulesConfig, load_rules_config
src/aijobradar/rules/__init__.py
src/aijobradar/rules/places.py       # normalize_place, Gazetteer
src/aijobradar/rules/facts.py        # JobFacts (rule input built from a Job row)
src/aijobradar/rules/context.py      # RuleContext, build_rule_context (compiles every regex once)
src/aijobradar/rules/basic.py        # not_remote, non_engineering_role, stale
src/aijobradar/rules/geo.py          # pattern templates, geo_country_only, geo_residency
src/aijobradar/rules/employer.py     # employer_country
src/aijobradar/rules/money.py        # rate_floor
src/aijobradar/rules/engine.py       # RULES, evaluate, RulesReport, apply_rules
src/aijobradar/db/models.py          # + RuleDecision, ReviewItem
src/aijobradar/db/alembic/versions/0002_rules.py
src/aijobradar/store.py              # + reopen on geo widening (merge and same-source), rule/review writes
src/aijobradar/ingest.py             # SEEN path passes the record to touch_source
src/aijobradar/sources/wwr.py        # region "X Only" without countries -> [X]
src/aijobradar/pipeline.py           # + rules step, report lines
src/aijobradar/cli.py                # fetch -> run
tests/rules_support.py               # make_ctx(), make_facts() helpers for rule tests
tests/test_rules_config.py, test_places.py, test_rules_basic.py, test_rules_geo.py,
tests/test_rules_employer_money.py, test_rules_engine.py
tests/test_ingest.py, test_pipeline.py, test_cli.py   # extended
```

---

### Task 0: WWR — регион без списка стран становится ограничением

**Files:**
- Modify: `src/aijobradar/sources/wwr.py`, `tests/test_wwr.py`

**Interfaces:**
- Produces: `wwr.region_restrictions(region: str, countries: list[str]) -> list[str]` (уточнение 9)

- [ ] **Step 1: Падающие тесты** — дописать в `tests/test_wwr.py`:

```python
@pytest.mark.parametrize(
    ("region", "countries", "expected"),
    [
        ("Anywhere in the World", [], []),
        ("", [], []),
        ("North America Only", [], ["North America"]),
        ("Europe Only", [], ["Europe"]),
        ("Anywhere in the World", ["United States of America"], ["United States of America"]),
        ("Europe Only", ["Slovakia", "Ukraine"], ["Slovakia", "Ukraine"]),  # countries are precise
    ],
)
def test_region_restrictions(region: str, countries: list[str], expected: list[str]) -> None:
    assert region_restrictions(region, countries) == expected


def test_region_only_item_is_restricted() -> None:
    item = Element("item")
    SubElement(item, "title").text = "Acme: Backend Engineer"
    SubElement(item, "link").text = "https://weworkremotely.com/remote-jobs/acme-backend"
    SubElement(item, "region").text = "North America Only"
    SubElement(item, "country").text = ""
    job = WwrAdapter().parse_record(item)
    assert job is not None
    assert job.location_restrictions == ["North America"]
    assert job.location_text == "North America Only"
```
(импорт: `from aijobradar.sources.wwr import FEED_URL, WwrAdapter, region_restrictions, split_countries`)

Run: `uv run pytest tests/test_wwr.py` → FAIL (`ImportError: region_restrictions`).

- [ ] **Step 2: `src/aijobradar/sources/wwr.py`**

```python
_WORLDWIDE_REGION = "anywhere in the world"
_ONLY_SUFFIX = re.compile(r"\s+only$", re.IGNORECASE)


def region_restrictions(region: str, countries: list[str]) -> list[str]:
    """Countries are the precise restriction when listed (even under "Anywhere in the World").

    Without them a region like "North America Only" is the restriction; leaving it out would
    read as "no restriction".
    """
    if countries:
        return countries
    if not region or region.casefold() == _WORLDWIDE_REGION:
        return []
    return [_ONLY_SUFFIX.sub("", region).strip()]
```
В `parse_record`: `location_restrictions=region_restrictions(region, countries),` вместо `countries`.

- [ ] **Step 3: Тесты зелёные** — `uv run pytest && uv run ruff check . && uv run mypy src`; ожидания `test_parse_item_fields` и `test_pipeline` не меняются (в фикстурах у всех записей с регионом «X Only» есть страны).

---

### Task 1: Конфигурация правил и профиль

**Files:**
- Create: `config/rules.yaml`, `config/profile.example.yaml`, `src/aijobradar/profile.py`, `tests/test_rules_config.py`
- Modify: `src/aijobradar/config.py` (добавить `RulesConfig`, `load_rules_config`)

**Interfaces:**
- Produces:
  - `config.RulesConfig` (pydantic, `extra="forbid"`): `version: str`, `stale_days: int`, `rate_margin: float`, `review_sample_size: int`, `fx_to_usd: dict[str, float]`, `hours_per_period: dict[str, float]`, `places: dict[str, list[str]]`, `case_sensitive_aliases: list[str]`, `not_remote_title_patterns: list[str]`, `not_remote_location_patterns: list[str]`, `not_remote_description_patterns: list[str]`, `non_engineering_title_terms: list[str]`, `engineering_title_terms: list[str]`, `employer_country_markers: dict[str, list[str]]`, `employer_country_currencies: dict[str, list[str]]`
  - `config.load_rules_config(path: Path) -> RulesConfig`
  - `profile.Profile` (`extra="forbid"`): `eligible_places: list[str]` (min 1), `excluded_employer_countries: list[str] = []`, `min_rate_usd_per_hour: float | None = None`
  - `profile.ProfileMissing(Exception)`, `profile.load_profile(path: Path) -> Profile`

- [ ] **Step 1: `config/rules.yaml`** — создать ровно так:

```yaml
# Deterministic pre-LLM rules (spec §6.3). Generic and public: personal values (eligible
# places, minimum rate, excluded employer countries) live in private/profile.yaml.
version: "2026-10-07.1"     # bump on any change; stored with every rule decision
stale_days: 30
rate_margin: 0.10            # reject only when the stated ceiling is >10% below the floor
review_sample_size: 5        # rejected jobs per run sampled into review_queue (false-reject audit)

# Approximate rates, refreshed by hand; rate_margin absorbs drift.
fx_to_usd:
  USD: 1.0
  EUR: 1.08
  GBP: 1.27
  CHF: 1.12
  CAD: 0.73
  AUD: 0.66
  NZD: 0.60
  PLN: 0.25
  CZK: 0.044
  SEK: 0.095
  NOK: 0.093
  DKK: 0.145
  INR: 0.012
  BRL: 0.18
  MXN: 0.055
  ILS: 0.27
  AED: 0.27
  SGD: 0.74
  JPY: 0.0067
  UAH: 0.024
  RUB: 0.011
hours_per_period: {hour: 1, day: 8, week: 40, month: 173.33, year: 2080}

# Canonical place code -> aliases. Short codes that are also ordinary words are listed in
# case_sensitive_aliases so "contact us only" is not read as "US only". Georgia is left out on
# purpose (country vs US state).
places:
  US: ["US", "U.S.", "USA", "U.S.A.", "united states", "united states of america"]
  CA: ["canada"]
  GB: ["UK", "U.K.", "united kingdom", "great britain", "britain", "england"]
  IE: ["ireland"]
  DE: ["germany", "deutschland"]
  FR: ["france"]
  ES: ["spain"]
  PT: ["portugal"]
  IT: ["italy"]
  NL: ["netherlands", "the netherlands"]
  BE: ["belgium"]
  CH: ["switzerland"]
  AT: ["austria"]
  PL: ["poland"]
  CZ: ["czechia", "czech republic"]
  RO: ["romania"]
  BG: ["bulgaria"]
  HU: ["hungary"]
  GR: ["greece"]
  SE: ["sweden"]
  "NO": ["norway"]
  DK: ["denmark"]
  FI: ["finland"]
  EE: ["estonia"]
  LV: ["latvia"]
  LT: ["lithuania"]
  UA: ["ukraine"]
  RS: ["serbia"]
  HR: ["croatia"]
  SK: ["slovakia"]
  SI: ["slovenia"]
  BA: ["bosnia and herzegovina"]
  ME: ["montenegro"]
  MD: ["moldova"]
  CY: ["cyprus"]
  TR: ["turkey", "türkiye"]
  IL: ["israel"]
  AE: ["UAE", "united arab emirates"]
  IN: ["india"]
  PK: ["pakistan"]
  PH: ["philippines"]
  VN: ["vietnam"]
  ID: ["indonesia"]
  MY: ["malaysia"]
  SG: ["singapore"]
  JP: ["japan"]
  KR: ["south korea"]
  CN: ["china"]
  AU: ["australia"]
  NZ: ["new zealand"]
  BR: ["brazil"]
  MX: ["mexico"]
  AR: ["argentina"]
  CO: ["colombia"]
  CL: ["chile"]
  PE: ["peru"]
  ZA: ["south africa"]
  NG: ["nigeria"]
  KE: ["kenya"]
  EG: ["egypt"]
  AM: ["armenia"]
  KZ: ["kazakhstan"]
  RU: ["russia", "russian federation"]
  BY: ["belarus"]
  UZ: ["uzbekistan"]
  KG: ["kyrgyzstan"]
  AZ: ["azerbaijan"]
  EU: ["EU", "european union"]
  EUROPE: ["europe"]
  EMEA: ["emea"]
  LATAM: ["latam", "latin america", "south america"]
  NORTH_AMERICA: ["north america", "us & canada", "usa & canada", "us/canada", "usa/canada"]
  AMERICAS: ["americas", "the americas"]
  APAC: ["apac", "asia pacific", "asia-pacific"]
  ASIA: ["asia"]
  AFRICA: ["africa"]
  MIDDLE_EAST: ["middle east", "MENA"]
  OCEANIA: ["oceania"]
  CIS: ["CIS"]
  NORDICS: ["nordics", "scandinavia"]
  DACH: ["DACH"]
  BALTICS: ["baltics", "baltic states"]
  CEE: ["CEE", "central and eastern europe", "eastern europe"]
  WORLDWIDE: ["worldwide", "anywhere", "anywhere in the world", "global", "globally"]
case_sensitive_aliases: ["US", "U.S.", "USA", "U.S.A.", "UK", "U.K.", "EU", "UAE", "MENA", "CIS", "DACH", "CEE"]

# R-NOT-REMOTE. Title: only bracketed/suffix forms ("Hybrid Cloud Engineer" is not a hybrid job).
not_remote_title_patterns:
  - '[(\[]\s*(?:hybrid|on-?site|in[- ]office)\s*[)\]]'
  - '[-–|,]\s*(?:hybrid|on-?site|in[- ]office)\s*$'
not_remote_location_patterns:
  - '\bhybrid\b'
  - '\bon-?site\b'
  - '\bin[- ]office\b'
# Description: only unambiguous phrases ("we have an office in Berlin" must pass).
not_remote_description_patterns:
  - '\b(?:this|the)\s+(?:role|position|job)\s+is\s+(?:a\s+)?(?:hybrid|on-?site)\b'
  - '\bthis\s+is\s+an?\s+(?:hybrid|on-?site)\s+(?:role|position)\b'
  - '\bhybrid\s+(?:role|position|working model|work model|schedule)\b'
  - '\b(?:[1-5]|one|two|three|four|five)\s+days?\s+(?:a|per)\s+week\s+(?:in|at|from)\s+(?:the|our)\s+office\b'

# R-NON-ENG-ROLE: matched as whole words on the normalized title; an engineering term wins.
non_engineering_title_terms: [sales, account executive, account manager, business development,
  marketing, marketer, seo, content writer, copywriter, recruiter, recruiting, talent acquisition,
  sourcer, hr, people partner, designer, customer success, customer support, support specialist,
  community manager, social media, accountant, bookkeeper, finance manager, legal, lawyer,
  paralegal, office manager, executive assistant, virtual assistant, project coordinator, sdr, bdr]
engineering_title_terms: [engineer, engineering, developer, programmer, architect, devops, sre,
  qa, sdet, tester, technical, tech lead, cto, software]

# R-EMPLOYER-COUNTRY: marker sets per employer country (matched case-insensitively on company,
# title and description). Which countries are excluded is decided by the private profile.
employer_country_markers:
  RU:
    - 'тк\s*рф'
    - 'трудов\w*\s+кодекс\w*\s+рф'
    - 'аккредитованн\w*\s+(?:it|ит)'
    - '\bооо\s*[«"]'
    - '\bв\s+рублях\b'
    - '₽'
    - '\bруб\.?(?=\s|$)'
    - 'налогов\w*\s+резидент\w*\s+рф'
    - 'граждан\w*\s+рф'
employer_country_currencies:
  RU: [RUB]
```

- [ ] **Step 2: `config/profile.example.yaml`**

```yaml
# Copy to private/profile.yaml (gitignored) and put your own values there.
# Everything below is fictional. Codes come from config/rules.yaml -> places.
eligible_places: [PT, EU, EUROPE, EMEA, WORLDWIDE]   # where you can be hired from, incl. regions you accept
excluded_employer_countries: []                      # e.g. [XX] for a country in employer_country_markers
min_rate_usd_per_hour: 25                            # null = no rate filter
```

- [ ] **Step 3: Падающие тесты**

`tests/test_rules_config.py`:
```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from aijobradar.config import load_rules_config
from aijobradar.profile import Profile, ProfileMissing, load_profile

ROOT = Path(__file__).parent.parent


def test_repo_rules_config_loads() -> None:
    cfg = load_rules_config(ROOT / "config" / "rules.yaml")
    assert cfg.version
    assert cfg.stale_days == 30 and cfg.review_sample_size == 5
    assert cfg.fx_to_usd["USD"] == 1.0
    assert cfg.hours_per_period["year"] == 2080
    assert "US" in cfg.places and "NO" in cfg.places  # YAML must not turn NO into False
    assert "georgia" not in {a.casefold() for aliases in cfg.places.values() for a in aliases}
    assert cfg.employer_country_currencies["RU"] == ["RUB"]


def test_rules_config_rejects_unknown_keys(tmp_path: Path) -> None:
    text = (ROOT / "config" / "rules.yaml").read_text() + "\nstale_dayz: 3\n"
    path = tmp_path / "rules.yaml"
    path.write_text(text)
    with pytest.raises(ValidationError):
        load_rules_config(path)


def test_example_profile_is_valid() -> None:
    profile = load_profile(ROOT / "config" / "profile.example.yaml")
    assert profile.eligible_places and profile.min_rate_usd_per_hour == 25


def test_missing_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(ProfileMissing):
        load_profile(tmp_path / "profile.yaml")


def test_profile_rejects_unknown_keys_and_empty_places() -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate({"eligible_places": ["PT"], "country": "PT"})
    with pytest.raises(ValidationError):
        Profile.model_validate({"eligible_places": []})
```

Run: `uv run pytest tests/test_rules_config.py`
Expected: FAIL — `ImportError: cannot import name 'load_rules_config'`

- [ ] **Step 4: `src/aijobradar/profile.py`**

```python
"""The candidate profile used by rules. Personal: lives in private/profile.yaml, never in git."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ProfileMissing(Exception):
    pass


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Place codes from config/rules.yaml -> places, incl. ambiguous regions the candidate accepts.
    eligible_places: list[str] = Field(min_length=1)
    excluded_employer_countries: list[str] = Field(default_factory=list)
    min_rate_usd_per_hour: float | None = None


def load_profile(path: Path) -> Profile:
    if not path.is_file():
        raise ProfileMissing(str(path))
    return Profile.model_validate(yaml.safe_load(path.read_text()) or {})
```

- [ ] **Step 5: `RulesConfig` в `src/aijobradar/config.py`** (дописать в конец модуля)

```python
class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    stale_days: int
    rate_margin: float
    review_sample_size: int
    fx_to_usd: dict[str, float]
    hours_per_period: dict[str, float]
    places: dict[str, list[str]]
    case_sensitive_aliases: list[str]
    not_remote_title_patterns: list[str]
    not_remote_location_patterns: list[str]
    not_remote_description_patterns: list[str]
    non_engineering_title_terms: list[str]
    engineering_title_terms: list[str]
    employer_country_markers: dict[str, list[str]]
    employer_country_currencies: dict[str, list[str]]


def load_rules_config(path: Path) -> RulesConfig:
    return RulesConfig.model_validate(yaml.safe_load(path.read_text()))
```

- [ ] **Step 6: Тесты зелёные, полная проверка**

Run: `uv run pytest tests/test_rules_config.py && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS.

---

### Task 2: Справочник мест (Gazetteer)

**Files:**
- Create: `src/aijobradar/rules/__init__.py` (пустой), `src/aijobradar/rules/places.py`, `tests/test_places.py`

**Interfaces:**
- Consumes: `RulesConfig.places`, `RulesConfig.case_sensitive_aliases`
- Produces: `normalize_place(token: str) -> str`; `Gazetteer(places: Mapping[str, Sequence[str]], case_sensitive: Iterable[str])` с атрибутами `codes: frozenset[str]`, `place_group: str` (regex-группа `(?P<place>…)`) и методом `canonical(token: str) -> str | None`

- [ ] **Step 1: Падающие тесты**

`tests/test_places.py`:
```python
import re
from pathlib import Path

import pytest

from aijobradar.config import load_rules_config
from aijobradar.rules.places import Gazetteer, normalize_place

CFG = load_rules_config(Path(__file__).parent.parent / "config" / "rules.yaml")
GAZ = Gazetteer(CFG.places, CFG.case_sensitive_aliases)


@pytest.mark.parametrize(
    ("token", "code"),
    [
        ("🇺🇸 United States of America", "US"),
        ("Anywhere in the World", "WORLDWIDE"),
        ("US only", "US"),
        ("U.S.", "US"),
        ("Bosnia and Herzegovina", "BA"),
        ("GERMANY", "DE"),
        ("US & Canada", "NORTH_AMERICA"),
        ("Narnia", None),
        ("Remote", None),
    ],
)
def test_canonical(token: str, code: str | None) -> None:
    assert GAZ.canonical(token) == code


def test_normalize_place_strips_flags_only_and_punctuation() -> None:
    assert normalize_place("  🇨🇦 Canada (only)  ") == "canada"


def test_alias_mapped_to_two_codes_is_rejected() -> None:
    with pytest.raises(ValueError, match="both"):
        Gazetteer({"US": ["america"], "AMERICAS": ["America"]}, [])


def _only(text: str) -> str | None:
    pattern = re.compile(rf"(?<![\w.]){GAZ.place_group}(?![\w])\s*[-(]?\s*only\b", re.I)
    match = pattern.search(text)
    return GAZ.canonical(match.group("place")) if match else None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("This role is US only.", "US"),
        ("Remote (USA only)", "US"),
        ("Please contact us only via email", None),  # short codes are case-sensitive
        ("Join us only if you love Rust", None),
        ("us & canada only", "NORTH_AMERICA"),
        ("EMEA only", "EMEA"),
        ("Bosnia and Herzegovina only", "BA"),
    ],
)
def test_place_group_in_text(text: str, code: str | None) -> None:
    assert _only(text) == code
```

Run: `uv run pytest tests/test_places.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.rules'`

- [ ] **Step 2: Реализация `src/aijobradar/rules/places.py`**

```python
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence

_FLAG = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")  # regional-indicator pair = one flag
_ONLY = re.compile(r"\bonly\b")
_PUNCT = re.compile(r"[^\w\s&/.-]")


def normalize_place(token: str) -> str:
    s = unicodedata.normalize("NFKC", _FLAG.sub("", token)).casefold()
    s = _ONLY.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return " ".join(s.split()).strip(" .-")


def _alternation(aliases: Iterable[str]) -> str:
    # Longest first, so "united states of america" wins over "united states".
    return "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))


class Gazetteer:
    """Place aliases -> canonical codes, plus one regex group that finds any alias in text."""

    def __init__(
        self, places: Mapping[str, Sequence[str]], case_sensitive: Iterable[str]
    ) -> None:
        sensitive = set(case_sensitive)
        self._lookup: dict[str, str] = {}
        cs: list[str] = []
        ci: list[str] = []
        for code, aliases in places.items():
            for alias in aliases:
                key = normalize_place(alias)
                existing = self._lookup.get(key)
                if existing is not None and existing != code:
                    raise ValueError(f"alias {alias!r} maps to both {existing} and {code}")
                self._lookup[key] = code
                (cs if alias in sensitive else ci).append(alias)
        self.codes = frozenset(places)
        # Short codes such as "US" are also ordinary words ("contact us only"): match them
        # case-sensitively inside an otherwise case-insensitive pattern.
        parts = []
        if cs:
            parts.append(f"(?-i:{_alternation(cs)})")
        if ci:
            parts.append(_alternation(ci))
        self.place_group = f"(?P<place>{'|'.join(parts)})"

    def canonical(self, token: str) -> str | None:
        return self._lookup.get(normalize_place(token))
```

- [ ] **Step 3: Тесты зелёные**

Run: `uv run pytest tests/test_places.py && uv run ruff check . && uv run mypy src`
Expected: PASS.

---

### Task 3: Входные данные правил, контекст и простые правила

**Files:**
- Create: `src/aijobradar/rules/facts.py`, `src/aijobradar/rules/context.py`, `src/aijobradar/rules/basic.py`, `src/aijobradar/rules/geo.py` (только шаблоны паттернов и `compile_geo_patterns`; правила — в Task 4), `tests/rules_support.py`, `tests/test_rules_basic.py`

**Interfaces:**
- Consumes: `RulesConfig`, `Profile`, `Gazetteer`, `db.models.Job`, `text.normalize_title`
- Produces:
  - `facts.JobFacts` (frozen dataclass): `job_id: uuid.UUID`, `company: str`, `title: str`, `title_norm: str`, `location_text: str`, `location_restrictions: tuple[str, ...]`, `description: str`, `salary_min: float | None`, `salary_max: float | None`, `salary_currency: str | None`, `salary_period: str | None`, `posted_at: datetime | None`; `JobFacts.from_job(job: Job) -> JobFacts`
  - `geo.GeoPatterns` (frozen dataclass): `country_only: tuple[re.Pattern[str], ...]`, `remote_place: re.Pattern[str]`, `residency: tuple[re.Pattern[str], ...]`, `clearance: re.Pattern[str]`; `geo.compile_geo_patterns(place_group: str) -> GeoPatterns`
  - `context.RuleContext` (frozen dataclass): `cfg`, `profile`, `gazetteer`, `now: datetime`, `eligible: frozenset[str]`, `not_remote_title`, `not_remote_location`, `not_remote_description`, `non_eng_terms`, `eng_terms` (все `tuple[re.Pattern[str], ...]`), `employer_markers: dict[str, tuple[re.Pattern[str], ...]]` (только исключённые страны), `employer_currencies: dict[str, frozenset[str]]` (только исключённые страны), `geo: GeoPatterns`
  - `context.build_rule_context(cfg: RulesConfig, profile: Profile, now: datetime) -> RuleContext` — `ValueError`, если в профиле неизвестные коды мест или исключённая страна без маркеров и валют
  - `basic.not_remote(f: JobFacts, ctx: RuleContext) -> str | None`, `basic.non_engineering_role(...)`, `basic.stale(...)` — строка-улика или `None`
  - `tests/rules_support.py`: `CFG`, `TEST_PROFILE`, `NOW`, `make_ctx(**profile_overrides) -> RuleContext`, `make_facts(**overrides) -> JobFacts`

- [ ] **Step 1: Помощники тестов `tests/rules_support.py`**

```python
"""Shared helpers for rule tests. The profile is fictional (spec: no personal data in git)."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aijobradar.config import load_rules_config
from aijobradar.profile import Profile
from aijobradar.rules.context import RuleContext, build_rule_context
from aijobradar.rules.facts import JobFacts
from aijobradar.text import normalize_title

CFG = load_rules_config(Path(__file__).parent.parent / "config" / "rules.yaml")
NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)
TEST_PROFILE: dict[str, Any] = {
    "eligible_places": ["PT", "EU", "EUROPE", "EMEA", "WORLDWIDE"],
    "excluded_employer_countries": ["RU"],
    "min_rate_usd_per_hour": 20,
}


def make_ctx(**profile_overrides: Any) -> RuleContext:
    profile = Profile.model_validate({**TEST_PROFILE, **profile_overrides})
    return build_rule_context(CFG, profile, NOW)


def make_facts(**overrides: Any) -> JobFacts:
    data: dict[str, Any] = {
        "job_id": uuid.uuid4(),
        "company": "Acme",
        "title": "Backend Engineer",
        "location_text": "",
        "location_restrictions": (),
        "description": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "posted_at": None,
    }
    data.update(overrides)
    data["location_restrictions"] = tuple(data["location_restrictions"])
    data.setdefault("title_norm", normalize_title(data["title"]))
    if "title" in overrides and "title_norm" not in overrides:
        data["title_norm"] = normalize_title(data["title"])
    return JobFacts(**data)
```

- [ ] **Step 2: Падающие тесты простых правил**

`tests/test_rules_basic.py`:
```python
from datetime import timedelta

import pytest

from aijobradar.rules.basic import non_engineering_role, not_remote, stale
from tests.rules_support import NOW, make_ctx, make_facts

CTX = make_ctx()


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"title": "Backend Engineer (Hybrid)"}, True),
        ({"title": "Platform Engineer - Onsite"}, True),
        ({"title": "Hybrid Cloud Engineer"}, False),
        ({"location_text": "Hybrid - Berlin"}, True),
        ({"location_text": "Remote, Europe"}, False),
        ({"description": "This is a hybrid role based in Lisbon."}, True),
        ({"description": "You will spend 3 days a week in the office."}, True),
        ({"description": "We have an office in Berlin you can visit."}, False),
        ({"description": "Remote-first, optional office."}, False),
    ],
)
def test_not_remote(fields: dict[str, str], hit: bool) -> None:
    assert (not_remote(make_facts(**fields), CTX) is not None) is hit


@pytest.mark.parametrize(
    ("title", "hit"),
    [
        ("Senior Account Executive", True),
        ("Product Designer", True),
        ("HR Generalist", True),
        ("Sales Engineer", False),  # engineering term wins
        ("Customer Support Engineer", False),
        ("Backend Developer", False),
        ("Data Analyst", False),  # neither list: not a clear non-engineering role
    ],
)
def test_non_engineering_role(title: str, hit: bool) -> None:
    assert (non_engineering_role(make_facts(title=title), CTX) is not None) is hit


def test_stale() -> None:
    assert stale(make_facts(posted_at=NOW - timedelta(days=31)), CTX) is not None
    assert stale(make_facts(posted_at=NOW - timedelta(days=29)), CTX) is None
    assert stale(make_facts(posted_at=None), CTX) is None
```

Run: `uv run pytest tests/test_rules_basic.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.rules.facts'` (или `context`)

- [ ] **Step 3: `src/aijobradar/rules/facts.py`**

```python
import uuid
from dataclasses import dataclass
from datetime import datetime

from aijobradar.db.models import Job


@dataclass(frozen=True)
class JobFacts:
    """Everything a rule may look at, detached from the ORM session."""

    job_id: uuid.UUID
    company: str
    title: str
    title_norm: str
    location_text: str
    location_restrictions: tuple[str, ...]
    description: str
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    posted_at: datetime | None

    @classmethod
    def from_job(cls, job: Job) -> "JobFacts":
        return cls(
            job_id=job.id,
            company=job.company_raw,
            title=job.title_raw,
            title_norm=job.title_norm,
            location_text=job.location_text or "",
            location_restrictions=tuple(job.location_restrictions or ()),
            description=job.description_text,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            posted_at=job.posted_at,
        )
```

- [ ] **Step 4: Шаблоны гео-паттернов `src/aijobradar/rules/geo.py`** (правила допишет Task 4)

```python
import re
from dataclasses import dataclass

# "{P}" is replaced by Gazetteer.place_group. (?<![\w.]) / (?![\w]) keep "US" from matching
# inside words; the group itself keeps short codes case-sensitive.
_COUNTRY_ONLY = (
    r"(?<![\w.]){P}(?![\w])\s*[-(]?\s*only\b",
    r"\bonly\s+(?:open\s+to\s+|accepting\s+|hiring\s+)?(?:candidates|applicants|people|residents)?"
    r"\s*(?:who\s+(?:are|live)\s+)?(?:based|located|residing|living)?\s*in\s+(?:the\s+)?{P}(?![\w])",
    r"\bmust\s+(?:be\s+)?(?:based|located|residing|reside|live|living)\s+in\s+(?:the\s+)?{P}(?![\w])",
)
_REMOTE_PLACE = r"\bremote\s*[-–(,:/]\s*{P}(?![\w])"
_RESIDENCY = (
    r"(?<![\w.]){P}(?![\w])\s+(?:residents?|citizens?|nationals?)\s+only\b",
    r"\bonly\s+(?:open\s+to\s+)?(?:residents|citizens|nationals)\s+of\s+(?:the\s+)?{P}(?![\w])",
    r"\b(?:right|authori[sz]ed|eligible|legally\s+(?:able|authori[sz]ed))\s+to\s+work\s+in\s+"
    r"(?:the\s+)?{P}(?![\w])",
    r"\bwork\s+(?:authori[sz]ation|permit)\s+(?:in|for)\s+(?:the\s+)?{P}(?![\w])",
    r"(?<![\w.]){P}(?![\w])\s+citizenship\s+(?:is\s+)?required\b",
)
_CLEARANCE = (
    r"\b(?:active|current|valid|secret|top\s+secret|ts/sci|government)\s+(?:security\s+)?clearance\b"
    r"|\bsecurity\s+clearance\s+(?:is\s+)?required\b"
    r"|\bmust\s+(?:hold|have|obtain)\s+(?:an?\s+)?(?:active\s+)?security\s+clearance\b"
)


@dataclass(frozen=True)
class GeoPatterns:
    country_only: tuple[re.Pattern[str], ...]
    remote_place: re.Pattern[str]
    residency: tuple[re.Pattern[str], ...]
    clearance: re.Pattern[str]


def compile_geo_patterns(place_group: str) -> GeoPatterns:
    def c(template: str) -> re.Pattern[str]:
        return re.compile(template.replace("{P}", place_group), re.IGNORECASE)

    return GeoPatterns(
        country_only=tuple(c(t) for t in _COUNTRY_ONLY),
        remote_place=c(_REMOTE_PLACE),
        residency=tuple(c(t) for t in _RESIDENCY),
        clearance=re.compile(_CLEARANCE, re.IGNORECASE),
    )
```

- [ ] **Step 5: `src/aijobradar/rules/context.py`**

```python
import re
from dataclasses import dataclass
from datetime import datetime

from aijobradar.config import RulesConfig
from aijobradar.profile import Profile
from aijobradar.rules.geo import GeoPatterns, compile_geo_patterns
from aijobradar.rules.places import Gazetteer


@dataclass(frozen=True)
class RuleContext:
    """Config + profile with every regex compiled once per run."""

    cfg: RulesConfig
    profile: Profile
    gazetteer: Gazetteer
    now: datetime
    eligible: frozenset[str]
    not_remote_title: tuple[re.Pattern[str], ...]
    not_remote_location: tuple[re.Pattern[str], ...]
    not_remote_description: tuple[re.Pattern[str], ...]
    non_eng_terms: tuple[re.Pattern[str], ...]
    eng_terms: tuple[re.Pattern[str], ...]
    employer_markers: dict[str, tuple[re.Pattern[str], ...]]
    employer_currencies: dict[str, frozenset[str]]
    geo: GeoPatterns


def _compile(patterns: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _terms(terms: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(rf"\b{re.escape(t.casefold())}\b") for t in terms)


def build_rule_context(cfg: RulesConfig, profile: Profile, now: datetime) -> RuleContext:
    gazetteer = Gazetteer(cfg.places, cfg.case_sensitive_aliases)
    unknown = set(profile.eligible_places) - gazetteer.codes
    if unknown:
        raise ValueError(f"eligible_places has codes missing from places: {sorted(unknown)}")
    excluded = profile.excluded_employer_countries
    unsupported = [
        c
        for c in excluded
        if c not in cfg.employer_country_markers and c not in cfg.employer_country_currencies
    ]
    if unsupported:
        # A silently inactive exclusion would be a dishonest filter.
        raise ValueError(f"no employer markers or currencies for: {unsupported}")
    return RuleContext(
        cfg=cfg,
        profile=profile,
        gazetteer=gazetteer,
        now=now,
        eligible=frozenset(profile.eligible_places),
        not_remote_title=_compile(cfg.not_remote_title_patterns),
        not_remote_location=_compile(cfg.not_remote_location_patterns),
        not_remote_description=_compile(cfg.not_remote_description_patterns),
        non_eng_terms=_terms(cfg.non_engineering_title_terms),
        eng_terms=_terms(cfg.engineering_title_terms),
        employer_markers={c: _compile(cfg.employer_country_markers.get(c, [])) for c in excluded},
        employer_currencies={
            c: frozenset(cfg.employer_country_currencies.get(c, [])) for c in excluded
        },
        geo=compile_geo_patterns(gazetteer.place_group),
    )
```

- [ ] **Step 6: `src/aijobradar/rules/basic.py`**

```python
import re
from collections.abc import Iterable
from datetime import timedelta

from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts


def _first(patterns: Iterable[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def not_remote(f: JobFacts, ctx: RuleContext) -> str | None:
    for patterns, text, field in (
        (ctx.not_remote_title, f.title, "title"),
        (ctx.not_remote_location, f.location_text, "location"),
        (ctx.not_remote_description, f.description, "description"),
    ):
        found = _first(patterns, text)
        if found:
            return f"{field}: {found}"
    return None


def non_engineering_role(f: JobFacts, ctx: RuleContext) -> str | None:
    if _first(ctx.eng_terms, f.title_norm):
        return None
    found = _first(ctx.non_eng_terms, f.title_norm)
    return f"title: {found}" if found else None


def stale(f: JobFacts, ctx: RuleContext) -> str | None:
    if f.posted_at is None:
        return None
    if f.posted_at < ctx.now - timedelta(days=ctx.cfg.stale_days):
        return f"posted {f.posted_at.date().isoformat()}"
    return None
```

- [ ] **Step 7: Тесты зелёные**

Run: `uv run pytest tests/test_rules_basic.py tests/test_places.py tests/test_rules_config.py && uv run ruff check . && uv run mypy src`
Expected: PASS.

---

### Task 4: Гео-правила

**Files:**
- Modify: `src/aijobradar/rules/geo.py` (добавить правила)
- Create: `tests/test_rules_geo.py`

**Interfaces:**
- Consumes: `RuleContext` (`gazetteer`, `eligible`, `geo`), `JobFacts`
- Produces: `geo.geo_country_only(f: JobFacts, ctx: RuleContext) -> str | None`, `geo.geo_residency(f: JobFacts, ctx: RuleContext) -> str | None`

Логика (уточнение 4): структурные ограничения → отсев, только если все известны и ни одно не в `eligible`. Текст (заголовок + локация + описание): фраза «X only» / «must be located in X» / «Remote - X» с известным X не из `eligible` → отсев. Резидентство: «X residents only», «right to work in X», «work authorization in X», «X citizenship required» с X не из `eligible`; требование допуска (clearance) — всегда.

- [ ] **Step 1: Падающие тесты**

`tests/test_rules_geo.py`:
```python
import pytest

from aijobradar.rules.geo import geo_country_only, geo_residency
from tests.rules_support import make_ctx, make_facts

CTX = make_ctx()  # eligible: PT, EU, EUROPE, EMEA, WORLDWIDE


@pytest.mark.parametrize(
    ("restrictions", "hit"),
    [
        (["United States"], True),
        (["Netherlands", "United Kingdom"], True),
        (["Canada", "United States", "United States of America"], True),
        (["Portugal"], False),
        (["Europe"], False),
        (["Remote"], False),  # unknown token = ambiguous
        (["United States", "Narnia"], False),  # any unknown token = ambiguous
        ([], False),
    ],
)
def test_structured_restrictions(restrictions: list[str], hit: bool) -> None:
    facts = make_facts(location_restrictions=restrictions)
    assert (geo_country_only(facts, CTX) is not None) is hit


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"description": "This role is US only."}, True),
        ({"description": "Please contact us only via email."}, False),
        ({"location_text": "Remote - US"}, True),
        ({"title": "Forward Deployed Engineer - Remote, US"}, True),
        ({"location_text": "Remote - Europe"}, False),
        ({"description": "You must be located in a timezone close to CET."}, False),
        ({"description": "Only open to candidates based in the United States."}, True),
        ({"description": "EMEA only"}, False),
        ({"description": "Must be based in Canada."}, True),
    ],
)
def test_text_country_only(fields: dict[str, str], hit: bool) -> None:
    assert (geo_country_only(make_facts(**fields), CTX) is not None) is hit


@pytest.mark.parametrize(
    ("description", "hit"),
    [
        ("US citizens only.", True),
        ("You must have the right to work in the UK.", True),
        ("Candidates must be authorized to work in the United States.", True),
        ("Work authorization in Canada is required.", True),
        ("You need to be eligible to work in the EU.", False),  # EU is eligible here
        ("EU timezone preferred.", False),
        ("An active security clearance is required.", True),
        ("No security clearance needed.", False),
    ],
)
def test_residency(description: str, hit: bool) -> None:
    assert (geo_residency(make_facts(description=description), CTX) is not None) is hit


def test_eligibility_comes_from_profile() -> None:
    us_ctx = make_ctx(eligible_places=["US", "WORLDWIDE"])
    facts = make_facts(location_restrictions=["United States"], description="US citizens only.")
    assert geo_country_only(facts, us_ctx) is None
    assert geo_residency(facts, us_ctx) is None
```

Run: `uv run pytest tests/test_rules_geo.py`
Expected: FAIL — `ImportError: cannot import name 'geo_country_only'`

- [ ] **Step 2: Дописать правила в `src/aijobradar/rules/geo.py`**

В начало модуля добавить импорты (`TYPE_CHECKING`, чтобы не было цикла с `context.py`, который импортирует `GeoPatterns` отсюда):
```python
from typing import TYPE_CHECKING

from aijobradar.rules.facts import JobFacts

if TYPE_CHECKING:
    from aijobradar.rules.context import RuleContext
```
В конец модуля:
```python
def _foreign_place(
    patterns: tuple[re.Pattern[str], ...], text: str, ctx: "RuleContext"
) -> str | None:
    """First phrase whose place is known and not eligible for the candidate."""
    for pattern in patterns:
        for match in pattern.finditer(text):
            code = ctx.gazetteer.canonical(match.group("place"))
            if code is not None and code not in ctx.eligible:
                return f"{code}: {match.group(0)}"
    return None


def geo_country_only(f: JobFacts, ctx: "RuleContext") -> str | None:
    if f.location_restrictions:
        codes = [ctx.gazetteer.canonical(t) for t in f.location_restrictions]
        # Any unknown token (a city, "Remote") makes the restriction ambiguous: leave it to the LLM.
        if all(c is not None for c in codes) and not any(c in ctx.eligible for c in codes):
            return "restrictions: " + ", ".join(sorted({c for c in codes if c}))
    head = f"{f.title}\n{f.location_text}"
    return _foreign_place((ctx.geo.remote_place,), head, ctx) or _foreign_place(
        ctx.geo.country_only, f"{head}\n{f.description}", ctx
    )


def geo_residency(f: JobFacts, ctx: "RuleContext") -> str | None:
    text = f"{f.title}\n{f.location_text}\n{f.description}"
    found = _foreign_place(ctx.geo.residency, text, ctx)
    if found:
        return found
    clearance = ctx.geo.clearance.search(text)
    return f"clearance: {clearance.group(0)}" if clearance else None
```

- [ ] **Step 3: Тесты зелёные**

Run: `uv run pytest tests/test_rules_geo.py && uv run ruff check . && uv run mypy src`
Expected: PASS. Если какой-то кейс из таблицы не проходит — отчитаться, какой и почему; ожидаемые значения не менять.

---

### Task 5: Страна работодателя и порог ставки

**Files:**
- Create: `src/aijobradar/rules/employer.py`, `src/aijobradar/rules/money.py`, `tests/test_rules_employer_money.py`

**Interfaces:**
- Consumes: `RuleContext` (`employer_markers`, `employer_currencies`, `profile`, `cfg`), `JobFacts`
- Produces: `employer.employer_country(f, ctx) -> str | None`, `money.rate_floor(f, ctx) -> str | None`, `money.usd_per_hour(amount: float, currency: str, period: str, cfg: RulesConfig) -> float | None`

Порог ставки: используется **потолок** (`salary_max`). Нет потолка, валюта неизвестна, период неизвестен или профиль без порога → не отсев. Отсев, если `потолок_в_USD/час < порог × (1 − rate_margin)`.

- [ ] **Step 1: Падающие тесты**

`tests/test_rules_employer_money.py`:
```python
import pytest

from aijobradar.rules.employer import employer_country
from aijobradar.rules.money import rate_floor, usd_per_hour
from tests.rules_support import CFG, make_ctx, make_facts

CTX = make_ctx()  # excluded RU, floor 20 USD/h


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"description": "Оформление по ТК РФ, белая зарплата."}, True),
        ({"title": "Backend-разработчик (от 200 000 ₽)"}, True),
        ({"company": "ООО «Ромашка»"}, True),
        ({"description": "Аккредитованная IT-компания."}, True),
        ({"salary_currency": "RUB"}, True),
        ({"description": "Russian-speaking team, payments in USD."}, False),
        ({"description": "Команда говорит по-русски, оплата в USD."}, False),
    ],
)
def test_employer_country(fields: dict[str, str], hit: bool) -> None:
    assert (employer_country(make_facts(**fields), CTX) is not None) is hit


def test_employer_country_inactive_without_exclusions() -> None:
    ctx = make_ctx(excluded_employer_countries=[])
    assert employer_country(make_facts(description="Оформление по ТК РФ"), ctx) is None


def test_usd_per_hour() -> None:
    assert usd_per_hour(2080, "USD", "year", CFG) == pytest.approx(1.0)
    assert usd_per_hour(10, "XYZ", "hour", CFG) is None
    assert usd_per_hour(10, "USD", "fortnight", CFG) is None


@pytest.mark.parametrize(
    ("salary_min", "salary_max", "currency", "period", "hit"),
    [
        (None, 30000, "USD", "year", True),  # 14.4 $/h
        (None, 60000, "EUR", "year", False),  # 31.2 $/h
        (None, 18.5, "USD", "hour", False),  # within the 10% margin (>= 18)
        (None, 17, "USD", "hour", True),
        (10, None, "USD", "hour", False),  # no ceiling stated
        (None, 10, "XYZ", "hour", False),  # unknown currency
        (None, 10, "USD", None, False),  # unknown period
    ],
)
def test_rate_floor(
    salary_min: float | None, salary_max: float | None, currency: str, period: str | None, hit: bool
) -> None:
    facts = make_facts(salary_min=salary_min, salary_max=salary_max, salary_currency=currency,
                       salary_period=period)
    assert (rate_floor(facts, CTX) is not None) is hit


def test_rate_floor_off_without_profile_floor() -> None:
    ctx = make_ctx(min_rate_usd_per_hour=None)
    facts = make_facts(salary_max=1, salary_currency="USD", salary_period="hour")
    assert rate_floor(facts, ctx) is None
```

Run: `uv run pytest tests/test_rules_employer_money.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.rules.employer'`

- [ ] **Step 2: `src/aijobradar/rules/employer.py`**

```python
from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts


def employer_country(f: JobFacts, ctx: RuleContext) -> str | None:
    text = f"{f.company}\n{f.title}\n{f.description}"
    for country, patterns in ctx.employer_markers.items():
        if f.salary_currency and f.salary_currency.upper() in ctx.employer_currencies[country]:
            return f"{country}: currency {f.salary_currency}"
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return f"{country}: {match.group(0)}"
    for country, currencies in ctx.employer_currencies.items():
        if f.salary_currency and f.salary_currency.upper() in currencies:
            return f"{country}: currency {f.salary_currency}"
    return None
```

- [ ] **Step 3: `src/aijobradar/rules/money.py`**

```python
from aijobradar.config import RulesConfig
from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts


def usd_per_hour(amount: float, currency: str, period: str, cfg: RulesConfig) -> float | None:
    rate = cfg.fx_to_usd.get(currency.upper())
    hours = cfg.hours_per_period.get(period)
    if rate is None or not hours:
        return None
    return amount * rate / hours


def rate_floor(f: JobFacts, ctx: RuleContext) -> str | None:
    floor = ctx.profile.min_rate_usd_per_hour
    # Only a stated ceiling proves the job pays too little; a minimum alone proves nothing.
    if floor is None or f.salary_max is None or not f.salary_currency or not f.salary_period:
        return None
    hourly = usd_per_hour(f.salary_max, f.salary_currency, f.salary_period, ctx.cfg)
    if hourly is None or hourly >= floor * (1 - ctx.cfg.rate_margin):
        return None
    return f"ceiling {f.salary_max:g} {f.salary_currency}/{f.salary_period} ≈ {hourly:.1f} USD/h"
```

- [ ] **Step 4: Тесты зелёные**

Run: `uv run pytest tests/test_rules_employer_money.py && uv run ruff check . && uv run mypy src`
Expected: PASS.

---

### Task 6: Схема БД для решений правил и возврат в `new` при расширении гео

**Files:**
- Modify: `src/aijobradar/db/models.py` (добавить `RuleDecision`, `ReviewItem`), `src/aijobradar/store.py`
- Create: `src/aijobradar/db/alembic/versions/0002_rules.py`
- Modify: `src/aijobradar/ingest.py`, `tests/test_ingest.py` (тесты возврата в `new` и повтора той же записи), создать `tests/test_store_rules.py`

**Interfaces:**
- Produces:
  - `db.models.RuleDecision`: `id` (BigInteger Identity pk), `job_id` (FK jobs.id CASCADE, index), `run_id` (FK runs.id CASCADE), `rules_version: str` (String(32)), `verdict: str` (String(8): `pass`/`reject`), `rule_ids: list[str]` (JSONB), `details: dict[str, str]` (JSONB), `decided_at` (timestamptz)
  - `db.models.ReviewItem` (`__tablename__ = "review_queue"`): `id` (BigInteger Identity pk), `job_id` (FK jobs.id CASCADE), `kind: str` (String(32)), `added_run_id` (FK runs.id CASCADE), `created_at` (timestamptz); `UniqueConstraint("job_id", "kind", name="uq_review_queue_job_kind")`
  - `store.jobs_in_state(session, state: str) -> list[Job]` (по `first_seen_at, id`)
  - `store.record_rule_decision(session, *, job_id: uuid.UUID, run_id: uuid.UUID, rules_version: str, hits: dict[str, str], now: datetime) -> None` (verdict выводится из `hits`)
  - `store.set_job_state(session, job: Job, state: str) -> None`
  - `store.add_review_item(session, *, job_id: uuid.UUID, kind: str, run_id: uuid.UUID, now: datetime) -> bool` (`False`, если такая пара уже есть)
  - `store.attach_source` — дополнительно: если гео изменилось и `state == "rejected"` → `state = "new"`
  - `store.touch_source(session, link: JobSource, raw: RawJob, now: datetime) -> None` — **новая сигнатура** (уточнение 8): `last_seen_at = max(старое, now)`, `source_url = raw.source_url`, гео вакансии расширяется тем же `_widen_job`, что и в `attach_source`

- [ ] **Step 1: Модели** — добавить в `src/aijobradar/db/models.py` (импорт `UniqueConstraint` уже есть):

```python
class RuleDecision(Base):
    __tablename__ = "rule_decisions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    rules_version: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str] = mapped_column(String(8))
    rule_ids: Mapped[list[str]] = mapped_column(JSONB)
    # Evidence per rule: third-party text and profile thresholds — DB only, never printed.
    details: Mapped[dict[str, str]] = mapped_column(JSONB)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewItem(Base):
    __tablename__ = "review_queue"
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_review_queue_job_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    added_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 2: Миграция `src/aijobradar/db/alembic/versions/0002_rules.py`**

```python
"""rule decisions and review queue

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_decisions",
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
        sa.Column("rules_version", sa.String(32), nullable=False),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("rule_ids", postgresql.JSONB(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rule_decisions_job_id", "rule_decisions", ["job_id"])
    op.create_table(
        "review_queue",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "added_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "kind", name="uq_review_queue_job_kind"),
    )


def downgrade() -> None:
    op.drop_table("review_queue")
    op.drop_index("ix_rule_decisions_job_id", table_name="rule_decisions")
    op.drop_table("rule_decisions")
```

- [ ] **Step 3: Падающие тесты хранилища**

`tests/test_store_rules.py`:
```python
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Job, ReviewItem, RuleDecision
from aijobradar.models import RawJob
from aijobradar.normalize import normalize

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)


def _job(session: Session, sid: str = "1") -> tuple[Job, object]:
    run = store.start_run(session, "fetch", NOW)
    raw = RawJob(source="s", source_job_id=sid, source_url=f"https://s/{sid}",
                 title="Backend Engineer", company="Acme")
    return store.insert_job(session, normalize(raw), run.id, NOW), run


def test_record_decision_and_state(session: Session) -> None:
    job, run = _job(session)
    store.record_rule_decision(session, job_id=job.id, run_id=run.id,  # type: ignore[attr-defined]
                               rules_version="v1", hits={"R-STALE": "posted 2026-01-01"}, now=NOW)
    store.set_job_state(session, job, "rejected")
    decision = session.scalars(select(RuleDecision)).one()
    assert (decision.verdict, decision.rule_ids, decision.rules_version) == (
        "reject", ["R-STALE"], "v1")
    assert decision.details == {"R-STALE": "posted 2026-01-01"}
    assert session.get(Job, job.id).state == "rejected"  # type: ignore[union-attr]


def test_pass_decision_has_empty_rule_ids(session: Session) -> None:
    job, run = _job(session)
    store.record_rule_decision(session, job_id=job.id, run_id=run.id,  # type: ignore[attr-defined]
                               rules_version="v1", hits={}, now=NOW)
    decision = session.scalars(select(RuleDecision)).one()
    assert (decision.verdict, decision.rule_ids, decision.details) == ("pass", [], {})


def test_review_item_is_unique_per_kind(session: Session) -> None:
    job, run = _job(session)
    kw = {"job_id": job.id, "kind": "rule_rejected_sample", "run_id": run.id,  # type: ignore[attr-defined]
          "now": NOW}
    assert store.add_review_item(session, **kw) is True  # type: ignore[arg-type]
    assert store.add_review_item(session, **kw) is False  # type: ignore[arg-type]
    assert len(session.scalars(select(ReviewItem)).all()) == 1


def test_jobs_in_state_filters_and_orders(session: Session) -> None:
    a, _ = _job(session, "a")
    b, _ = _job(session, "b")
    store.set_job_state(session, b, "rejected")
    assert [j.id for j in store.jobs_in_state(session, "new")] == [a.id]
```

В `tests/test_ingest.py` дописать (помощники `_raw`, `_ingest`, `NOW` уже есть в файле):
```python
def _set_state(session: Session, state: str) -> None:
    job = session.scalars(select(Job)).one()
    store.set_job_state(session, job, state)


def test_widened_geo_reopens_rejected_job(session: Session) -> None:
    us = _raw("himalayas", "1", "Full Stack Engineer")
    us = us.model_copy(update={"location_restrictions": ["United States"]})
    _ingest(session, us)
    _set_state(session, "rejected")
    worldwide = _raw("wwr", "a", "Full Stack Engineer")  # [] = unrestricted
    assert _ingest(session, worldwide) is DedupOutcome.MERGED
    assert session.scalars(select(Job)).one().state == "new"


def test_unchanged_geo_keeps_rejection(session: Session) -> None:
    us = _raw("himalayas", "1", "Full Stack Engineer")
    us = us.model_copy(update={"location_restrictions": ["United States"]})
    _ingest(session, us)
    _set_state(session, "rejected")
    _ingest(session, us.model_copy(update={"source": "wwr", "source_job_id": "a"}))
    assert session.scalars(select(Job)).one().state == "rejected"


def test_widening_does_not_touch_pending_job(session: Session) -> None:
    us = _raw("himalayas", "1", "Full Stack Engineer")
    us = us.model_copy(update={"location_restrictions": ["United States"]})
    _ingest(session, us)
    _set_state(session, "pending_score")
    _ingest(session, _raw("wwr", "a", "Full Stack Engineer"))
    assert session.scalars(select(Job)).one().state == "pending_score"


def test_same_record_with_widened_geo_widens_and_reopens(session: Session) -> None:
    _ingest(session, _raw("himalayas", "1", "Full Stack Engineer", locations=["United States"]))
    _set_state(session, "rejected")
    again = _raw("himalayas", "1", "Full Stack Engineer", locations=["United States", "Canada"])
    assert _ingest(session, again) is DedupOutcome.SEEN
    job = session.scalars(select(Job)).one()
    assert (job.location_restrictions, job.state) == (["Canada", "United States"], "new")


def test_same_record_never_narrows_geo(session: Session) -> None:
    _ingest(session, _raw("himalayas", "1", "Full Stack Engineer"))  # [] = unrestricted
    _ingest(session, _raw("himalayas", "1", "Full Stack Engineer", locations=["United States"]))
    assert session.scalars(select(Job)).one().location_restrictions == []


def test_same_record_updates_url_and_last_seen_never_goes_back(session: Session) -> None:
    _ingest(session, _raw("himalayas", "1", "Full Stack Engineer"))
    moved = _raw("himalayas", "1", "Full Stack Engineer", url="https://himalayas/moved")
    _ingest(session, moved, now=NOW - timedelta(days=1))  # an older run replayed late
    link = session.scalars(select(JobSource)).one()
    assert (link.source_url, link.last_seen_at) == ("https://himalayas/moved", NOW)
```

Run: `uv run pytest tests/test_store_rules.py tests/test_ingest.py tests/test_db_schema.py`
Expected: FAIL — `AttributeError: module 'aijobradar.store' has no attribute 'record_rule_decision'` (и падения новых тестов в `test_ingest.py`: гео при повторе той же записи не расширяется, URL не обновляется).

- [ ] **Step 4: `src/aijobradar/store.py`** — дописать функции и изменить `attach_source`

Новые импорты: `from sqlalchemy.dialects.postgresql import insert as pg_insert`; в импорт моделей добавить `ReviewItem, RuleDecision`.

```python
def jobs_in_state(session: Session, state: str) -> list[Job]:
    return list(
        session.scalars(select(Job).where(Job.state == state).order_by(Job.first_seen_at, Job.id))
    )


def record_rule_decision(
    session: Session,
    *,
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    rules_version: str,
    hits: dict[str, str],
    now: datetime,
) -> None:
    session.add(
        RuleDecision(
            job_id=job_id,
            run_id=run_id,
            rules_version=rules_version,
            verdict="reject" if hits else "pass",
            rule_ids=list(hits),
            details=hits,
            decided_at=now,
        )
    )
    session.flush()


def set_job_state(session: Session, job: Job, state: str) -> None:
    job.state = state
    session.flush()


def add_review_item(
    session: Session, *, job_id: uuid.UUID, kind: str, run_id: uuid.UUID, now: datetime
) -> bool:
    inserted = session.execute(
        pg_insert(ReviewItem)
        .values(job_id=job_id, kind=kind, added_run_id=run_id, created_at=now)
        .on_conflict_do_nothing(constraint="uq_review_queue_job_kind")
        .returning(ReviewItem.id)
    ).first()
    return inserted is not None
```

Общая часть `attach_source` и `touch_source` выносится в `_widen_job` (гео + `last_seen_at` + возврат отсеянной вакансии в `new`); `attach_source` и `touch_source` целиком заменяются на:
```python
def _widen_job(session: Session, job_id: uuid.UUID, raw: RawJob, now: datetime) -> None:
    """Widen the job's geo to cover this record; a rejection resting on narrower geo re-runs."""
    geo = session.execute(
        select(Job.location_restrictions, Job.timezone_restrictions, Job.state).where(
            Job.id == job_id
        )
    ).one()
    locations = _widen_locations(geo.location_restrictions, raw.location_restrictions)
    timezones = _widen_timezones(geo.timezone_restrictions, raw.timezone_restrictions)
    values: dict[str, object] = {
        "last_seen_at": func.greatest(Job.last_seen_at, now),
        "location_restrictions": locations,
        "timezone_restrictions": timezones,
    }
    widened = (locations, timezones) != (geo.location_restrictions, geo.timezone_restrictions)
    if widened and geo.state == "rejected":
        values["state"] = "new"
    session.execute(update(Job).where(Job.id == job_id).values(**values))


def attach_source(session: Session, job_id: uuid.UUID, raw: RawJob, now: datetime) -> None:
    """Link another source record to an existing job and widen the job's geo to cover it."""
    session.add(
        JobSource(
            job_id=job_id,
            source=raw.source,
            source_job_id=raw.source_job_id,
            source_url=raw.source_url,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    _widen_job(session, job_id, raw, now)
    session.flush()


def touch_source(session: Session, link: JobSource, raw: RawJob, now: datetime) -> None:
    """The same record again: its geo may have widened and its URL moved."""
    link.last_seen_at = max(link.last_seen_at, now)
    link.source_url = raw.source_url
    _widen_job(session, link.job_id, raw, now)
    session.flush()
```
В `src/aijobradar/ingest.py`: `store.touch_source(session, link, raw, now)`.

- [ ] **Step 5: Тесты зелёные** (включая сверку моделей с миграциями)

Run: `uv run pytest tests/test_store_rules.py tests/test_ingest.py tests/test_db_schema.py && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS; `test_models_match_migrations` — пустой diff.

---

### Task 7: Движок правил

**Files:**
- Create: `src/aijobradar/rules/engine.py`, `tests/test_rules_engine.py`

**Interfaces:**
- Consumes: все правила (Tasks 3–5), `RuleContext`, `JobFacts.from_job`, `store.jobs_in_state/record_rule_decision/set_job_state/add_review_item`
- Produces:
  - `engine.RuleFn = Callable[[JobFacts, RuleContext], str | None]`
  - `engine.RULES: tuple[tuple[str, RuleFn], ...]` — порядок: `R-NOT-REMOTE`, `R-GEO-COUNTRY-ONLY`, `R-GEO-RESIDENCY`, `R-EMPLOYER-COUNTRY`, `R-RATE-FLOOR`, `R-NON-ENG-ROLE`, `R-STALE`
  - `engine.evaluate(facts: JobFacts, ctx: RuleContext, rules: Sequence[tuple[str, RuleFn]] = RULES) -> dict[str, str]` — все сработавшие правила (`rule_id → улика`, ≤ 200 символов, одной строкой)
  - `engine.RulesReport` (dataclass): `evaluated: int = 0`, `rejected: int = 0`, `by_rule: Counter[str]`, `sampled: int = 0`, `errors: int = 0`, `first_error: str | None = None`; свойство `passed = evaluated - rejected`
  - `engine.apply_rules(session, *, run_id: uuid.UUID, ctx: RuleContext, rng: random.Random, rules: Sequence[tuple[str, RuleFn]] = RULES) -> RulesReport`

Поведение `apply_rules`: для каждой вакансии в `new` (по порядку `jobs_in_state`) — в своём savepoint: `evaluate` → `record_rule_decision` → состояние `rejected` или `pending_score`. Исключение → savepoint откатывается, вакансия остаётся `new`, `errors += 1`, `first_error = имя класса исключения`. В конце: `rng.sample` из отсеянных в этом прогоне, не больше `review_sample_size`, → `add_review_item(kind="rule_rejected_sample")`; `sampled` = число реально вставленных.

- [ ] **Step 1: Падающие тесты**

`tests/test_rules_engine.py`:
```python
import random
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Job, ReviewItem, RuleDecision
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.rules.engine import RULES, apply_rules, evaluate
from tests.rules_support import NOW, make_ctx, make_facts

CTX = make_ctx()


def _insert(session: Session, run_id: uuid.UUID, sid: str, **raw: object) -> Job:
    data: dict[str, object] = {"source": "s", "source_job_id": sid,
                               "source_url": f"https://s/{sid}", "title": f"Backend Engineer {sid}",
                               "company": f"Company {sid}"}
    data.update(raw)
    return store.insert_job(session, normalize(RawJob.model_validate(data)), run_id, NOW)


def test_rule_order_is_fixed() -> None:
    assert [rule_id for rule_id, _ in RULES] == [
        "R-NOT-REMOTE", "R-GEO-COUNTRY-ONLY", "R-GEO-RESIDENCY", "R-EMPLOYER-COUNTRY",
        "R-RATE-FLOOR", "R-NON-ENG-ROLE", "R-STALE"]


def test_evaluate_collects_every_hit() -> None:
    facts = make_facts(title="Sales Manager (Hybrid)", location_restrictions=["United States"])
    hits = evaluate(facts, CTX)
    assert list(hits) == ["R-NOT-REMOTE", "R-GEO-COUNTRY-ONLY", "R-NON-ENG-ROLE"]
    assert all("\n" not in v and len(v) <= 200 for v in hits.values())


def test_evaluate_clean_job() -> None:
    assert evaluate(make_facts(), CTX) == {}


def test_apply_rules_sets_states_and_records(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    us = _insert(session, run.id, "us", location_restrictions=["United States"])
    ok = _insert(session, run.id, "ok")
    report = apply_rules(session, run_id=run.id, ctx=CTX, rng=random.Random(0))
    assert (report.evaluated, report.rejected, report.passed) == (2, 1, 1)
    assert report.by_rule == {"R-GEO-COUNTRY-ONLY": 1}
    assert session.get(Job, us.id).state == "rejected"  # type: ignore[union-attr]
    assert session.get(Job, ok.id).state == "pending_score"  # type: ignore[union-attr]
    verdicts = {d.job_id: d.verdict for d in session.scalars(select(RuleDecision))}
    assert verdicts == {us.id: "reject", ok.id: "pass"}


def test_only_new_jobs_are_evaluated(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    done = _insert(session, run.id, "done")
    store.set_job_state(session, done, "pending_score")
    assert apply_rules(session, run_id=run.id, ctx=CTX, rng=random.Random(0)).evaluated == 0


def test_review_sample_is_capped_and_not_duplicated(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    for i in range(7):
        _insert(session, run.id, f"r{i}", location_restrictions=["United States"])
    report = apply_rules(session, run_id=run.id, ctx=CTX, rng=random.Random(0))
    assert (report.rejected, report.sampled) == (7, 5)
    assert len(session.scalars(select(ReviewItem)).all()) == 5


def test_rule_error_leaves_job_new_and_is_counted(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    broken = _insert(session, run.id, "boom")
    fine = _insert(session, run.id, "fine")

    def explode(facts, ctx):  # type: ignore[no-untyped-def]
        if facts.job_id == broken.id:
            raise RuntimeError("bug")
        return None

    report = apply_rules(session, run_id=run.id, ctx=CTX, rng=random.Random(0),
                         rules=[("R-TEST", explode)])
    assert (report.evaluated, report.errors, report.first_error) == (1, 1, "RuntimeError")
    assert session.get(Job, broken.id).state == "new"  # type: ignore[union-attr]
    assert session.get(Job, fine.id).state == "pending_score"  # type: ignore[union-attr]
    assert [d.job_id for d in session.scalars(select(RuleDecision))] == [fine.id]
```

Run: `uv run pytest tests/test_rules_engine.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aijobradar.rules.engine'`

- [ ] **Step 2: `src/aijobradar/rules/engine.py`**

```python
import random
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.rules.basic import non_engineering_role, not_remote, stale
from aijobradar.rules.context import RuleContext
from aijobradar.rules.employer import employer_country
from aijobradar.rules.facts import JobFacts
from aijobradar.rules.geo import geo_country_only, geo_residency
from aijobradar.rules.money import rate_floor

RuleFn = Callable[[JobFacts, RuleContext], str | None]

RULES: tuple[tuple[str, RuleFn], ...] = (
    ("R-NOT-REMOTE", not_remote),
    ("R-GEO-COUNTRY-ONLY", geo_country_only),
    ("R-GEO-RESIDENCY", geo_residency),
    ("R-EMPLOYER-COUNTRY", employer_country),
    ("R-RATE-FLOOR", rate_floor),
    ("R-NON-ENG-ROLE", non_engineering_role),
    ("R-STALE", stale),
)
REVIEW_KIND = "rule_rejected_sample"


@dataclass
class RulesReport:
    evaluated: int = 0
    rejected: int = 0
    by_rule: Counter[str] = field(default_factory=Counter)
    sampled: int = 0
    errors: int = 0
    first_error: str | None = None

    @property
    def passed(self) -> int:
        return self.evaluated - self.rejected


def evaluate(
    facts: JobFacts, ctx: RuleContext, rules: Sequence[tuple[str, RuleFn]] = RULES
) -> dict[str, str]:
    """Run every rule (not just until the first hit) so the audit shows all reasons."""
    hits: dict[str, str] = {}
    for rule_id, rule in rules:
        evidence = rule(facts, ctx)
        if evidence is not None:
            hits[rule_id] = " ".join(evidence.split())[:200]
    return hits


def apply_rules(
    session: Session,
    *,
    run_id: uuid.UUID,
    ctx: RuleContext,
    rng: random.Random,
    rules: Sequence[tuple[str, RuleFn]] = RULES,
) -> RulesReport:
    report = RulesReport()
    rejected: list[uuid.UUID] = []
    for job in store.jobs_in_state(session, "new"):
        try:
            with session.begin_nested():  # a buggy rule costs this job one run, not the run
                hits = evaluate(JobFacts.from_job(job), ctx, rules)
                store.record_rule_decision(
                    session,
                    job_id=job.id,
                    run_id=run_id,
                    rules_version=ctx.cfg.version,
                    hits=hits,
                    now=ctx.now,
                )
                store.set_job_state(session, job, "rejected" if hits else "pending_score")
        except Exception as exc:
            report.errors += 1
            report.first_error = report.first_error or type(exc).__name__
            continue
        report.evaluated += 1
        if hits:
            report.rejected += 1
            report.by_rule.update(hits.keys())
            rejected.append(job.id)
    sample = rng.sample(rejected, min(ctx.cfg.review_sample_size, len(rejected)))
    for job_id in sample:
        if store.add_review_item(session, job_id=job_id, kind=REVIEW_KIND, run_id=run_id,
                                 now=ctx.now):
            report.sampled += 1
    return report
```

Примечание: после отката savepoint объект `job` может быть в «просроченном» состоянии; тест `test_rule_error_leaves_job_new_and_is_counted` проверяет, что `state` в БД остался `new`. Если SQLAlchemy при этом перезагружает объект — это ожидаемо.

- [ ] **Step 3: Тесты зелёные**

Run: `uv run pytest tests/test_rules_engine.py && uv run pytest && uv run ruff check . && uv run mypy src`
Expected: PASS.

---

### Task 8: Пайплайн, отчёт и команда `run`

**Files:**
- Modify: `src/aijobradar/pipeline.py`, `src/aijobradar/cli.py`, `tests/test_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `apply_rules`, `RulesReport`, `build_rule_context`, `load_rules_config`, `load_profile`, `ProfileMissing`
- Produces:
  - `pipeline.run_fetch(..., rules_ctx: RuleContext | None = None, rng: random.Random | None = None)` — при `rules_ctx` правила применяются после записи, до `finish_run`; ошибки правил делают прогон `partial` (если он был `ok`); в `run.counts` добавляются `rules_evaluated`, `rules_rejected`, `rules_errors`, `review_sampled`
  - `pipeline.FetchReport.rules: RulesReport | None = None`
  - `format_report` при наличии `rules` добавляет две строки:
    `Отбор правилами: проверено N, отсеяно M (R-A k, R-B j), к оценке K[, ошибок правил E]` (разбивка по убыванию числа, при равенстве — по id; без скобок при M = 0) и `В очередь разбора отсева: S`
  - CLI: команда `run` (вместо `fetch`) с опциями `--config` (`config/sources.yaml`), `--rules` (`config/rules.yaml`), `--profile` (`private/profile.yaml`). Профиль и правила загружаются **до** `Settings()`. Нет профиля → stderr `Нет файла профиля {path}. Скопируйте config/profile.example.yaml в {path} и заполните.`, код выхода 2. Профиль не согласован с правилами (`ValueError` из `build_rule_context`) → `Профиль не согласован с {rules}: {exc}`, код 2.

- [ ] **Step 1: Падающие тесты**

В `tests/test_pipeline.py` дописать (импорты: `import random`, `from collections import Counter`, `from aijobradar.rules.engine import RulesReport`, `from tests.rules_support import make_ctx`):
```python
@respx.mock
def test_run_with_rules_filters_and_reports(session: Session) -> None:
    _mock_sources()
    with httpx.Client() as client:
        report = run_fetch(session, _adapters(), client, now=NOW, dedup_cfg=DedupConfig(),
                           rules_ctx=make_ctx(), rng=random.Random(0))
    # Acme (US/Canada after merge) and Initech (NL+UK after merge) are geo-rejected;
    # Northwind (worldwide wins on merge), Umbrella (Europe), Hooli (anywhere) pass.
    assert report.rules is not None
    assert (report.rules.evaluated, report.rules.rejected, report.rules.sampled) == (5, 2, 2)
    assert report.rules.by_rule == {"R-GEO-COUNTRY-ONLY": 2}
    run = session.get(Run, report.run_id)
    assert run is not None
    assert run.counts["rules_rejected"] == 2 and run.counts["review_sampled"] == 2
    text = format_report(report)
    assert "Отбор правилами: проверено 5, отсеяно 2 (R-GEO-COUNTRY-ONLY 2), к оценке 3" in text
    assert "В очередь разбора отсева: 2" in text


def test_format_report_rules_lines() -> None:
    report = FetchReport(
        run_id=uuid.UUID(int=1), status=RunStatus.OK, sources=[], outcomes={},
        rules=RulesReport(evaluated=9, rejected=4,
                          by_rule=Counter({"R-STALE": 1, "R-GEO-COUNTRY-ONLY": 3, "R-NOT-REMOTE": 1}),
                          sampled=4, errors=1, first_error="RuntimeError"),
    )
    lines = format_report(report).splitlines()
    assert lines[-2] == ("Отбор правилами: проверено 9, отсеяно 4 "
                         "(R-GEO-COUNTRY-ONLY 3, R-NOT-REMOTE 1, R-STALE 1), к оценке 5, "
                         "ошибок правил 1")
    assert lines[-1] == "В очередь разбора отсева: 4"
```
(Если в файле нет импорта `Run`/`FetchReport`/`RunStatus`/`uuid` — добавить. Названия `_mock_sources`, `_adapters`, `NOW` уже есть в файле.)

`tests/test_cli.py` — заменить содержимое:
```python
from pathlib import Path

from typer.testing import CliRunner

from aijobradar.cli import app

ROOT = Path(__file__).parent.parent


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "db" in result.output


def test_run_without_profile_explains_and_exits_2(tmp_path: Path) -> None:
    missing = tmp_path / "profile.yaml"
    result = CliRunner().invoke(app, ["run", "--profile", str(missing)])
    assert result.exit_code == 2
    assert "Нет файла профиля" in result.output
    assert "config/profile.example.yaml" in result.output


def test_run_with_inconsistent_profile_exits_2(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [NARNIA]\n")
    result = CliRunner().invoke(
        app, ["run", "--profile", str(profile), "--rules", str(ROOT / "config" / "rules.yaml")]
    )
    assert result.exit_code == 2
    assert "Профиль не согласован" in result.output
```

Run: `uv run pytest tests/test_pipeline.py tests/test_cli.py`
Expected: FAIL (нет параметра `rules_ctx`, нет команды `run`).

- [ ] **Step 2: `src/aijobradar/pipeline.py`**

- импорты: `import random`, `from aijobradar.rules.context import RuleContext`, `from aijobradar.rules.engine import RulesReport, apply_rules`
- `FetchReport` — новое поле `rules: RulesReport | None = None`
- `run_fetch` — новые keyword-параметры `rules_ctx: RuleContext | None = None`, `rng: random.Random | None = None`; после цикла по источникам и до `finish_run`:
```python
    rules_report: RulesReport | None = None
    if rules_ctx is not None:
        rules_report = apply_rules(session, run_id=run.id, ctx=rules_ctx, rng=rng or random.Random())
    status = run_status(results)
    if rules_report and rules_report.errors and status is RunStatus.OK:
        status = RunStatus.PARTIAL  # jobs left unprocessed are not an "ok" run
    ...
    if rules_report is not None:
        counts.update(
            rules_evaluated=rules_report.evaluated,
            rules_rejected=rules_report.rejected,
            rules_errors=rules_report.errors,
            review_sampled=rules_report.sampled,
        )
```
  и вернуть `FetchReport(..., rules=rules_report)`.
- `format_report` — в конец (до `return`):
```python
    if report.rules is not None:
        r = report.rules
        breakdown = ", ".join(
            f"{rule_id} {n}" for rule_id, n in sorted(r.by_rule.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        line = f"Отбор правилами: проверено {r.evaluated}, отсеяно {r.rejected}"
        if breakdown:
            line += f" ({breakdown})"
        line += f", к оценке {r.passed}"
        if r.errors:
            line += f", ошибок правил {r.errors}"
        lines.append(line)
        lines.append(f"В очередь разбора отсева: {r.sampled}")
```
  Улики (`hits`) в отчёт не попадают — только счётчики.

- [ ] **Step 3: `src/aijobradar/cli.py`** — заменить команду `fetch` на `run`:

```python
@app.command()
def run(
    config: Path = typer.Option(Path("config/sources.yaml"), help="Sources config (YAML)."),
    rules: Path = typer.Option(Path("config/rules.yaml"), help="Rules config (YAML)."),
    profile: Path = typer.Option(
        Path("private/profile.yaml"), help="Candidate profile (YAML, kept out of git)."
    ),
) -> None:
    """Fetch, deduplicate and store jobs, then filter them with deterministic rules."""
    import random

    from sqlalchemy.orm import Session

    from aijobradar.config import Settings, load_config, load_rules_config
    from aijobradar.db.session import make_engine
    from aijobradar.pipeline import RunStatus, format_report, run_fetch
    from aijobradar.profile import ProfileMissing, load_profile
    from aijobradar.rules.context import build_rule_context
    from aijobradar.sources import build_adapters
    from aijobradar.sources.common import make_client

    now = datetime.now(UTC)
    # Profile and rules are checked before anything touches the network or the database.
    try:
        candidate = load_profile(profile)
    except ProfileMissing:
        typer.echo(
            f"Нет файла профиля {profile}. "
            f"Скопируйте config/profile.example.yaml в {profile} и заполните.",
            err=True,
        )
        raise typer.Exit(2) from None
    try:
        rules_ctx = build_rule_context(load_rules_config(rules), candidate, now)
    except ValueError as exc:
        typer.echo(f"Профиль не согласован с {rules}: {exc}", err=True)
        raise typer.Exit(2) from None

    settings = Settings()
    cfg = load_config(config)
    engine = make_engine(settings.sqlalchemy_url)
    with (
        make_client(settings.user_agent, settings.http_timeout_s) as client,
        Session(engine) as session,
        # A failed run is still committed: its statuses are the evidence. The connection is
        # acquired lazily, so it is first used after run_fetch has finished all network I/O.
        session.begin(),
    ):
        report = run_fetch(
            session,
            build_adapters(cfg),
            client,
            now=now,
            dedup_cfg=cfg.dedup,
            rules_ctx=rules_ctx,
            rng=random.Random(),
        )
    typer.echo(format_report(report))
    if report.status is RunStatus.FAILED:
        raise typer.Exit(1)
```
Примечание: `CliRunner` по умолчанию смешивает stderr с `result.output` — тесты на это рассчитывают; если в установленной версии Click это не так, использовать `result.stderr`/`result.output` соответственно и отметить в отчёте.

- [ ] **Step 4: Всё зелёное**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: PASS, прежние ожидания `test_pipeline` (NEW 5 / MERGED 3, SEEN 8) не изменились.

- [ ] **Step 5: Ручной smoke на локальной `aijobradar_dev`** (единственный шаг с сетью)

Контроллер заранее создаёт `private/profile.yaml` (личный, не в git). Затем:

```bash
DATABASE_URL=postgresql://localhost/aijobradar_dev uv run aijobradar db upgrade
```

```bash
DATABASE_URL=postgresql://localhost/aijobradar_dev uv run aijobradar run
```

Ожидание: строки источников, строка «Вакансии: …», строка «Отбор правилами: проверено N, отсеяно M (…), к оценке K» и «В очередь разбора отсева: S». При первом запуске N включает все вакансии этапа 1 из `aijobradar_dev` в состоянии `new`. Обход Himalayas идёт ~6 минут.

**Второй запуск — без Himalayas** (повторный полный обход подряд получает `HTTP 429`): скопировать `config/sources.yaml` во временный файл в scratchpad с `himalayas.enabled: false` и запустить `run --config <копия>`. Ожидание: «проверено» ≈ только новые с Jobicy/WWR. В отчёт задачи — оба вывода целиком (там нет текстов вакансий, только счётчики). Neon и `.env` не трогать.

---

## Self-review (выполнено при написании)

- **Покрытие §6.3:** все семь правил (Tasks 3–5), версия набора в каждом решении (Task 6–7), аудит отсева через `review_queue` (Task 7), отчёт по правилам (Task 8). §6.7: ошибки правил видимы и делают прогон `partial`.
- **Пункты из `stage1-followups.md` «до этапа 2»** закрыты: семантика склейки — уточнение 1 и Task 6; повтор той же записи (гео, URL, `last_seen_at`) — уточнение 8 и Task 6; регион WWR — уточнение 9 и Task 0. После выполнения убрать их из `stage1-followups.md`.
- **Сверено с main после PR #2/#3 (2026-10-09):** `run_status` (все записи не записались → `failed`), строгие конверты источников и Himalayas через `search?sort=recent` не меняют интерфейсов, на которые опирается план; `_mock_sources` в `test_pipeline` уже использует `SEARCH_URL`.
- **Согласованность имён:** `RuleContext`, `build_rule_context`, `JobFacts.from_job`, `RULES`, `evaluate`, `apply_rules`, `RulesReport.passed`, `store.record_rule_decision(hits=…)` — одинаковы во всех задачах.
- **Проверенные предпосылки:** логика справочника мест и текстовых паттернов прогнана прототипом на 14 трудных случаях («contact us only», «must be located in a timezone…», «US & Canada only», «Bosnia and Herzegovina») — все совпали с ожиданием. Ожидание e2e-теста (5 проверено, 2 отсеяно) выведено из синтетических фикстур этапа 1: Acme после склейки = Canada/US, Initech = Netherlands/UK, Northwind = `[]` (worldwide побеждает), Umbrella = Europe/Armenia (Europe подходит), Hooli = anywhere.
