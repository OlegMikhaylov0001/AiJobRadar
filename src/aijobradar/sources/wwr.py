import re
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as SafeET

from aijobradar.models import RawJob
from aijobradar.sources.base import FeedRequest, Fetched, fetch_feeds
from aijobradar.sources.common import parse_rfc822_utc

FEED_URL = "https://weworkremotely.com/categories/{slug}.rss"
_FLAG = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")  # regional-indicator pair = one flag
_TRAILING_SEPARATOR = re.compile(r"(?:,\s*and|,|\s+and)\s*$")
_SKILL_SEPARATOR = re.compile(r",\s*(?:and\s+)?|\s+and\s+")


def split_countries(value: str) -> list[str]:
    """Split WWR's flag-prefixed list. Split on flags, not on 'and' (Bosnia and Herzegovina)."""
    names = []
    for chunk in _FLAG.split(value):
        name = _TRAILING_SEPARATOR.sub("", chunk.strip()).strip()
        if name:
            names.append(name)
    return names


def _text(item: Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def _extract(response: httpx.Response) -> list[Any]:
    root = SafeET.fromstring(response.content)
    return list(root.iter("item"))


@dataclass
class WwrAdapter:
    feeds: tuple[str, ...] = (
        "remote-full-stack-programming-jobs",
        "remote-back-end-programming-jobs",
        "remote-programming-jobs",
    )
    name: str = "wwr"

    def fetch(self, client: httpx.Client) -> Fetched:
        requests = [FeedRequest(slug, FEED_URL.format(slug=slug)) for slug in self.feeds]
        return fetch_feeds(
            client,
            requests,
            extract=_extract,
            key=lambda item: _text(item, "guid") or _text(item, "link"),
        )

    def parse_record(self, item: Element) -> RawJob | None:
        company, separator, title = _text(item, "title").partition(": ")
        if not separator:
            raise ValueError("title lacks the 'Company: Role' separator")
        link = _text(item, "link") or _text(item, "guid")
        region = _text(item, "region")
        countries = split_countries(_text(item, "country"))
        location = ", ".join(part for part in (region, ", ".join(countries)) if part)
        skills = [s.strip() for s in _SKILL_SEPARATOR.split(_text(item, "skills")) if s.strip()]
        return RawJob(
            source=self.name,
            source_job_id=_text(item, "guid") or link,
            source_url=link,
            title=title,
            company=company,
            location_text=location or None,
            location_restrictions=countries,
            employment_type=_text(item, "type") or None,
            posted_at=parse_rfc822_utc(_text(item, "pubDate")),
            description_html=_text(item, "description"),
            tags=skills,
        )
