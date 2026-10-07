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
            []
            if geo.casefold() in _NO_RESTRICTION
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
