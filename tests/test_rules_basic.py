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
        ("Data Analyst", False),  # neither list: not a clear non-engineering role
    ],
)
def test_non_engineering_role(title: str, hit: bool) -> None:
    assert (non_engineering_role(make_facts(title=title), CTX) is not None) is hit


def test_stale() -> None:
    assert stale(make_facts(posted_at=NOW - timedelta(days=31)), CTX) is not None
    assert stale(make_facts(posted_at=NOW - timedelta(days=29)), CTX) is None
    assert stale(make_facts(posted_at=None), CTX) is None
