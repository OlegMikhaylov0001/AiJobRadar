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
    return RawJob(
        source="fake",
        source_job_id=str(i),
        source_url=f"https://x/{i}",
        title="Engineer",
        company="Acme",
    )


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
    exc = httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(503, request=request)
    )
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
    reqs = [
        FeedRequest("one", "https://a/1"),
        FeedRequest("two", "https://a/2"),
        FeedRequest("three", "https://a/3"),
    ]
    with httpx.Client() as client:
        fetched = fetch_feeds(
            client, reqs, extract=lambda r: r.json(), key=lambda rec: str(rec["id"])
        )
    assert [r["id"] for r in fetched.records] == [1, 2, 3]
    assert (fetched.error, fetched.http_status) == ("three: HTTP 500", 500)


@respx.mock
def test_fetch_feeds_raises_when_all_fail() -> None:
    respx.get("https://a/1").mock(return_value=httpx.Response(403))
    with httpx.Client() as client, pytest.raises(FeedsFailed) as info:
        fetch_feeds(
            client,
            [FeedRequest("one", "https://a/1")],
            extract=lambda r: r.json(),
            key=lambda rec: str(rec),
        )
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
