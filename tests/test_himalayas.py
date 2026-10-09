import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from aijobradar.models import SalaryPeriod, SourceStatus
from aijobradar.sources.base import run_adapter
from aijobradar.sources.himalayas import SEARCH_URL, HimalayasAdapter
from tests.conftest import fixture_path

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def _page(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads(fixture_path("himalayas", name).read_text())


def _adapter(**kw: object) -> HimalayasAdapter:
    kw.setdefault("sleep", lambda _: None)
    return HimalayasAdapter(now=lambda: NOW, **kw)  # type: ignore[arg-type]


@respx.mock
def test_pages_through_recent_feed_and_classifies_records() -> None:
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=_page("page1.json")),
            httpx.Response(200, json=_page("page2.json")),
        ]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert route.call_count == 2
    assert [dict(c.request.url.params) for c in route.calls] == [
        {"sort": "recent", "page": "1"},
        {"sort": "recent", "page": "2"},
    ]
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
        job["pubDate"] = 1789862400  # 2026-09-20, older than the 30-hour lookback
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=old))
    with httpx.Client() as client:
        run_adapter(_adapter(), client)
    assert route.call_count == 1


@respx.mock
def test_second_page_failure_keeps_first_page_as_degraded() -> None:
    respx.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=_page("page1.json")), httpx.Response(429)]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.DEGRADED
    assert (result.error, result.http_status) == ("page 2: HTTP 429", 429)
    assert len(result.items) == 3


@respx.mock
def test_first_page_failure_is_failed() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert (result.status, result.http_status) == (SourceStatus.FAILED, 503)


def _page_for(request: httpx.Request) -> httpx.Response:
    """Endless feed: page N of page1.json with the offset the API gives page N."""
    page_no = int(request.url.params["page"])
    page = _page("page1.json") | {"offset": (page_no - 1) * 20, "totalCount": 10_000}
    return httpx.Response(200, json=page)


@respx.mock
def test_max_pages_is_a_hard_limit() -> None:
    route = respx.get(SEARCH_URL).mock(side_effect=_page_for)
    with httpx.Client() as client:
        result = run_adapter(_adapter(max_pages=3), client)
    assert route.call_count == 3
    assert result.status is SourceStatus.DEGRADED  # cut short is not "ok"
    assert result.error == "truncated: max_pages=3 reached"
    assert len(result.items) == 9


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"jobs": None},
        [],
        {"jobs": []},
        {"jobs": [], "offset": 0, "limit": 20, "totalCount": "25"},
        {"jobs": [], "offset": False, "limit": 20, "totalCount": 25},
        {"jobs": [], "offset": 0, "limit": 20, "totalCount": -1},
        {"jobs": [], "offset": 0, "limit": 0, "totalCount": 25},
        {"jobs": [], "offset": 40, "limit": 20, "totalCount": 25},  # not page 1's offset
    ],
)
@respx.mock
def test_unexpected_envelope_is_failed_not_empty(payload: object) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.FAILED
    assert result.error is not None and "unexpected response" in result.error


@respx.mock
def test_unexpected_envelope_on_later_page_is_degraded() -> None:
    respx.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=_page("page1.json")), httpx.Response(200, json={})]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.DEGRADED
    assert result.error is not None and result.error.startswith("page 2: ValueError: unexpected")


@pytest.mark.parametrize("value", [None, "Developer", [1], "missing"])
def test_parent_categories_not_a_list_of_names_is_invalid_not_out_of_scope(value: object) -> None:
    record = _page("page1.json")["jobs"][0]
    if value == "missing":
        del record["parentCategories"]
    else:
        record["parentCategories"] = value
    with pytest.raises(ValueError, match="parentCategories"):
        _adapter().parse_record(record)


def test_empty_parent_categories_is_out_of_scope() -> None:
    record = _page("page1.json")["jobs"][0]
    record["parentCategories"] = []
    assert _adapter().parse_record(record) is None


@respx.mock
def test_parent_categories_dropped_from_the_format_fails_the_source() -> None:
    page = _page("page2.json")
    for job in page["jobs"]:
        del job["parentCategories"]
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=page))
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert (result.status, result.out_of_scope) == (SourceStatus.FAILED, 0)


@respx.mock
def test_pages_are_spaced_out_but_the_first_is_not_delayed() -> None:
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=_page("page1.json")),
            httpx.Response(200, json=_page("page2.json")),
        ]
    )
    pauses: list[float] = []
    with httpx.Client() as client:
        run_adapter(_adapter(page_delay_s=0.5, sleep=pauses.append), client)
    assert pauses == [0.5]


@respx.mock
def test_last_page_by_total_count_ends_the_walk() -> None:
    page = _page("page1.json")
    page["totalCount"] = 20  # offset 0 + limit 20 covers everything
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=page))
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert route.call_count == 1 and result.status is SourceStatus.OK


@respx.mock
@pytest.mark.parametrize("total", [100, 25])  # mid-feed, and the last page with 5 records left
def test_empty_page_before_the_end_is_degraded_not_a_natural_end(total: int) -> None:
    empty = {"jobs": [], "offset": 20, "limit": 20, "totalCount": total}
    respx.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=_page("page1.json")), httpx.Response(200, json=empty)]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.DEGRADED
    assert result.error == "page 2: ValueError: unexpected response: empty page before totalCount"


@respx.mock
@pytest.mark.parametrize("total", [100, 5])
def test_empty_first_page_before_the_end_is_failed_not_empty(total: int) -> None:
    empty = {"jobs": [], "offset": 0, "limit": 20, "totalCount": total}
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=empty))
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.FAILED


@respx.mock
def test_empty_feed_is_empty() -> None:
    empty = {"jobs": [], "offset": 0, "limit": 20, "totalCount": 0}
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=empty))
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.EMPTY


@respx.mock
def test_empty_page_past_a_shrunk_end_is_a_natural_end() -> None:
    gone = {"jobs": [], "offset": 20, "limit": 20, "totalCount": 18}  # feed shrank mid-walk
    respx.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=_page("page1.json")), httpx.Response(200, json=gone)]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.OK


@respx.mock
def test_later_page_with_a_wrong_offset_is_degraded() -> None:
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=_page("page1.json")),
            httpx.Response(200, json=_page("page1.json")),  # offset 0 again on page 2
        ]
    )
    with httpx.Client() as client:
        result = run_adapter(_adapter(), client)
    assert result.status is SourceStatus.DEGRADED
    assert result.error is not None and "do not match page 2" in result.error


@respx.mock
def test_undated_record_keeps_an_otherwise_old_page_from_ending_the_walk() -> None:
    old = _page("page1.json")
    for job in old["jobs"]:
        job["pubDate"] = 1789862400  # 2026-09-20, older than the 30-hour lookback
    old["jobs"][0]["pubDate"] = None  # could be a fresh job
    route = respx.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=old), httpx.Response(200, json=_page("page2.json"))]
    )
    with httpx.Client() as client:
        run_adapter(_adapter(), client)
    assert route.call_count == 2
