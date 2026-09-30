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
    respx.get(FULL).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "fullstack.rss").read_bytes())
    )
    respx.get(BACK).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "backend.rss").read_bytes())
    )


@respx.mock
def test_feeds_are_merged_and_deduplicated_by_guid() -> None:
    _mock_feeds()
    adapter = WwrAdapter(
        feeds=("remote-full-stack-programming-jobs", "remote-back-end-programming-jobs")
    )
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
        (
            "🇧🇦 Bosnia and Herzegovina, 🇸🇰 Slovakia, and 🇺🇦 Ukraine",
            ["Bosnia and Herzegovina", "Slovakia", "Ukraine"],
        ),
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
        return_value=httpx.Response(403)
    )
    with httpx.Client() as client:
        result = run_adapter(WwrAdapter(), client)
    assert result.status is SourceStatus.DEGRADED
    assert result.error == "remote-programming-jobs: HTTP 403"
    assert len(result.items) == 3
