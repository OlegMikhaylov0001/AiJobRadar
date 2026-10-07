from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from aijobradar.models import SalaryPeriod

_PERIODS = {
    "hourly": SalaryPeriod.HOUR,
    "hour": SalaryPeriod.HOUR,
    "daily": SalaryPeriod.DAY,
    "day": SalaryPeriod.DAY,
    "weekly": SalaryPeriod.WEEK,
    "week": SalaryPeriod.WEEK,
    "monthly": SalaryPeriod.MONTH,
    "month": SalaryPeriod.MONTH,
    "annual": SalaryPeriod.YEAR,
    "yearly": SalaryPeriod.YEAR,
    "year": SalaryPeriod.YEAR,
}


def make_client(user_agent: str, timeout_s: float) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent}, timeout=timeout_s, follow_redirects=True
    )


def _as_utc(value: datetime) -> datetime:
    # Sources that omit the offset (Remotive-style) are treated as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_iso_utc(value: str | None) -> datetime | None:
    return _as_utc(datetime.fromisoformat(value)) if value else None


def parse_rfc822_utc(value: str | None) -> datetime | None:
    return _as_utc(parsedate_to_datetime(value)) if value else None


def parse_salary_period(value: str | None) -> SalaryPeriod | None:
    # Unknown periods (e.g. "fortnightly") yield None: salary stays, but rate rules skip it.
    return _PERIODS.get((value or "").strip().lower())
