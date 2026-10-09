from datetime import timedelta

import pytest

from aijobradar.rules.basic import non_engineering_role, not_remote, stale
from tests.rules_support import NOW, STRONG_DESCRIPTIONS, facts_from_job, make_ctx, make_facts

CTX = make_ctx()


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"title": "Backend Engineer (Hybrid)"}, True),
        ({"title": "Platform Engineer - Onsite"}, True),
        ({"title": "Hybrid Cloud Engineer"}, False),
        ({"location_text": "Hybrid - Berlin"}, True),
        ({"location_text": "Remote, Europe"}, False),
        ({"location_text": "On-site, Lisbon"}, True),
        ({"title": "Backend Engineer [In-Office]"}, True),
    ],
)
def test_not_remote(fields: dict[str, str], hit: bool) -> None:
    assert (not_remote(make_facts(**fields), CTX) is not None) is hit


@pytest.mark.parametrize(
    ("title", "hit"),
    [
        ("Senior Account Executive", True),
        ("Product Designer", True),
        ("HR Generalist", True),
        ("Sales Engineer", False),  # engineering term wins
        ("Customer Support Engineer", False),
        ("Backend Developer", False),
        ("Data Analyst", False),  # engineering terms (data, analyst)
    ],
)
def test_non_engineering_role(title: str, hit: bool) -> None:
    assert (non_engineering_role(make_facts(title=title), CTX) is not None) is hit


def test_stale() -> None:
    assert stale(make_facts(posted_at=NOW - timedelta(days=31)), CTX) is not None
    assert stale(make_facts(posted_at=NOW - timedelta(days=29)), CTX) is None
    assert stale(make_facts(posted_at=None), CTX) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"location_text": "Remote / Hybrid"},
        {"location_text": "Remote (no onsite required)"},
        {"title": "Backend Engineer - Remote | Hybrid"},
    ],
)
def test_not_remote_must_not_reject(fields: dict[str, str]) -> None:
    assert not_remote(make_facts(**fields), CTX) is None


@pytest.mark.parametrize("description", STRONG_DESCRIPTIONS)
def test_not_remote_ignores_description(description: str) -> None:
    assert not_remote(facts_from_job(description), CTX) is None


@pytest.mark.parametrize(
    "title",
    [
        "Full Stack Lead, Marketing Platform",
        "Senior Backend (Sales Platform)",
        "Frontend Lead - Customer Support Tools",
        "Data Scientist, Marketing",
        "Database Designer",
    ],
)
def test_non_engineering_role_must_not_reject(title: str) -> None:
    assert non_engineering_role(make_facts(title=title), CTX) is None


@pytest.mark.parametrize("title", ["Web Designer", "Marketing Manager"])
def test_non_engineering_role_still_rejects(title: str) -> None:
    assert non_engineering_role(make_facts(title=title), CTX) is not None


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"location_text": "Optional hybrid — Berlin"}, False),
        ({"location_text": "Hybrid optional"}, False),
        ({"location_text": "Hybrid - Berlin"}, True),
        ({"title": "Backend Engineer (Hybrid)"}, True),
    ],
)
def test_not_remote_optional_hybrid_is_not_rejected(fields: dict[str, str], hit: bool) -> None:
    assert (not_remote(make_facts(**fields), CTX) is not None) is hit
