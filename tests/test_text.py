from datetime import datetime

import pytest
from pydantic import ValidationError

from aijobradar.models import RawJob
from aijobradar.text import (
    canonical_url,
    content_hash,
    html_to_text,
    normalize_company,
    normalize_title,
)


def test_html_to_text_strips_tags_scripts_and_nbsp() -> None:
    html = "<h3>About</h3><p>We build&nbsp;APIs.</p><ul><li>Node</li><li>Postgres</li></ul>"
    html += "<script>track()</script>"
    assert html_to_text(html) == "About\nWe build APIs.\nNode\nPostgres"


def test_html_to_text_keeps_single_blank_line_and_decodes_entities() -> None:
    assert html_to_text("<p>Invoices &amp; contracts.</p>\n\n\n<p>Second</p>") == (
        "Invoices & contracts.\n\nSecond"
    )


def test_html_to_text_empty() -> None:
    assert html_to_text("") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Initrode LLC", "initrode"),
        ("Vandelay Industries ", "vandelay industries"),
        ("Acme Ledger, Inc.", "acme ledger"),
        ("ООО «Ромашка»", "ромашка"),
        ("Co", "co"),  # name made only of a legal suffix is kept, not emptied
    ],
)
def test_normalize_company(raw: str, expected: str) -> None:
    assert normalize_company(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Senior Full-Stack Engineer (React/Node)", "senior fullstack engineer react node"),
        ("Senior Full Stack Engineer", "senior fullstack engineer"),
        ("Back-end Developer - Remote, US", "backend developer us"),
        ("AI Augmented Software Engineer [gn]", "ai augmented software engineer"),
        ("C++ / C# Engineer", "c++ c# engineer"),
        ("Backend Engineer (m/w/d)", "backend engineer"),
        (
            "Senior Backend Developer (Node.js / Nest.js)",
            "senior backend developer node js nest js",
        ),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_canonical_url_drops_tracking_and_normalizes() -> None:
    url = "https://remoteOK.com/remote-jobs/x-1137434/?utm_source=a&ref=b&u=aff&page=2#top"
    assert canonical_url(url) == "https://remoteok.com/remote-jobs/x-1137434?page=2"


def test_canonical_url_keeps_meaningful_params_sorted() -> None:
    url = "https://boards.greenhouse.io/acme?gh_jid=42&b=1"
    assert canonical_url(url) == "https://boards.greenhouse.io/acme?b=1&gh_jid=42"


def test_content_hash_is_stable_and_separator_safe() -> None:
    assert content_hash("a", "bc") == content_hash("a", "bc")
    assert content_hash("a", "bc") != content_hash("ab", "c")
    assert len(content_hash("x")) == 64


def test_raw_job_strips_and_rejects_empty_title() -> None:
    job = RawJob(source="s", source_job_id="1", source_url="https://x", title=" T ", company=" C ")
    assert (job.title, job.company) == ("T", "C")
    with pytest.raises(ValidationError):
        RawJob(source="s", source_job_id="1", source_url="https://x", title="  ", company="C")


def test_raw_job_strips_nul_from_every_string_field() -> None:
    job = RawJob(
        source="s",
        source_job_id="1\x002",
        source_url="https://x/\x00",
        title="Dev\x00 Ops",
        company="Ac\x00me",
        description_html="<p>a\x00b</p>",
        tags=["py\x00thon", "go"],
        location_restrictions=["Uni\x00ted States"],
    )
    assert (job.source_job_id, job.title, job.company) == ("12", "Dev Ops", "Acme")
    assert (job.source_url, job.description_html) == ("https://x/", "<p>ab</p>")
    assert (job.tags, job.location_restrictions) == (["python", "go"], ["United States"])


def test_raw_job_nulls_over_long_currency_instead_of_failing() -> None:
    base = {"source": "s", "source_job_id": "1", "source_url": "https://x", "title": "T"}
    long = RawJob(**base, company="C", salary_currency="US DOLLARS")
    assert long.salary_currency is None
    assert RawJob(**base, company="C", salary_currency="USD").salary_currency == "USD"


def test_raw_job_rejects_naive_posted_at() -> None:
    with pytest.raises(ValidationError):
        RawJob(
            source="s",
            source_job_id="1",
            source_url="https://x",
            title="T",
            company="C",
            posted_at=datetime(2026, 9, 1),
        )
