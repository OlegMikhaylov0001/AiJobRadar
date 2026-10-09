from datetime import timedelta

import pytest

from aijobradar.rules.basic import non_engineering_role, not_remote, stale
from tests.rules_support import NOW, make_ctx, make_facts

CTX = make_ctx()


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"title": "Backend Engineer (Hybrid)"}, True),
        ({"title": "Platform Engineer - Onsite"}, True),
        ({"title": "Hybrid Cloud Engineer"}, False),
        ({"location_text": "Hybrid - Berlin"}, True),
        ({"location_text": "Remote, Europe"}, False),
        ({"description": "This is a hybrid role based in Lisbon."}, True),
        ({"description": "You will spend 3 days a week in the office."}, True),
        ({"description": "We have an office in Berlin you can visit."}, False),
        ({"description": "Remote-first, optional office."}, False),
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
        {"description": "This is a remote or hybrid role, your choice."},
        {"description": "Fully remote. Optional hybrid schedule if you live near Berlin."},
        {
            "description": "Remote-first. If you are near Lisbon you can join us 2 days a week "
            "in the office."
        },
    ],
)
def test_not_remote_must_not_reject(fields: dict[str, str]) -> None:
    assert not_remote(make_facts(**fields), CTX) is None


def test_not_remote_rejects_plain_on_site_role() -> None:
    assert not_remote(make_facts(description="This is an on-site role."), CTX) is not None


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
