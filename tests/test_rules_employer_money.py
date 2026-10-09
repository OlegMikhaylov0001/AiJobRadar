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
    facts = make_facts(
        salary_min=salary_min, salary_max=salary_max, salary_currency=currency, salary_period=period
    )
    assert (rate_floor(facts, CTX) is not None) is hit


def test_rate_floor_off_without_profile_floor() -> None:
    ctx = make_ctx(min_rate_usd_per_hour=None)
    facts = make_facts(salary_max=1, salary_currency="USD", salary_period="hour")
    assert rate_floor(facts, ctx) is None


@pytest.mark.parametrize(
    ("salary_min", "salary_max", "currency", "period"),
    [
        (None, 0, "USD", "hour"),  # no real ceiling
        (None, 2.5, "BRL", "month"),  # implausible: a parsing artefact, not a wage
        (None, 120, "USD", "year"),
        (20, 10, "USD", "hour"),  # min above max: inconsistent data
    ],
)
def test_rate_floor_must_not_reject_implausible_salary(
    salary_min: float | None, salary_max: float, currency: str, period: str
) -> None:
    facts = make_facts(
        salary_min=salary_min, salary_max=salary_max, salary_currency=currency, salary_period=period
    )
    assert rate_floor(facts, CTX) is None
