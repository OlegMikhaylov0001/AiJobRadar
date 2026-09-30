from aijobradar.models import RawJob
from aijobradar.normalize import normalize


def _raw(**overrides: object) -> RawJob:
    data: dict[str, object] = {
        "source": "jobicy",
        "source_job_id": "1",
        "source_url": "https://jobicy.com/jobs/1-x/?utm_source=feed",
        "title": "Senior Full-Stack Engineer (React/Node)",
        "company": "Umbrella Billing Ltd",
        "description_html": "<p>Invoices &amp; contracts.</p>",
    }
    data.update(overrides)
    return RawJob.model_validate(data)


def test_normalize_fills_derived_fields() -> None:
    job = normalize(_raw())
    assert job.company_norm == "umbrella billing"
    assert job.title_norm == "senior fullstack engineer"
    assert job.description_text == "Invoices & contracts."
    assert job.apply_url_canonical == "https://jobicy.com/jobs/1-x"
    assert len(job.content_hash) == 64
    assert job.raw.company == "Umbrella Billing Ltd"


def test_content_hash_changes_with_description_only() -> None:
    a = normalize(_raw())
    b = normalize(_raw(description_html="<p>Different text.</p>"))
    c = normalize(_raw(source="himalayas", source_job_id="zzz"))
    assert a.content_hash != b.content_hash
    assert a.content_hash == c.content_hash  # same posting from another source hashes equal
