import pytest

from aijobradar.rules.geo import geo_country_only, geo_residency
from tests.rules_support import make_ctx, make_facts

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


@pytest.mark.parametrize(
    ("fields", "hit"),
    [
        ({"description": "This role is US only."}, True),
        ({"description": "Please contact us only via email."}, False),
        ({"location_text": "Remote - US"}, True),
        ({"title": "Forward Deployed Engineer - Remote, US"}, True),
        ({"location_text": "Remote - Europe"}, False),
        ({"description": "You must be located in a timezone close to CET."}, False),
        ({"description": "Only open to candidates based in the United States."}, True),
        ({"description": "EMEA only"}, False),
        ({"description": "Must be based in Canada."}, True),
    ],
)
def test_text_country_only(fields: dict[str, str], hit: bool) -> None:
    assert (geo_country_only(make_facts(**fields), CTX) is not None) is hit


@pytest.mark.parametrize(
    ("description", "hit"),
    [
        ("US citizens only.", True),
        ("You must have the right to work in the UK.", True),
        ("Candidates must be authorized to work in the United States.", True),
        ("Work authorization in Canada is required.", True),
        ("You need to be eligible to work in the EU.", False),  # EU is eligible here
        ("EU timezone preferred.", False),
        ("An active security clearance is required.", True),
        ("No security clearance needed.", False),
    ],
)
def test_residency(description: str, hit: bool) -> None:
    assert (geo_residency(make_facts(description=description), CTX) is not None) is hit


def test_eligibility_comes_from_profile() -> None:
    us_ctx = make_ctx(eligible_places=["US", "WORLDWIDE"])
    facts = make_facts(location_restrictions=["United States"], description="US citizens only.")
    assert geo_country_only(facts, us_ctx) is None
    assert geo_residency(facts, us_ctx) is None
