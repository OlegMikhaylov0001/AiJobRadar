from pydantic import BaseModel

from aijobradar.models import RawJob
from aijobradar.text import (
    canonical_url,
    content_hash,
    html_to_text,
    normalize_company,
    normalize_title,
)


class NormalizedJob(BaseModel):
    raw: RawJob
    company_norm: str
    title_norm: str
    description_text: str
    apply_url_canonical: str
    content_hash: str  # identity of the posting's content, independent of the source


def normalize(raw: RawJob) -> NormalizedJob:
    company = normalize_company(raw.company)
    title = normalize_title(raw.title)
    text = html_to_text(raw.description_html)
    return NormalizedJob(
        raw=raw,
        company_norm=company,
        title_norm=title,
        description_text=text,
        apply_url_canonical=canonical_url(raw.source_url),
        content_hash=content_hash(company, title, text),
    )
