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


def _parse_page(payload: Any) -> tuple[list[Any], str | None]:
    """Reject an unexpected envelope: a silent [] would pass for an empty source."""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("unexpected response: no 'jobs' list")
    if "nextCursor" not in payload:  # a missing key would end the walk as if complete
        raise ValueError("unexpected response: no 'nextCursor'")
    cursor = payload["nextCursor"]
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError("unexpected response: 'nextCursor' is not a string")
    return payload["jobs"], cursor


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
                page, cursor = _parse_page(response.json())
            except Exception as exc:
                if not records:
                    raise
                message, code = describe_error(exc)
                return Fetched(records, error=f"page {page_no}: {message}", http_status=code)
            records.extend(page)
            # Newest first except pinned posts, hence the page maximum, not the last item.
            newest = max((job.get("pubDate") or 0 for job in page), default=0)
            if not cursor or not page or newest < cutoff:
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
