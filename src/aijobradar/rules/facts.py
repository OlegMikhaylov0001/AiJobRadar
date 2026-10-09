import uuid
from dataclasses import dataclass
from datetime import datetime

from aijobradar.db.models import Job


@dataclass(frozen=True)
class JobFacts:
    """Everything a rule may look at, detached from the ORM session. No description: free text
    is left to the LLM stage."""

    job_id: uuid.UUID
    company: str
    title: str
    title_norm: str
    location_text: str
    location_restrictions: tuple[str, ...]
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    posted_at: datetime | None

    @classmethod
    def from_job(cls, job: Job) -> "JobFacts":
        return cls(
            job_id=job.id,
            company=job.company_raw,
            title=job.title_raw,
            title_norm=job.title_norm,
            location_text=job.location_text or "",
            location_restrictions=tuple(job.location_restrictions or ()),
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            posted_at=job.posted_at,
        )
