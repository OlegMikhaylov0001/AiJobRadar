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
        side_effect=[
            httpx.Response(200, json=_page("page1.json")),
            httpx.Response(200, json=_page("page2.json")),
        ]
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
