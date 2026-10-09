import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from aijobradar.models import RawJob
from aijobradar.sources.base import Fetched, describe_error
from aijobradar.sources.common import parse_salary_period

SEARCH_URL = "https://himalayas.app/jobs/api/search"
# 26 whole-hour offsets from UTC-11 to UTC+14: a job allowing all of them has no tz restriction.
_ALL_OFFSETS = 26


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _count(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:  # bool is an int
        raise ValueError(f"unexpected response: '{key}' is not a non-negative integer")
    return value


def _parse_page(payload: Any, page_no: int) -> tuple[list[Any], bool]:
    """Return the page's jobs and whether it is the last page; reject an unexpected envelope."""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("unexpected response: no 'jobs' list")
    offset, limit, total = (_count(payload, key) for key in ("offset", "limit", "totalCount"))
    # The end is computed from these counters, so they must describe the page we asked for.
    if limit < 1 or offset != (page_no - 1) * limit:
        raise ValueError(f"unexpected response: offset/limit do not match page {page_no}")
    # An empty page is the end only past the last record; inside the range it hides jobs.
    if not payload["jobs"] and offset < total:
        raise ValueError("unexpected response: empty page before totalCount")
    return payload["jobs"], offset + limit >= total


@dataclass
class HimalayasAdapter:
    """All categories, newest first (`sort=recent`); the category filter is ours, in parse_record.

    The search endpoint has no category parameter, and keyword queries miss part of the
    Developer category, so the adapter walks the whole recent feed back to the lookback cutoff.
    """

    max_pages: int = 500
    lookback_hours: int = 30
    page_delay_s: float = 0.5
    parent_categories: tuple[str, ...] = ("Developer",)
    now: Callable[[], datetime] = field(default=_utcnow)
    sleep: Callable[[float], None] = field(default=time.sleep)
    name: str = "himalayas"

    def fetch(self, client: httpx.Client) -> Fetched:
        cutoff = (self.now() - timedelta(hours=self.lookback_hours)).timestamp()
        records: list[Any] = []
        for page_no in range(1, self.max_pages + 1):
            if page_no > 1:
                self.sleep(self.page_delay_s)  # one daily sync of a few hundred pages, spaced out
            try:
                response = client.get(SEARCH_URL, params={"sort": "recent", "page": page_no})
                response.raise_for_status()
                page, last = _parse_page(response.json(), page_no)
            except Exception as exc:
                if not records:
                    raise
                message, code = describe_error(exc)
                return Fetched(records, error=f"page {page_no}: {message}", http_status=code)
            records.extend(page)
            # Newest first except a few pinned old posts on top, hence the page maximum. An undated
            # record could be fresh, so only a fully dated page may end the walk by date.
            dates = [job.get("pubDate") if isinstance(job, dict) else None for job in page]
            dated = [d for d in dates if isinstance(d, int | float) and not isinstance(d, bool)]
            if last or (dated and len(dated) == len(dates) and max(dated) < cutoff):
                return Fetched(records)
        # Pages within the lookback remain: the list is cut short, which must not read as "ok".
        return Fetched(records, error=f"truncated: max_pages={self.max_pages} reached")

    def parse_record(self, record: dict[str, Any]) -> RawJob | None:
        categories = record.get("parentCategories")
        # A format change must not pass as out of scope: only a list of names is understood.
        if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
            raise ValueError("record lacks a parentCategories list")
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
            timezone_restrictions=offsets
            if 0 < len(set(map(int, offsets))) < _ALL_OFFSETS
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
