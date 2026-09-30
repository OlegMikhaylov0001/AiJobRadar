from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    DEGRADED = "degraded"
    FAILED = "failed"


class SalaryPeriod(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class RawJob(BaseModel):
    """A job as one source reports it, before normalization. Datetimes are UTC-aware."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    source_job_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)  # page at the source; required for attribution
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    location_text: str | None = None
    location_restrictions: list[str] = Field(default_factory=list)  # [] = no stated restriction
    timezone_restrictions: list[float] | None = None
    employment_type: str | None = None
    seniority: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: SalaryPeriod | None = None
    posted_at: datetime | None = None
    description_html: str = ""
    tags: list[str] = Field(default_factory=list)


class SourceResult(BaseModel):
    source: str
    status: SourceStatus
    items: list[RawJob] = Field(default_factory=list)
    error: str | None = None
    http_status: int | None = None
    duration_ms: int = 0
    invalid_items: int = 0  # records that failed parsing/validation
    out_of_scope: int = 0  # records dropped by the source's own scope filter (e.g. category)
