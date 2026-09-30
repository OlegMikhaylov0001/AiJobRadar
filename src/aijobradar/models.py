from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

MAX_CURRENCY_LEN = 8  # width of jobs.salary_currency


def _strip_nul(value: Any) -> Any:
    """Postgres text cannot hold NUL; drop it from strings and from lists of strings."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    return value


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
    posted_at: AwareDatetime | None = None
    description_html: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("*", mode="before")
    @classmethod
    def _drop_nul(cls, value: Any) -> Any:
        return _strip_nul(value)

    @field_validator("salary_currency")
    @classmethod
    def _currency_fits_column(cls, value: str | None) -> str | None:
        # An over-long code is not worth losing the job over: keep the job, drop the code.
        return value if value is None or len(value) <= MAX_CURRENCY_LEN else None


class SourceResult(BaseModel):
    source: str
    status: SourceStatus
    items: list[RawJob] = Field(default_factory=list)
    error: str | None = None
    http_status: int | None = None
    duration_ms: int = 0
    invalid_items: int = 0  # records that failed parsing/validation
    out_of_scope: int = 0  # records dropped by the source's own scope filter (e.g. category)
    ingest_errors: int = 0  # valid records the database rejected; set by the pipeline, not adapters
