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
    respx.get(API_URL).mock(
        return_value=httpx.Response(200, json={"success": False, "statusCode": 400})
    )
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
