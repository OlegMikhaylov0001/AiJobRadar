import pytest

from aijobradar.rules.geo import geo_country_only, geo_residency
from tests.rules_support import STRONG_DESCRIPTIONS, facts_from_job, make_ctx, make_facts

CTX = make_ctx()  # eligible: PT, EU, EUROPE, EMEA, WORLDWIDE


@pytest.mark.parametrize(
    ("restrictions", "hit"),
    [
        (["United States"], True),
        (["Netherlands", "United Kingdom"], True),
        (["Canada", "United States", "United States of America"], True),
        (["Portugal"], False),
        (["Europe"], False),
        (["Remote"], False),  # unknown token = ambiguous
        (["United States", "Narnia"], False),  # any unknown token = ambiguous
        ([], False),
    ],
)
def test_structured_restrictions(restrictions: list[str], hit: bool) -> None:
    facts = make_facts(location_restrictions=restrictions)
    assert (geo_country_only(facts, CTX) is not None) is hit


# location_text is scanned only alongside restrictions; these mirror what Jobicy derives
# (jobGeo split on commas), so an unknown token keeps the structural check ambiguous.
@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"location_text": "Remote - US", "location_restrictions": ["Remote - US"]}, True),
        ({"title": "Forward Deployed Engineer - Remote, US"}, True),
        ({"title": "Platform Engineer (US only)"}, True),
        ({"title": "Backend Engineer (must be based in Canada)"}, True),
        ({"location_text": "Remote - Europe", "location_restrictions": ["Remote - Europe"]}, False),
        ({"title": "Backend Engineer (EMEA only)"}, False),
        ({"title": "Contact Us Only Engineer"}, False),  # "Us" is not the case-sensitive "US"
    ],
)
def test_head_country_only(fields: dict[str, object], hit: bool) -> None:
    assert (geo_country_only(make_facts(**fields), CTX) is not None) is hit


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"title": "Backend Engineer (US Citizens Only)"}, True),
        ({"title": "Software Engineer - TS/SCI Clearance Required"}, True),
        ({"title": "Backend Engineer - right to work in the UK"}, True),
        (
            {
                "location_text": "Remote - US citizens only",
                "location_restrictions": ["Remote - US citizens only"],
            },
            True,
        ),
        ({"title": "Backend Engineer (EU citizens only)"}, False),  # EU is eligible here
        ({"title": "Security Clearance Platform Engineer"}, False),
    ],
)
def test_head_residency(fields: dict[str, object], hit: bool) -> None:
    assert (geo_residency(make_facts(**fields), CTX) is not None) is hit


def test_eligibility_comes_from_profile() -> None:
    us_ctx = make_ctx(eligible_places=["US", "WORLDWIDE"])
    facts = make_facts(
        location_restrictions=["United States"], title="Backend Engineer (US citizens only)"
    )
    assert geo_country_only(facts, us_ctx) is None
    assert geo_residency(facts, us_ctx) is None


@pytest.mark.parametrize(
    "fields",
    [
        {
            "location_text": "Remote - US, Europe",
            "location_restrictions": ["Remote - US", "Europe"],
        },
        {"location_text": "Remote - US or EU", "location_restrictions": ["Remote - US or EU"]},
        {"location_text": "Remote - USA/Europe", "location_restrictions": ["Remote - USA/Europe"]},
        {
            "location_text": "Remote (US, Canada, Europe)",
            "location_restrictions": ["Remote (US", "Canada", "Europe)"],
        },
        {"title": "Backend Engineer - Remote, US or Europe"},
        {"title": "Backend Engineer (not US only)"},
    ],
)
def test_country_only_hedged_phrases_are_not_rejected(fields: dict[str, object]) -> None:
    assert geo_country_only(make_facts(**fields), CTX) is None


@pytest.mark.parametrize(
    "title",
    [
        "Backend Engineer - right to work in the UK or the EU",
        "Software Engineer (TS/SCI clearance preferred)",
        "Backend Engineer - no US citizenship required",
    ],
)
def test_residency_hedged_phrases_are_not_rejected(title: str) -> None:
    assert geo_residency(make_facts(title=title), CTX) is None


def test_multiple_foreign_places_still_reject() -> None:
    facts = make_facts(
        location_text="Remote - US, Canada", location_restrictions=["Remote - US", "Canada"]
    )
    assert geo_country_only(facts, CTX) is not None


def test_location_text_alone_is_not_scanned_without_restrictions() -> None:
    # location_text is derived from the structured fields: with no restrictions it must not
    # re-introduce a narrowing that the merged geo already widened away.
    facts = make_facts(location_text="USA Only", location_restrictions=())
    assert geo_country_only(facts, CTX) is None
    facts = make_facts(location_text="US citizens only", location_restrictions=())
    assert geo_residency(facts, CTX) is None


def test_location_text_with_restrictions_still_rejects() -> None:
    facts = make_facts(location_text="USA Only", location_restrictions=("United States",))
    assert geo_country_only(facts, CTX) is not None


@pytest.mark.parametrize("description", STRONG_DESCRIPTIONS)
def test_geo_country_only_ignores_description(description: str) -> None:
    assert geo_country_only(facts_from_job(description), CTX) is None


@pytest.mark.parametrize("description", STRONG_DESCRIPTIONS)
def test_geo_residency_ignores_description(description: str) -> None:
    assert geo_residency(facts_from_job(description), CTX) is None


@pytest.mark.parametrize(
    ("title", "hit"),
    [
        ("Backend Engineer - Remote, US hours", False),
        ("Backend Engineer - Remote, US time zones", False),
        ("Backend Engineer - Remote (US timezone overlap)", False),
        ("Backend Engineer - Remote, US", True),
        ("Backend Engineer - Remote, US, Canada", True),
    ],
)
def test_country_only_time_zone_wording_is_not_a_location_restriction(
    title: str, hit: bool
) -> None:
    assert (geo_country_only(make_facts(title=title), CTX) is not None) is hit
